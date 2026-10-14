# -*- coding: utf-8 -*-
"""P3 评估层：渲染 `02_复盘报告.md` / `03_薪资建议.md` / `04_简历风险点.md`。

设计要点
- 三个产物**全部由结构化 JSON 渲染**（scorecard / salary / risks），
  报告里不会出现 JSON 里没有的数字——避免"报告阶段二次创作"。
- `04` 的改写建议**默认走确定性模板**；若提供了 agent 产出的
  `judgments/rewrites.json`，则逐条校验"不新增事实"：改写文案里出现的
  每一个数字都必须能在简历原文或风险点原文里找到，否则拒绝采用。
- 每个风险点都标注"在对话中被追问"的轮次题号，服务验收 A5（编号引用闭环）。

用法::

    python pipeline/report.py --root .
    python pipeline/report.py --root . --only 04
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, now, read_text, render, write_text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SEV_RANK = {"🔴": 0, "🟠": 1, "🟡": 2, "⚪": 3}

# 确定性改写建议模板（按风险类型）——只给动作，不造事实
REWRITE_FALLBACK = {
    "口径缺失": "保留数字，但在同一句里补出入口：分子是什么、分母是什么、统计周期多长。"
                "口径用你真实能拿出的那一个，宁可缩小数字范围，也不要留一个说不清来源的百分比。",
    "夸大词": "把「精通」降级为「熟练使用」或改成可验证的深度描述（例如用在哪个模块、解决过什么问题）。",
    "机制存疑": "改成一句话能说清失败路径的表述；说不清失败路径的机制名直接删掉，"
                "换成你在面试里能当场画出来的那段流程。",
    "时间线": "在该段经历下方补一句时间说明（学习/自由职业/家庭原因等真实情况），"
              "两段空窗要用**不同**的真实理由，不能套同一句话。",
    "能力缺口": "不写进简历。改为准备一段「如何快速补齐」的迁移论证："
                "用相邻的已落地能力说明可迁移的部分，并给出补齐路径与时间预期。",
    "数字不实": "把数字改回你能当场说清来源的那个版本（提交数、测试数、覆盖率等，"
                "按仓库里能查到的实际值写）。",
    "事实错误": "按事实改写：写清每个组件在链路里的真实角色，不确定的组件名不要出现。",
    "张冠李戴": "核对每个技术名词的真实归属后重写该句；面试里这个词被追问一次就会暴露。",
    "措辞注水": "把弱保证说成弱保证：工具/库的用法就用库的名字，不要升格成更重的组件名词。",
    "编造": "删除该表述。无法自证的内容留在简历上，会在面试里放大成信任问题。",
}

EFFORT = {"P0": "面试前必做（≤1 小时）", "P1": "本周内（≤半天）", "P2": "有空再补"}


# ------------------------------------------------------------------ 工具

def load(work, rel, required=True):
    fp = os.path.join(work, rel)
    if not os.path.exists(fp):
        if required:
            die(f"缺少输入文件: {fp}")
        return None
    with open(fp, encoding="utf-8") as f:
        return json.load(f)


def digits_of(text: str) -> set:
    """抽取文本里的数字型 token（含百分号与小数点）。"""
    return set(re.findall(r"\d+(?:\.\d+)?%?", text or ""))


def validate_rewrite(risk: dict, text: str, resume_blob: str) -> None:
    """改写建议不得引入新事实：新出现的数字必须在简历或原文里找得到。"""
    if not text or not text.strip():
        die(f"{risk['id']} 的改写建议为空")
    known = digits_of(resume_blob) | digits_of(risk.get("original", ""))
    new = digits_of(text) - known
    if new:
        die(f"{risk['id']} 的改写建议引入了简历中不存在的新数字: {sorted(new)}"
            f"（改写只能重述已有事实，不能新增）")


def probe_map(rounds: list) -> dict:
    """风险点 → 被哪些 (轮次, 题号) 追问过。"""
    m = {}
    for t in rounds:
        for item in t.get("items", []):
            for rid in item.get("targets_risk") or []:
                m.setdefault(rid, []).append(f"第{t['round']}轮{item['qid']}")
    return m


def cell(s) -> str:
    """表格单元格转义：竖线与换行会破坏 markdown 表格。"""
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


# --------------------------------------------------------------- 02 复盘报告

def build_review(work, args) -> str:
    sc = load(work, args.scorecard)
    summary, rows = sc["summary"], sc["rows"]
    jd = load(work, args.jd)
    resume = load(work, args.resume, required=False) or {}
    risks = load(work, args.risks, required=False) or []
    rounds = [load(work, f"rounds/round-{r}.json") for r in summary["per_round"]
              if os.path.exists(os.path.join(work, f"rounds/round-{r}.json"))]
    tpl = read_text(os.path.join(ROOT, "templates", "report.md"))

    by_id = {r["id"]: r for r in risks}
    probes = probe_map(rounds)

    # ---- 评分表
    score_rows = []
    for r in rows:
        dec = r["deductions"][0] if r["deductions"] else {}
        reason = dec.get("reason") or r["rule_note"] or "无扣分点"
        quote = f"「{dec.get('quote')}」" if dec.get("quote") else ""
        flags = "、".join(r["red_flags"]) or "—"
        rid = "、".join(r["risk_ids"]) or "—"
        score_rows.append(
            f"| {r['round']}-{r['qid']} | {cell(r['dimension'])} | {r['score']} | "
            f"{r['band_name']} | {cell(reason)} {cell(quote)} | {cell(flags)} | "
            f"{cell(rid)} | {cell(r['improvement'])} |")

    # ---- 追问链
    followup_rows = []
    for r in rows:
        if "🔴 答不住" in r["markers"]:
            result, point = "崩", "出现答不住标记"
        elif r["followup_collapse"]:
            result, point = "部分崩", r["followup_collapse"]
        elif r["followup_layers"] >= 3:
            result, point = "三层通过", "—"
        else:
            result, point = f"{r['followup_layers']} 层通过", "—"
        followup_rows.append(f"| {r['round']}-{r['qid']} | {r['followup_layers']} | "
                             f"{cell(result)} | {cell(point)} |")

    # ---- 雷区
    red_flag_rows = []
    for code, info in summary["red_flags"].items():
        ev = "；".join(f"{e['qid']}「{e['quote']}」" for e in info["evidence"][:2])
        red_flag_rows.append(f"| {code} | {cell(info['rule'])} | {info['count']} | {cell(ev)} |")
    if not red_flag_rows:
        red_flag_rows.append("| — | 未命中任何雷区规则 | 0 | — |")

    # ---- 编造检测
    if summary["fabrications"]:
        fabs = [f"- **{f['round']}-{f['qid']}**（{f['code']}）：「{f['quote']}」 — {f['reason']}"
                for f in summary["fabrications"]]
        fab = "检测到以下与事实不符的表述：\n\n" + "\n".join(fabs)
    else:
        fab = ("本轮**未检测到** F 类（编造）表述。\n\n"
               "> 说明：当前结论仅基于文本交叉核对（简历 facts × 回答内容）。"
               "未接入代码仓库验真时，**无法排除**「数字口径不可考」的问题——"
               "这类问题按风险提示处理，不判为编造。")

    # ---- 改进项
    improvement_rows = [f"| {i['priority']} | {cell(i['action'])} | "
                        f"{cell('、'.join(i['risk_ids']) or '—')} | {EFFORT[i['priority']]} |"
                        for i in summary["improvements"]]

    # ---- 最大风险 Top3
    hit = [r for r in risks if r["id"] in probes]
    hit.sort(key=lambda r: SEV_RANK.get(r["severity"], 9))
    top = "；".join(f"{r['severity']}{r['id']} {cell(r['why'])[:34]}" for r in hit[:3]) or "—"

    # ---- 一句话结论（由结构化数据拼装，不引入新判断）
    gaps = [r["id"] for r in risks
            if r.get("type") == "能力缺口" and r.get("severity") in ("🔴", "🟠")]
    weak_dims = sorted(summary["dimension_scores"].items(), key=lambda kv: kv[1])[:2]
    concl = (f"加权 {summary['weighted_total']} 分（{summary['pass_probability']}）；"
             f"最弱两项是 " + "、".join(f"{k} {v}" for k, v in weak_dims) +
             (f"；JD 硬性要求上有 {len(gaps)} 处高严重度缺口（{'、'.join(gaps)}），"
              f"这决定能不能过，而不是决定分数。" if gaps else "。"))

    mapping = {
        "job_title": jd.get("title", "（未提供）"),
        "rounds": f"{len(summary['per_round'])} 轮",
        "question_count": summary["question_count"],
        "resume_file": args.resume_label,
        "evidence_status": args.evidence_status,
        "timestamp": sc["meta"]["generated_at"],
        "total_score": summary["weighted_total"],
        "pass_probability": summary["pass_probability"],
        "round_scores": summary["round_scores"],
        "top_risks": top,
        "one_line_conclusion": concl,
        "score_rows": "\n".join(score_rows),
        "followup_rows": "\n".join(followup_rows),
        "red_flag_rows": "\n".join(red_flag_rows),
        "fabrication_section": fab,
        "improvement_rows": "\n".join(improvement_rows),
    }
    md = render(tpl, mapping, "templates/report.md")

    # 报告尾部追加：分轮汇总 + 维度明细（模板里没有，但契约第 2 节要求"分轮汇总"）
    extra = ["\n---\n", "## 八、分轮汇总\n", "| 轮次 | 均分 | 题数 | 档位分布 |", "|---|---|---|---|"]
    for rnd, v in summary["per_round"].items():
        bands = "、".join(f"{k}×{n}" for k, n in v["bands"].items() if n)
        extra.append(f"| 第 {rnd} 轮 | {v['score']} | {v['questions']} | {bands} |")
    extra += ["\n## 九、维度明细（加权用）\n", "| 维度 | 均分 | 权重 | 加权贡献 |", "|---|---|---|---|"]
    for k, w in sc["meta"]["rubric_weights"].items():
        v = summary["dimension_scores"][k]
        extra.append(f"| {k} | {v} | {w:.0%} | {round(v * w, 2)} |")
    extra.append(f"\n> 评分规则版本（rubric sha1）：`{sc['meta']['rubric_sha1']}`；"
                 f"雷区规则 sha1：`{sc['meta']['red_flags_sha1']}`；"
                 f"评审调用 {sc['meta']['reviewer_calls']} 次。")
    return md.replace("\n", "\n").rstrip() + "\n" + "\n".join(extra) + "\n"


# --------------------------------------------------------------- 03 薪资建议

def build_salary(work, args) -> str:
    s = load(work, args.salary)
    r = s["recommended"]
    L = ["# 期望薪资建议", "",
         f"**生成时间**：{s['meta']['generated_at']} ｜ "
         f"**JD 区间**：{s['jd_range'][0]/1000:.0f}K – {s['jd_range'][1]/1000:.0f}K", "",
         "---", "", "## 一、结论（只给区间，不给单点值）", "",
         "| 下限 | 中位 | 上限 |", "|---|---|---|",
         f"| **{r['low']/1000:.1f}K** | **{r['mid']/1000:.1f}K** | **{r['high']/1000:.1f}K** |", "",
         f"定位分位：`{s['position']}`（0 = JD 区间下限，1 = 上限）", "",
         "---", "", "## 二、推理链（每一步都带依据）", "",
         "| 步骤 | 取值 | 依据 |", "|---|---|---|"]
    for c in s["reasoning_chain"]:
        L.append(f"| {cell(c['step'])} | {cell(c['value'])} | {cell(c['basis'])} |")
    L += ["", "---", "", "## 三、依据声明", "", s["basis_statement"], "",
          "输入文件：" + "、".join(f"`{p}`" for p in s["meta"]["inputs"]), "",
          "---", "", "## 四、报价策略", "", "| 步骤 | 话术 |", "|---|---|"]
    for n in s["negotiation"]:
        L.append(f"| {cell(n['step'])} | {cell(n['script'])} |")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------- 04 风险点清单

def build_risks(work, args) -> str:
    risks = load(work, args.risks)
    resume = load(work, args.resume, required=False) or {}
    resume_blob = json.dumps(resume, ensure_ascii=False)
    rounds = []
    for name in sorted(os.listdir(os.path.join(work, "rounds"))):
        m = re.match(r"^round-(\d+)\.json$", name)
        if m and "interviewer" not in name and "answers" not in name:
            rounds.append(load(work, f"rounds/{name}"))
    probes = probe_map(rounds)

    rew = load(work, args.rewrites, required=False) or {}
    provided = {x["risk_id"]: x for x in rew.get("rewrites", [])}
    for rid, x in provided.items():
        if rid not in {r["id"] for r in risks}:
            die(f"改写建议引用了不存在的风险点编号: {rid}")
        validate_rewrite(next(r for r in risks if r["id"] == rid), x.get("text", ""), resume_blob)

    counts = {}
    for r in risks:
        counts[r["severity"]] = counts.get(r["severity"], 0) + 1
    order = sorted(risks, key=lambda r: (SEV_RANK.get(r["severity"], 9), r["id"]))

    L = ["# 简历风险点清单", "",
         f"**生成时间**：{now()} ｜ **风险点**：{len(risks)} 条（" +
         "、".join(f"{k} {v} 条" for k, v in sorted(counts.items(), key=lambda kv: SEV_RANK.get(kv[0], 9))) +
         "）", "",
         "> 说明：本清单只基于**文本判断**（简历原文 × JD 要求）。"
         "标注「仅叙述」的条目在没有代码仓库验真前，只能提示风险，不能断言编造。", "",
         "---", "", "## 一、清单", "",
         "| 编号 | 类型 | 严重度 | 可验证性 | 原文 | 问题 | 在对话中被追问 |",
         "|---|---|---|---|---|---|---|"]
    for r in order:
        L.append(f"| {r['id']} | {cell(r['type'])} | {r['severity']} | {cell(r['verifiability'])} | "
                 f"{cell(r['original'])[:120]} | {cell(r['why'])} | "
                 f"{cell('、'.join(probes.get(r['id'], [])) or '**未被追问**')} |")

    L += ["", "---", "", "## 二、改写建议（不新增事实）", ""]
    for r in order:
        x = provided.get(r["id"])
        text = x["text"] if x else REWRITE_FALLBACK.get(r["type"], "按事实重写该句，删掉无法自证的部分。")
        src = "评审 agent 产出（已通过「不新增数字」校验）" if x else "确定性模板"
        L.append(f"**{r['id']}**｜{r['severity']} {cell(r['type'])}｜来源：{src}")
        L.append("")
        L.append(f"> {text}")
        if x and x.get("note"):
            L.append(">")
            L.append(f"> 备注：{x['note']}")
        L.append("")
        L.append(f"追问准备：{cell(r['probe_hint'])}")
        L.append("")

    total = len(risks)
    covered = sum(1 for r in risks if probes.get(r["id"]))
    L += ["---", "", "## 三、闭环校验", "",
          f"- 风险点总数：{total}",
          f"- 已在对话中被追问：{covered} / {total}"
          + ("（全部覆盖）" if covered == total else "（**存在未被追问的条目**）"),
          f"- 改写建议已通过「不新增数字」校验：{len(provided)} 条来自评审 agent，"
          f"{total - len(provided)} 条来自确定性模板", ""]
    out = "\n".join(L) + "\n"
    if args.strict and covered != total:
        die(f"编号引用闭环未达成：{total - covered} 条风险点没有被追问过")
    return out


# ------------------------------------------------------------------- 主流程

def cmd_all(args):
    log = Log(args.quiet)
    work = args.root
    made = []
    if args.only in ("all", "02"):
        p = os.path.join(work, "02_复盘报告.md")
        write_text(p, build_review(work, args))
        made.append(p)
    if args.only in ("all", "03"):
        p = os.path.join(work, "03_薪资建议.md")
        write_text(p, build_salary(work, args))
        made.append(p)
    if args.only in ("all", "04"):
        p = os.path.join(work, "04_简历风险点.md")
        write_text(p, build_risks(work, args))
        made.append(p)
    for p in made:
        log.info(f"已生成: {p}")
    for p in made:
        print(p)
    return made


def main(argv=None):
    p = argparse.ArgumentParser(description="P3 报告渲染")
    p.add_argument("--root", default=".")
    p.add_argument("--only", choices=["all", "02", "03", "04"], default="all")
    p.add_argument("--scorecard", default=os.path.join("out", "scorecard.json"))
    p.add_argument("--salary", default=os.path.join("out", "salary.json"))
    p.add_argument("--risks", default=os.path.join("out", "risks.json"))
    p.add_argument("--jd", default=os.path.join("out", "real", "jd.json"))
    p.add_argument("--resume", default=os.path.join("out", "real", "resume.json"))
    p.add_argument("--rewrites", default=os.path.join("judgments", "rewrites.json"))
    p.add_argument("--resume-label", dest="resume_label", default="（未提供）")
    p.add_argument("--evidence-status", dest="evidence_status", default="未验真（P4 未运行）")
    p.add_argument("--strict", action="store_true", help="闭环未达成时返回非 0")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    return cmd_all(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
