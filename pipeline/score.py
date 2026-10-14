# -*- coding: utf-8 -*-
"""P3 评估层：按 rubric 计算评分卡。

设计要点（都直接对应 ROADMAP 里 P3 的 DoD）
- **rubric 驱动**：维度、权重、档位、雷区编号全部从 `rubric/*.md` 解析。
  评审结果里出现 rubric 之外的维度或编号 → 直接报错。这样"不得引入本文件
  之外的维度"就不再是写在 prompt 里的君子协定，而是脚本强制的契约。
- **无原话不扣分**：每条扣分必须附候选人原话片段，脚本校验它确实是答题文本
  的真子串（去空白归一化后），否则拒绝计分。
- **先定档再微调**：题分 = 档位 + clamp(维度均分 − 档位, ±0.5)，再落到 0.5 网格。
- **可复现**：聚合过程完全确定，两次运行的差异只来自评审 agent 的判档，
  用 `compare` 子命令量化偏差（验收 A1：加权总分偏差 ≤ 0.3）。

用法::

    python pipeline/score.py run --root . --judgments judgments --out out/scorecard.json
    python pipeline/score.py compare --a out/scorecard.json --b out/scorecard-b.json
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text, write_json, write_text, now  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BAND_NAMES = {5: "专家级", 4: "可录用", 3: "及格线", 2: "存疑", 1: "失败"}

# red_flags.md 附录的检测优先级：编排改进项与总评用
FLAG_PRIORITY = {"F": 0, "M": 1, "G": 2, "E": 3, "T": 4, "I": 5}
PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2}

# scoring.md 第 3 节的硬性扣分规则。评审结果用短键引用，脚本校验该短键
# 对应的原文确实存在于 rubric 中——规则本身仍然由 rubric 定义，不写死在代码里。
HARD_RULE_KEYS = {
    "编造": "编造不存在的事实",
    "常识错误": "技术常识错误",
    "矛盾": "与自己简历矛盾",
    "不确定": "主动承认不确定",
    "答不出": "答不出来",
}


# ---------------------------------------------------------------- rubric 解析

def parse_rubric(path: str) -> dict:
    """从 scoring.md 解析维度权重与通过概率档位。"""
    t = read_text(path)
    dims = {}
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(\d+)%\s*\|", t, re.M):
        name = m.group(1).strip().split()[0]
        if name in ("分", "维度"):
            continue
        dims[name] = int(m.group(2)) / 100.0
    if not dims:
        die(f"未能从 {path} 解析出任何评分维度")

    total = sum(dims.values())
    if abs(total - 1.0) > 0.005:
        die(f"{path} 维度权重合计 {total:.2f}，应为 1.00")

    tiers = []
    for line in t.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or "%" in cells[0]:
            continue
        cell, label = cells[0], cells[1]
        m = re.match(r"^(≥|>|<=|≤|<)\s*([\d.]+)$", cell)
        rng = re.match(r"^([\d.]+)\s*[–—-]\s*([\d.]+)$", cell)
        if m:
            op, v = m.group(1), float(m.group(2))
            tiers.append({"lo": v if op in ("≥", ">") else None,
                          "hi": v if op in ("≤", "<=") else None,
                          "op_lo": op in ("≥", ">"), "op_hi": op in ("≤", "<="),
                          "label": label})
        elif rng:
            tiers.append({"lo": float(rng.group(1)), "hi": float(rng.group(2)),
                          "op_lo": True, "op_hi": True, "label": label})
    if not tiers:
        die(f"未能从 {path} 解析出通过概率档位")

    rules = {}
    for key, anchor in HARD_RULE_KEYS.items():
        if anchor not in t:
            die(f"{path} 第 3 节里找不到硬性扣分规则「{anchor}」（规则改名后需同步 HARD_RULE_KEYS）")
        rules[key] = anchor
    return {"dims": dims, "tiers": tiers, "rules": rules,
            "sha1": hashlib.sha1(t.encode("utf-8")).hexdigest()[:12]}


def parse_flags(path: str) -> dict:
    """从 red_flags.md 解析雷区编号与规则文本。"""
    t = read_text(path)
    flags = {}
    for m in re.finditer(r"^\|\s*([A-Z]-\d{2})\s*\|\s*([^|]+?)\s*\|", t, re.M):
        flags[m.group(1)] = m.group(2).strip()
    if not flags:
        die(f"未能从 {path} 解析出任何雷区编号")
    return {"flags": flags, "sha1": hashlib.sha1(t.encode("utf-8")).hexdigest()[:12]}


def pass_probability(score: float, tiers: list) -> str:
    for t in tiers:
        if t["lo"] is not None and t["op_lo"] and score < t["lo"]:
            continue
        if t["lo"] is not None and not t["op_lo"] and score <= t["lo"]:
            continue
        if t["hi"] is not None and t["op_hi"] and score > t["hi"]:
            continue
        if t["hi"] is not None and not t["op_hi"] and score >= t["hi"]:
            continue
        return t["label"]
    return "未定（超出 rubric 档位表范围）"


# ------------------------------------------------------------------ 判分逻辑

def norm(s: str) -> str:
    """去空白归一化：引用原话时允许换行/空格差异，但不允许改字。"""
    return re.sub(r"\s+", "", s or "")


def answer_text(item: dict) -> str:
    """该题的全部回答文本（主答 + 所有追问）。"""
    parts = [item.get("answer", "")]
    parts += [f.get("answer", "") for f in item.get("followups", [])]
    return norm("".join(parts))


def score_round(transcript: dict, judg: dict, dims: dict, flags: dict,
                risk_ids: set, rules: dict = None) -> list:
    """校验并计算一轮的逐题得分。任何契约违规直接退出。"""
    rules = rules or {}
    rnd = transcript.get("round")
    by_qid = {i["qid"]: i for i in transcript.get("items", [])}
    rows = []
    for j in judg.get("items", []):
        qid = j.get("qid")
        if qid not in by_qid:
            die(f"第 {rnd} 轮评审结果引用了不存在的题号: {qid}")
        t = by_qid[qid]
        text = answer_text(t)

        band = j.get("band")
        if not isinstance(band, int) or not 1 <= band <= 5:
            die(f"{qid} 档位必须是 1–5 的整数（rubric 第 2 节：先定档再调分），实际 {band!r}")

        d = j.get("dims") or {}
        if set(d) != set(dims):
            die(f"{qid} 维度集合与 rubric 不一致\n  应为: {sorted(dims)}\n  实际: {sorted(d)}")
        for k, v in d.items():
            if not isinstance(v, (int, float)) or not 1 <= v <= 5 or round(v * 2) != v * 2:
                die(f"{qid} 维度「{k}」得分必须是 1–5 且以 0.5 为步长，实际 {v!r}")

        codes = []
        for dec in j.get("deductions", []):
            q = dec.get("quote", "")
            if not q.strip():
                die(f"{qid} 有一条扣分没有引用原话（rubric 第 5.3 条：无原话不扣分）")
            if norm(q) not in text:
                die(f"{qid} 扣分引用的原话在答题中找不到: 「{q}」")
            code = dec.get("red_flag")
            if code and code not in flags:
                die(f"{qid} 引用了 red_flags.md 之外的编号: {code}")
            rid = dec.get("risk_id")
            if rid and rid not in risk_ids:
                die(f"{qid} 引用了不存在的风险点编号: {rid}")
            if code:
                codes.append(code)

        # §3 硬性扣分规则（覆盖分档结果）
        mean = sum(d.values()) / len(d)
        score = band + max(-0.5, min(0.5, mean - band))
        note = ""
        hr = j.get("hard_rules") or []
        for k in hr:
            if k not in rules:
                die(f"{qid} 引用了 scoring.md 第 3 节之外的硬性规则: {k!r}（可用: {sorted(rules)}）")
        f_hit = [c for c in codes if c.startswith("F-")]
        m_hit = [c for c in codes if c.startswith("M-")]
        notes = []
        if f_hit or "编造" in hr:
            score = 1.0
            notes.append(f"编造（{'/'.join(f_hit) if f_hit else '评审判定'}）→ 直接 1 分")
        elif m_hit or "常识错误" in hr:
            score = min(2.0, score)
            notes.append(f"技术常识错误（{'/'.join(m_hit) if m_hit else '评审判定'}）→ 最高 2 分")
        if "矛盾" in hr and "编造" not in hr:
            score = max(1.0, score - 1.0)
            notes.append("与自己简历矛盾 → 扣 1 分")
        if "答不出" in hr and "编造" not in hr:
            score = min(score, 2.0)
            notes.append("答不出来 → 记 2 分")
        if "不确定" in hr or ("🟡 示弱" in (t.get("markers") or [])
                             and len(t.get("answer", "")) >= 40):
            if "编造" not in hr and not f_hit:
                score = min(5.0, score + 0.5)
                notes.append("主动承认不确定并给出推理路径 → +0.5")
        note = "；".join(notes)

        score = max(1.0, min(5.0, score))
        score = round(score * 2) / 2  # 落到 0.5 网格，同时收窄两次运行的偏差

        rows.append({
            "round": rnd,
            "qid": qid,
            "dimension": t.get("dimension", ""),
            "question": t.get("question", ""),
            "score": score,
            "band": band,
            "band_name": BAND_NAMES[band],
            "dims": d,
            "dims_mean": round(mean, 2),
            "rule_note": note,
            "deductions": j.get("deductions", []),
            "hard_rules": hr,
            "red_flags": codes,
            "risk_ids": sorted({dec.get("risk_id") for dec in j.get("deductions", [])
                                if dec.get("risk_id")}),
            "followup_layers": len(t.get("followups", [])),
            "followup_collapse": j.get("followup_collapse", ""),
            "improvement": j.get("improvement", ""),
            "markers": t.get("markers", []),
        })
    return rows


# --------------------------------------------------------------------- 聚合

def aggregate(rows: list, dims: dict, tiers: list, flags: dict,
              rounds_present: list) -> dict:
    dim_avg = {k: sum(r["dims"][k] for r in rows) / len(rows) for k in dims}
    weighted = sum(dim_avg[k] * w for k, w in dims.items())

    per_round = {}
    for rnd in rounds_present:
        rs = [r for r in rows if r["round"] == rnd]
        if rs:
            per_round[str(rnd)] = {
                "score": round(sum(r["score"] for r in rs) / len(rs), 2),
                "questions": len(rs),
                "bands": {BAND_NAMES[b]: sum(1 for r in rs if r["band"] == b)
                          for b in (5, 4, 3, 2, 1)},
            }

    hit = {}
    for r in rows:
        for c in r["red_flags"]:
            hit.setdefault(c, {"rule": flags[c], "count": 0, "evidence": []})
            hit[c]["count"] += 1
            for dec in r["deductions"]:
                if dec.get("red_flag") == c:
                    hit[c]["evidence"].append({"qid": r["qid"], "quote": dec.get("quote", ""),
                                               "reason": dec.get("reason", "")})

    fabrications = [{"round": r["round"], "qid": r["qid"], "quote": dec.get("quote", ""),
                     "reason": dec.get("reason", ""), "code": dec.get("red_flag")}
                    for r in rows for dec in r["deductions"]
                    if (dec.get("red_flag") or "").startswith("F-")]

    def _flag_rank(r):
        return min((FLAG_PRIORITY.get(c[0], 9) for c in r["red_flags"]), default=9)

    improvements = [{"round": r["round"], "qid": r["qid"], "priority": prio_rank(r),
                     "action": r["improvement"], "risk_ids": r["risk_ids"],
                     "score": r["score"], "flag_rank": _flag_rank(r)}
                    for r in rows if r["improvement"]]
    improvements.sort(key=lambda i: (PRIO_ORDER.get(i["priority"], 9),
                                     i["flag_rank"], i["score"]))

    return {
        "weighted_total": round(weighted, 1),
        "dimension_scores": {k: round(v, 2) for k, v in dim_avg.items()},
        "per_round": per_round,
        "round_scores": " ｜ ".join(f"第 {k} 轮 {v['score']}" for k, v in per_round.items()),
        "pass_probability": pass_probability(round(weighted, 1), tiers),
        "red_flags": {k: v for k, v in sorted(hit.items())},
        "fabrications": fabrications,
        "improvements": improvements,
        "band_distribution": {BAND_NAMES[b]: sum(1 for r in rows if r["band"] == b)
                              for b in (5, 4, 3, 2, 1)},
        "question_count": len(rows),
        "avg_score": round(sum(r["score"] for r in rows) / len(rows), 2),
    }


def prio_rank(row: dict) -> str:
    """改进项优先级：编造/雷区 > 低分题 > 一般。"""
    if any(c.startswith(("F-", "M-")) for c in row["red_flags"]):
        return "P0"
    if row["score"] <= 3.0:
        return "P1"
    return "P2"


# --------------------------------------------------------------------- 子命令

def cmd_run(args):
    log = Log(args.quiet)
    work = args.root
    rubric = parse_rubric(args.rubric)
    flagdoc = parse_flags(args.red_flags)
    dims, flags = rubric["dims"], flagdoc["flags"]

    risks_path = os.path.join(work, "out", "risks.json")
    risks = json.loads(read_text(risks_path)) if os.path.exists(risks_path) else []
    risk_ids = {r["id"] for r in risks}

    jdir = os.path.join(work, args.judgments)
    if not os.path.isdir(jdir):
        die(f"评审结果目录不存在: {jdir}")

    rows, rounds_present = [], []
    for name in sorted(os.listdir(jdir)):
        m = re.match(r"^round-(\d+)\.json$", name)
        if not m:
            continue
        rnd = int(m.group(1))
        tpath = os.path.join(work, "rounds", f"round-{rnd}.json")
        if not os.path.exists(tpath):
            die(f"缺少第 {rnd} 轮对话记录: {tpath}")
        transcript = json.loads(read_text(tpath))
        judg = json.loads(read_text(os.path.join(jdir, name)))
        rows += score_round(transcript, judg, dims, flags, risk_ids, rubric["rules"])
        rounds_present.append(rnd)

    if not rows:
        die(f"{jdir} 下没有找到 round-N.json 评审结果")

    agg = aggregate(rows, dims, rubric["tiers"], flags, sorted(rounds_present))
    out = {
        "meta": {
            "generated_at": now(),
            "rubric_sha1": rubric["sha1"],
            "red_flags_sha1": flagdoc["sha1"],
            "rubric_weights": dims,
            "reviewer_calls": len(rounds_present),
            "rounds": sorted(rounds_present),
        },
        "summary": agg,
        "rows": rows,
    }
    out_path = args.out if os.path.isabs(args.out) else os.path.join(work, args.out)
    write_json(out_path, out)
    log.info(f"评分卡: {out_path}")

    csv = args.csv or os.path.splitext(out_path)[0] + ".csv"
    head = "round,qid,dimension,score,band,deduction_reason,quote,red_flag,risk_id,improvement"
    lines = [head]
    for r in rows:
        main = r["deductions"][0] if r["deductions"] else {}
        cells = [r["round"], r["qid"], r["dimension"], r["score"], r["band_name"],
                 main.get("reason", r["rule_note"]), main.get("quote", ""),
                 "/".join(r["red_flags"]), "/".join(r["risk_ids"]), r["improvement"]]
        lines.append(",".join('"' + str(c).replace('"', "'") + '"' for c in cells))
    write_text(csv, "\n".join(lines) + "\n")
    log.info(f"评分表: {csv}")

    print(f"加权总分 {agg['weighted_total']} / 5（{agg['pass_probability']}）｜"
          f"分轮 {agg['round_scores']}｜题数 {agg['question_count']}")
    return agg


def cmd_compare(args):
    a = json.loads(read_text(args.a))["summary"]
    b = json.loads(read_text(args.b))["summary"]
    ta, tb = a["weighted_total"], b["weighted_total"]
    delta = abs(ta - tb)
    dd = {k: round(abs(a["dimension_scores"][k] - b["dimension_scores"][k]), 2)
          for k in a["dimension_scores"]}
    print(f"A 加权总分 {ta} ｜ B 加权总分 {tb} ｜ 偏差 {delta:.2f}（阈值 {args.tolerance}）")
    print("维度偏差: " + "  ".join(f"{k} {v}" for k, v in dd.items()))
    print(f"分轮偏差: " + "  ".join(
        f"第 {k} 轮 {abs(a['per_round'][k]['score'] - b['per_round'][k]['score']):.2f}"
        for k in a["per_round"] if k in b["per_round"]))
    if delta > args.tolerance:
        print("== 验收 A1 失败：偏差超阈值，需补充锚点样例 ==")
        raise SystemExit(1)
    print("== 验收 A1 通过（偏差 ≤ 阈值）==")
    return delta


def main(argv=None):
    p = argparse.ArgumentParser(description="P3 评分器（rubric 驱动）")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="按 rubric 计算评分卡")
    r.add_argument("--root", default=".")
    r.add_argument("--judgments", default="judgments")
    r.add_argument("--out", default=os.path.join("out", "scorecard.json"))
    r.add_argument("--csv", default=None)
    r.add_argument("--rubric", default=os.path.join(ROOT, "rubric", "scoring.md"))
    r.add_argument("--red-flags", dest="red_flags",
                   default=os.path.join(ROOT, "rubric", "red_flags.md"))
    r.add_argument("--quiet", action="store_true")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="对比两次运行的加权总分（验收 A1）")
    c.add_argument("--a", required=True)
    c.add_argument("--b", required=True)
    c.add_argument("--tolerance", type=float, default=0.3)
    c.set_defaults(func=cmd_compare)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    # 注意：main() 会返回结构化结果（dict/float），不能直接当退出码用——
    # sys.exit(非整数) 会打印对象并以 1 退出。失败路径由 die() 抛 SystemExit 处理。
    main()
    sys.exit(0)
