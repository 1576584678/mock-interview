#!/usr/bin/env python3
"""P2 演练层 · 轮次编排与状态落盘

职责边界（重要）
----------------
本脚本**不做任何模型调用**，它只负责：
  1. 把 `prompts/*.md` 渲染成"某一轮的具体指令文件"（注入 JD/简历/风险点/上一轮问答）
  2. 校验 agent 产出的 JSON 是否满足契约（不满足就报错退出，不产生半成品）
  3. 把每轮结果落盘 `rounds/round-N.json`，并维护 `rounds/state.json`
  4. `check` 校验 ROADMAP 中 P2 的 DoD 是否真的达成

模型调用由宿主（DSH agent）执行：
  prepare → 面试官 agent 写 round-N.interviewer.json
  prepare-candidate → 求职者 agent 写 round-N.answers.json
  commit  → 合并成 round-N.json

用法
----
  python pipeline/orchestrate.py init --resume out/resume.json --jd out/jd.json --root .
  python pipeline/orchestrate.py prepare --round 1 --root .
  python pipeline/orchestrate.py prepare-candidate --round 1 --root .
  python pipeline/orchestrate.py commit --round 1 --root . \
        --questions rounds/round-1.interviewer.json --answers rounds/round-1.answers.json
  python pipeline/orchestrate.py check --root .
  python pipeline/orchestrate.py status --root .
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

from common import Log, as_text, die, read_text, write_text, write_json, now
from risks import seed_risks

QUESTIONS_PER_ROUND = 5
MAX_FOLLOWUPS = 3
MIN_Q, MAX_Q = 4, 6
STATE = "state.json"
PROMPT_DIR = "prompts"


# ---------------------------------------------------------------- 基础设施

def load_json(path, what="文件"):
    if not os.path.exists(path):
        die(f"{what}不存在: {path}")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        die(f"{what}不是合法 JSON: {path}\n  {e}")


def rounds_dir(root):
    return os.path.join(root, "rounds")


def state_path(root):
    return os.path.join(rounds_dir(root), STATE)


def load_state(root):
    p = state_path(root)
    if not os.path.exists(p):
        die(f"尚未初始化，先执行 init（缺少 {p}）")
    return load_json(p, "状态文件")


def save_state(root, st):
    st["updated"] = now()
    write_json(state_path(root), st)


def render(tpl: str, mapping: dict, where: str) -> str:
    """替换 {{KEY}}；出现未提供的占位符直接报错，避免把空提示词喂给模型。"""
    missing = set()

    def sub(m):
        k = m.group(1)
        if k not in mapping:
            missing.add(k)
            return m.group(0)
        return str(mapping[k])

    out = re.sub(r"\{\{([A-Z_]+)\}\}", sub, tpl)
    if missing:
        die(f"{where} 模板占位符未被赋值: {', '.join(sorted(missing))}")
    return out


# ---------------------------------------------------------------- 渲染素材

def render_jd(jd: dict) -> str:
    L = []
    L.append(f"岗位：{jd.get('title', '(未知)')}")
    if jd.get("salary_range"):
        L.append(f"薪资：{jd['salary_range'][0]} - {jd['salary_range'][1]}"
                 + (f"（{jd['salary_note']}）" if jd.get("salary_note") else ""))
    if jd.get("business_context"):
        L.append(f"业务场景：{jd['business_context']}")
    L.append("\n【硬性要求】")
    for it in jd.get("must_have", []):
        t = it.get("text") if isinstance(it, dict) else str(it)
        d = it.get("dimension", "") if isinstance(it, dict) else ""
        L.append(f"- [{d}] {t}")
    if jd.get("duties"):
        L.append("\n【岗位职责（出题方向参考，不是硬性门槛）】")
        for it in jd["duties"]:
            t = it.get("text") if isinstance(it, dict) else str(it)
            L.append(f"- {t}")
    if jd.get("nice_to_have"):
        L.append("\n【加分项】")
        for it in jd.get("nice_to_have", []):
            t = it.get("text") if isinstance(it, dict) else str(it)
            L.append(f"- {t}")
    if jd.get("hidden_pain_points"):
        L.append("\n【隐藏痛点（推断，仅供出题方向，不要直接问）】")
        for p in jd["hidden_pain_points"]:
            L.append(f"- {p}")
    return "\n".join(L)


def render_resume(resume: dict, max_hl: int = 6, width: int = 170,
                  claim_width: int = 90) -> str:
    """渲染简历事实。

    **必须给足事实**：早期版本每段经历只注入 3 条要点、每条截 100 字，
    结果求职者 agent 把简历里真实存在的数字（95% 拦截率、99.98% 准确率）
    当成"面试官编造的陷阱数字"拒绝认领——这同样违反"不编造"，
    只是方向反了。所以这里连带把 claims 清单（简历中出现的全部数字与机制）
    一并注入，它才是候选人真正的事实边界。
    """
    L = []
    b = resume.get("basic", {})
    L.append(f"姓名：{b.get('name', '')}｜年限：{b.get('years', '')} 年")
    if resume.get("summary"):
        L.append("个人总结：" + as_text(resume["summary"])[:600])
    if resume.get("skills"):
        L.append("\n【技能】")
        for g in resume["skills"]:
            L.append(f"- {g.get('category', '')}：{'、'.join(g.get('items', [])[:12])}")
    if resume.get("experiences"):
        L.append("\n【工作经历】")
        for e in resume["experiences"]:
            L.append(f"- {e.get('company', '')}｜{e.get('title', '')}｜{e.get('start', '')} - {e.get('end', '')}")
            for h in e.get("highlights", [])[:max_hl]:
                L.append(f"    · {h[:width]}")
    if resume.get("projects"):
        L.append("\n【项目经历】")
        for p in resume["projects"]:
            L.append(f"- {p.get('name', '')}｜{p.get('period', '')}")
            for h in p.get("highlights", [])[:max_hl]:
                L.append(f"    · {h[:width]}")
    if resume.get("timeline_gaps"):
        L.append("\n【时间线空窗】")
        for g in resume["timeline_gaps"]:
            L.append(f"- {g.get('from')} → {g.get('to')}（约 {g.get('months')} 个月）")

    claims = resume.get("claims", {})
    if claims.get("quantitative"):
        L.append("\n【简历中出现的量化声明（说这些数字时必须给出清单里的口径）】")
        for c in claims["quantitative"]:
            L.append(f"- {as_text(c.get('text')).strip()[:claim_width]}")
    if claims.get("mechanism"):
        L.append("\n【简历中出现的机制声明（被追问时必须讲清失败路径）】")
        for c in claims["mechanism"]:
            L.append(f"- {as_text(c.get('keyword'))}：{as_text(c.get('text')).strip()[:claim_width]}")
    return "\n".join(L)


def render_risks(risks: list) -> str:
    if not risks:
        return "（本轮无需定点攻击）"
    L = []
    for r in risks:
        L.append(f"[{r['id']}] {r['type']}/{r['severity']}｜原文：{r['original'][:90]}\n"
                 f"      被问倒的原因：{r['why']}\n"
                 f"      追问方向：{r['probe_hint']}")
    return "\n".join(L)


def render_prev_qa(root, round_no: int, width: int = 130) -> str:
    if round_no <= 1:
        return "（第一轮，无历史）"
    p = os.path.join(rounds_dir(root), f"round-{round_no - 1}.json")
    if not os.path.exists(p):
        return "（上一轮未落盘）"
    rec = load_json(p, "上一轮记录")
    L = [f"—— 第 {round_no - 1} 轮问答摘要 ——"]
    for it in rec.get("items", []):
        L.append(f"Q[{it.get('qid')}]（{it.get('dimension', '')}）{it.get('question', '')[:width]}")
        L.append(f"  A：{it.get('answer', '')[:width]}")
        for f in it.get("followups", []):
            L.append(f"  追问：{f.get('question', '')[:90]}")
            L.append(f"    答：{f.get('answer', '')[:width]}")
    return "\n".join(L)


# ---------------------------------------------------------------- 初始化

def collect_must_dims(jd: dict) -> list:
    dims = []
    for it in jd.get("must_have", []):
        d = it.get("dimension", "") if isinstance(it, dict) else ""
        if d and d not in dims:
            dims.append(d)
    return dims


def chunk_even(items: list, n: int) -> list:
    """连续切块（不是轮转）：保证最高严重度的风险落在最早轮次。

    这样即使运行被中断，最重要的点也已经被问过——这是有意的取舍。
    """
    chunks = [[] for _ in range(n)]
    if not items:
        return chunks
    per = (len(items) + n - 1) // n
    for i, x in enumerate(items):
        idx = min(i // per, n - 1)
        chunks[idx].append(x)
    return chunks


def make_plan(must_dims: list, risks: list, max_rounds: int) -> dict:
    dim_chunks = chunk_even(must_dims, max_rounds)
    risk_chunks = chunk_even([r["id"] for r in risks], max_rounds)
    themes = ["开场与动机", "技术主干", "场景与架构", "收尾与反问"]
    plan = {}
    for i in range(max_rounds):
        plan[str(i + 1)] = {
            "theme": themes[i] if i < len(themes) else f"第 {i + 1} 轮",
            "dims": dim_chunks[i],
            "risks": risk_chunks[i],
        }
    return plan


def cmd_init(args):
    log = Log(args.quiet)
    resume = load_json(args.resume, "简历解析结果")
    jd = load_json(args.jd, "JD 解析结果")
    risks = seed_risks(resume, jd, max_risks=args.max_risks)
    must_dims = collect_must_dims(jd)
    st = {
        "created": now(),
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "finished_at": None,
        "max_rounds": args.max_rounds,
        "calls": 0,
        "current_round": 0,
        "position": jd.get("title", ""),
        "must_dims": must_dims,
        "covered_dims": {},
        "risks": risks,
        "probed": {},
        "asked": [],
        "plan": make_plan(must_dims, risks, args.max_rounds),
        "sources": {"resume": os.path.abspath(args.resume), "jd": os.path.abspath(args.jd)},
    }
    os.makedirs(rounds_dir(args.root), exist_ok=True)
    save_state(args.root, st)
    # 风险清单同时落一份独立产物：P3 风险报告 / P4 验真层都直接消费它，
    # 不必去 state.json 里挖（与 docs/ARCHITECTURE.md 的数据流一致）。
    risks_path = os.path.join(args.root, "out", "risks.json")
    write_json(risks_path, risks)
    log.info(f"风险清单: {risks_path}")
    log.info(f"风险点 {len(risks)} 条，硬性维度 {len(must_dims)} 个，计划 {args.max_rounds} 轮")
    for r in risks:
        log.info(f"  {r['id']} {r['severity']} {r['type']}｜{r['original'][:50]}")


# ---------------------------------------------------------------- 出题轮

def cmd_prepare(args):
    log = Log(args.quiet)
    root = args.root
    st = load_state(root)
    key = str(args.round)
    if key not in st["plan"]:
        die(f"轮次 {args.round} 超出计划（共 {st['max_rounds']} 轮）")
    ctx = st["plan"][key]
    risk_map = {r["id"]: r for r in st["risks"]}
    risks = [risk_map[i] for i in ctx["risks"] if i in risk_map]

    tpl = read_text(os.path.join(root, PROMPT_DIR, "interviewer.md"))
    out_path = os.path.join(rounds_dir(root), f"round-{args.round}.interviewer.json")
    body = render(tpl, {
        "POSITION": st["position"],
        "ROUND": args.round,
        "ROUND_TOTAL": st["max_rounds"],
        "THEME": ctx["theme"],
        "TARGET_DIMS": "、".join(ctx["dims"]) or "（本轮无指定维度，按 JD 自由覆盖）",
        "TARGET_RISKS": render_risks(risks),
        "JD": render_jd(load_json(os.path.abspath(st["sources"]["jd"]), "JD 解析结果")),
        "RESUME": render_resume(load_json(os.path.abspath(st["sources"]["resume"]), "简历解析结果")),
        "PREV_QA": render_prev_qa(root, args.round),
        "ASKED": "；".join(st["asked"][-12:]) or "（无）",
        "MAX_Q": QUESTIONS_PER_ROUND,
        "MAX_FOLLOWUPS": MAX_FOLLOWUPS,
        "OUT_PATH": out_path,
    }, "interviewer.md")

    p = os.path.join(rounds_dir(root), f"round-{args.round}.interviewer.md")
    write_text(p, body)
    write_json(os.path.join(rounds_dir(root), f"round-{args.round}.context.json"),
               {"round": args.round, "theme": ctx["theme"], "dims": ctx["dims"],
                "risks": [r["id"] for r in risks], "ts": now()})
    log.info(f"已生成出题指令: {p}")
    log.info(f"产出请写入: {out_path}")
    print(p)


# ---------------------------------------------------------------- 答题轮

def validate_interviewer(obj: dict, st: dict) -> list:
    errs = []
    qs = obj.get("questions")
    if not isinstance(qs, list) or not (MIN_Q <= len(qs) <= MAX_Q):
        errs.append(f"questions 必须是 {MIN_Q}-{MAX_Q} 条，当前 "
                    f"{len(qs) if isinstance(qs, list) else '非列表'}")
        return errs
    ids = set()
    valid_risks = {r["id"] for r in st["risks"]}
    for i, q in enumerate(qs, 1):
        for f in ("qid", "dimension", "question", "followups"):
            if not q.get(f):
                errs.append(f"第 {i} 题缺字段 {f}")
        if q.get("qid") in ids:
            errs.append(f"qid 重复：{q.get('qid')}")
        ids.add(q.get("qid"))
        fs = q.get("followups") or []
        if not (1 <= len(fs) <= MAX_FOLLOWUPS):
            errs.append(f"{q.get('qid')} 追问层数必须是 1-{MAX_FOLLOWUPS}，当前 {len(fs)}")
        for rid in q.get("targets_risk", []) or []:
            if rid not in valid_risks:
                errs.append(f"{q.get('qid')} 引用了不存在的风险点 {rid}")
    return errs


def cmd_prepare_candidate(args):
    log = Log(args.quiet)
    root = args.root
    st = load_state(root)
    qp = os.path.join(rounds_dir(root), f"round-{args.round}.interviewer.json")
    obj = load_json(qp, "出题结果")
    errs = validate_interviewer(obj, st)
    if errs:
        die("出题结果不符合契约：\n  - " + "\n  - ".join(errs))

    lines = []
    for q in obj["questions"]:
        tgt = ("　目标风险：" + "、".join(q.get("targets_risk", []))) if q.get("targets_risk") else ""
        lines.append(f"### {q['qid']}｜维度：{q['dimension']}{tgt}")
        lines.append(f"问题：{q['question']}")
        lines.append("面试官准备的追问链（请**逐层**回答，共 "
                     f"{len(q['followups'])} 层）：")
        for j, f in enumerate(q["followups"], 1):
            lines.append(f"  {j}. {f}")
        lines.append("")

    tpl = read_text(os.path.join(root, PROMPT_DIR, "candidate.md"))
    out_path = os.path.join(rounds_dir(root), f"round-{args.round}.answers.json")
    body = render(tpl, {
        "POSITION": st["position"],
        "ROUND": args.round,
        "RESUME": render_resume(load_json(os.path.abspath(st["sources"]["resume"]), "简历解析结果")),
        "QUESTIONS": "\n".join(lines),
        "MAX_FOLLOWUPS": MAX_FOLLOWUPS,
        "OUT_PATH": out_path,
    }, "candidate.md")
    p = os.path.join(rounds_dir(root), f"round-{args.round}.candidate.md")
    write_text(p, body)
    log.info(f"已生成答题指令: {p}")
    log.info(f"产出请写入: {out_path}")
    print(p)


# ---------------------------------------------------------------- 合并落盘

def decide_markers(item: dict) -> list:
    """P2 只做规则可判定的标记；「编造」的判定属于 P3/P4，不在这里猜。"""
    m = []
    ans = (item.get("answer") or "").strip()
    conf = item.get("confidence") or ""
    if len(ans) < 40 or re.search(r"(不知道|答不上|没了解过|没想过|不清楚)", ans):
        m.append("🔴 答不住")
    if "不确定" in conf:
        m.append("🟡 示弱")
    return m


def cmd_commit(args):
    log = Log(args.quiet)
    root = args.root
    st = load_state(root)
    qs = load_json(args.questions, "出题结果")
    ans = load_json(args.answers, "答题结果")
    errs = validate_interviewer(qs, st)
    if errs:
        die("出题结果不符合契约：\n  - " + "\n  - ".join(errs))
    if ans.get("round") != args.round or qs.get("round") != args.round:
        die(f"轮次不一致：命令行 {args.round}，出题 {qs.get('round')}，答题 {ans.get('round')}")

    ans_by_qid = {}
    for a in ans.get("items", []):
        if not a.get("qid"):
            die("答题结果有 item 缺 qid")
        ans_by_qid[a["qid"]] = a

    items = []
    missing = []
    for q in qs["questions"]:
        a = ans_by_qid.get(q["qid"])
        if not a:
            missing.append(q["qid"])
            continue
        fs = []
        for f in (a.get("followups") or [])[:MAX_FOLLOWUPS]:
            if not f.get("question") or not f.get("answer"):
                die(f"{q['qid']} 的追问必须同时有 question 与 answer")
            fs.append({"question": f["question"], "answer": f["answer"]})
        items.append({
            "qid": q["qid"],
            "dimension": q["dimension"],
            "question": q["question"],
            "targets_risk": q.get("targets_risk", []),
            "expected_signals": q.get("expected_signals", []),
            "answer": a.get("answer", ""),
            "confidence": a.get("confidence", ""),
            "duration_seconds": a.get("duration_seconds"),
            "self_flags": a.get("self_flags", []),
            "followups": fs,
            "markers": decide_markers(a),
        })
    if missing:
        die(f"以下问题没有作答：{', '.join(missing)}")

    rec = {
        "round": args.round,
        "theme": qs.get("theme", st["plan"][str(args.round)]["theme"]),
        "ts": now(),
        "items": items,
        "candidate_questions": ans.get("candidate_questions", []),
    }
    write_json(os.path.join(rounds_dir(root), f"round-{args.round}.json"), rec)

    # 更新状态
    for it in items:
        st["asked"].append(it["question"][:60])
        if it["dimension"] in st["must_dims"]:
            st["covered_dims"][it["dimension"]] = st["covered_dims"].get(it["dimension"], 0) + 1
        if it["followups"]:
            for rid in it["targets_risk"]:
                st["probed"][rid] = st["probed"].get(rid, 0) + 1
    st["calls"] = st.get("calls", 0) + 2
    st["current_round"] = max(st.get("current_round", 0), args.round)
    if args.round >= st["max_rounds"]:
        st["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    save_state(root, st)
    log.info(f"第 {args.round} 轮落盘：{len(items)} 题，追问 "
             f"{sum(len(i['followups']) for i in items)} 层，调用累计 {st['calls']} 次")


# ---------------------------------------------------------------- 校验 DoD

def cmd_check(args):
    log = Log(args.quiet)
    root = args.root
    st = load_state(root)
    ok = True

    print("== P2 DoD 校验 ==")
    n_rounds = st.get("current_round", 0)
    print(f"[1] 轮次：{n_rounds}/{st['max_rounds']} 轮已落盘"
          + ("  ✓" if n_rounds >= min(3, st["max_rounds"]) else "  ✗ 需 3 轮"))
    ok &= n_rounds >= min(3, st["max_rounds"])

    print(f"\n[2] JD 硬性维度覆盖：{len(st['covered_dims'])}/{len(st['must_dims'])}")
    for d in st["must_dims"]:
        n = st["covered_dims"].get(d, 0)
        print(f"    {'✓' if n else '✗'} {d}（{n} 题）")
    ok &= all(st["covered_dims"].get(d) for d in st["must_dims"])

    print(f"\n[3] 风险点追问覆盖：{len(st['probed'])}/{len(st['risks'])}")
    for r in st["risks"]:
        n = st["probed"].get(r["id"], 0)
        print(f"    {'✓' if n else '✗'} {r['id']} {r['severity']} {r['type']}"
              f"（被追问 {n} 次）｜{r['original'][:40]}")
    ok &= all(st["probed"].get(r["id"]) for r in st["risks"])

    calls = st.get("calls", 0)
    print(f"\n[4] 模型调用：{calls}/60" + ("  ✓" if calls <= 60 else "  ✗"))
    ok &= calls <= 60

    elapsed = None
    if st.get("started_at"):
        a = datetime.datetime.fromisoformat(st["started_at"])
        b = datetime.datetime.fromisoformat(st["finished_at"]) if st.get("finished_at") \
            else datetime.datetime.now()
        elapsed = (b - a).total_seconds() / 60.0
        print(f"[5] 墙钟耗时：{elapsed:.1f} 分钟"
              + ("  ✓ ≤15" if elapsed <= 15 else "  ✗ 超过 15 分钟（见 README 说明）"))
    print("\n== " + ("全部通过" if ok else "存在未达标项") + " ==")
    sys.exit(0 if ok else 1)


def cmd_status(args):
    st = load_state(args.root)
    print(json.dumps({k: st[k] for k in
                      ("position", "max_rounds", "current_round", "calls",
                       "must_dims", "covered_dims", "probed", "started_at", "finished_at")
                      if k in st}, ensure_ascii=False, indent=2))
    print(f"风险点 {len(st['risks'])} 条，已追问 {len(st['probed'])} 条")


# ---------------------------------------------------------------- CLI

def main():
    p = argparse.ArgumentParser(description="P2 演练层 · 轮次编排")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, need_round=False):
        sp.add_argument("--root", default=".", help="工作区根目录（含 prompts/ rounds/）")
        sp.add_argument("--quiet", action="store_true")
        if need_round:
            sp.add_argument("--round", type=int, required=True)

    sp = sub.add_parser("init")
    add_common(sp)
    sp.add_argument("--resume", required=True)
    sp.add_argument("--jd", required=True)
    sp.add_argument("--max-rounds", type=int, default=3)
    sp.add_argument("--max-risks", type=int, default=12)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("prepare")
    add_common(sp, True)
    sp.set_defaults(func=cmd_prepare)

    sp = sub.add_parser("prepare-candidate")
    add_common(sp, True)
    sp.set_defaults(func=cmd_prepare_candidate)

    sp = sub.add_parser("commit")
    add_common(sp, True)
    sp.add_argument("--questions", required=True)
    sp.add_argument("--answers", required=True)
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("check")
    add_common(sp)
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("status")
    add_common(sp)
    sp.set_defaults(func=cmd_status)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
