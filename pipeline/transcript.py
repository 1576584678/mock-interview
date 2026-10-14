# -*- coding: utf-8 -*-
"""P5 产出层：渲染 `01_面试对话实录.md`。

设计要点
- 纯渲染：实录里**每一个字都来自 `rounds/round-N.json`**，不做二次创作，
  面试官的题面逐字保留（这是"复现面试现场"的前提）。
- 标记体系与 `02_复盘报告.md` 一致：`[R-xx]` 关联风险点、`🔴` 答不住、
  `⚠️` 与简历矛盾/技术性错误、`🟡` 主动示弱、`✅` 给出可验证细节。
  标记来源是 P2 落盘的 `markers` / `self_flags` / `targets_risk`，不是渲染时猜的。
- 服务验收 A5：实录必须带上 `[R-xx]`，否则"编号引用闭环"断链。

用法::

    python pipeline/transcript.py --root .
    python pipeline/transcript.py --root . --resume-label 李岳临_简历.pdf --quiet
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text, write_text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

ROUND_RE = re.compile(r"^round-(\d+)\.json$")

MARK_LEGEND = [
    ("[R-xx]", "关联简历风险点（编号见 `04_简历风险点.md`）"),
    ("🔴", "答不住：明确承认不会，或给不出任何可验证细节"),
    ("⚠️", "与简历原文矛盾，或技术性错误（张冠李戴 / 常识错误）"),
    ("🟡", "主动示弱：承认边界但给出推理路径（风险控制加分行为）"),
    ("✅", "给出可验证细节：命令、字段、数值来源、失败路径"),
]


def load_rounds(work: str) -> list:
    """只认 `round-N.json`；`round-N.answers.json` / `round-N.interviewer.json`
    是 P2 的中间产物，格式不同，混进来会把轮次数与题数算错。"""
    files = []
    for f in glob.glob(os.path.join(work, "rounds", "*.json")):
        if ROUND_RE.match(os.path.basename(f)):
            files.append(f)
    rounds = []
    for f in sorted(files):
        try:
            r = json.loads(read_text(f))
        except Exception as e:  # noqa: BLE001
            die(f"轮次文件解析失败 {f}: {e}")
        if not isinstance(r.get("items"), list):
            die(f"轮次文件缺少 items 数组：{f}")
        rounds.append(r)
    rounds.sort(key=lambda r: int(r.get("round", 0)))
    return rounds


def markers_of(item: dict) -> list:
    """短标记（emoji 级），来自 P2 的 `markers`。

    注意：`self_flags` 存的是候选人**长段自评依据**（例如"本回答依据简历的…，
    口径为…，无简历依据的已显式示弱"），不是标记——早期版本把它当标记渲染，
    结果统计表里塞进了 50 条长文本。
    """
    return list(item.get("markers") or [])


def self_flags_of(item: dict) -> list:
    return [" ".join(str(s).split()) for s in (item.get("self_flags") or []) if str(s).strip()]


def risk_tags(item: dict) -> list:
    return list(item.get("targets_risk") or [])


def fmt_q(text: str) -> str:
    return " ".join(str(text or "").split())


def render_round(r: dict) -> list:
    out = []
    n = r.get("round", "?")
    out.append(f"## 第 {n} 轮 · {r.get('theme', '')}".rstrip())
    out.append("")
    if r.get("ts"):
        out.append(f"*本轮机位时间：{r['ts']}*")
        out.append("")
    for item in r.get("items", []):
        qid = item.get("qid", "?")
        risks = risk_tags(item)
        title = f"### {n}-{qid} ｜ 维度：{item.get('dimension', '')}"
        if risks:
            title += " ｜ " + " ".join(f"[{x}]" for x in risks)
        out.append(title)
        out.append("")
        out.append(f"**面试官**：{fmt_q(item.get('question'))}")
        out.append("")
        ms = markers_of(item)
        suffix = (" " + " ".join(ms)) if ms else ""
        out.append(f"**候选人**：{fmt_q(item.get('answer'))}{suffix}")
        out.append("")
        for i, fu in enumerate(item.get("followups") or [], 1):
            out.append(f"**追问 {i}（面试官）**：{fmt_q(fu.get('question'))}")
            out.append("")
            out.append(f"**追问 {i}（候选人）**：{fmt_q(fu.get('answer'))}")
            out.append("")
        meta = []
        if ms:
            meta.append("标记：" + " ".join(ms))
        if risks:
            meta.append("关联风险点：" + "、".join(risks))
        if item.get("confidence"):
            meta.append(f"自评把握度：{item['confidence']}")
        if item.get("duration_seconds"):
            meta.append(f"作答时长：{item['duration_seconds']}s")
        out.append(f"> {' ｜ '.join(meta)}" if meta else "> —")
        sig = item.get("expected_signals") or []
        if sig:
            out.append("> **本题预期信号**：" + "；".join(fmt_q(s) for s in sig))
        sfs = self_flags_of(item)
        if sfs:
            out.append(">")
            out.append("> **候选人自评依据**（P2 落盘的事实边界声明，逐条保留）：")
            for s in sfs:
                out.append(f"> - {s}")
        out.append("")
    cqs = r.get("candidate_questions") or []
    if cqs:
        out.append(f"### 第 {n} 轮 · 候选人反问")
        out.append("")
        for i, q in enumerate(cqs, 1):
            out.append(f"{i}. {fmt_q(q)}")
        out.append("")
    return out


def build(work: str, state: dict, rounds: list, resume_label: str) -> str:
    items = [it for r in rounds for it in r.get("items", [])]
    n_q = len(items)
    n_fu = sum(len(it.get("followups") or []) for it in items)
    probed = state.get("probed") or {}
    if isinstance(probed, dict):
        probed_ids = list(probed.keys())
    else:
        probed_ids = list(probed)

    head = []
    head.append("# 模拟面试对话实录")
    head.append("")
    head.append(f"**岗位**：{state.get('position', '—')} ｜ **轮次**：{len(rounds)} 轮 ｜ "
                f"**题目数**：{n_q} ｜ **追问层数**：{n_fu}")
    head.append(f"**开始**：{state.get('started_at', '—')} ｜ **结束**：{state.get('finished_at', '—')} ｜ "
                f"**模型调用**：{state.get('calls', '—')} 次 ｜ **简历**：{resume_label or '—'}")
    head.append("")
    head.append("> **标记含义**：" + "；".join(f"`{k}` {v}" for k, v in MARK_LEGEND))
    head.append(">")
    head.append(f"> **风险点覆盖**：{len(probed_ids)} 条在对话中被追问"
                f"（{('、'.join(probed_ids)) if probed_ids else '无'}）。"
                "本实录为逐字稿，面试官题面与候选人作答均按 P2 落盘原文呈现，未做改写。")
    head.append("")
    head.append("---")
    head.append("")

    body = []
    for r in rounds:
        body += render_round(r)
        body.append("---")
        body.append("")

    tail = []
    tail.append("## 全场面板统计")
    tail.append("")
    tail.append("| 指标 | 值 |")
    tail.append("|---|---|")
    tail.append(f"| 轮次 | {len(rounds)} |")
    tail.append(f"| 主题问题数 | {n_q} |")
    tail.append(f"| 追问层数 | {n_fu} |")
    tail.append(f"| 候选人反问数 | {sum(len(r.get('candidate_questions') or []) for r in rounds)} |")
    if items:
        main_len = [len(str(it.get("answer") or "")) for it in items]
        tail.append(f"| 主答平均字数 | {round(sum(main_len) / len(main_len))} |")
        tail.append(f"| 主答最短/最长 | {min(main_len)} / {max(main_len)} |")
    all_marks = [m for it in items for m in markers_of(it)]
    n_sf = sum(len(self_flags_of(it)) for it in items)
    tail.append(f"| 标记总数 | {len(all_marks)} |")
    if all_marks:
        uniq = sorted(set(all_marks))
        tail.append("| 标记分布 | " + "；".join(f"{u}×{all_marks.count(u)}" for u in uniq) + " |")
    tail.append(f"| 候选人自评依据条数 | {n_sf} |")
    tail.append(f"| 命中风险点 | {len(probed_ids)} / {len(state.get('risks') or [])} |")
    tail.append("")
    return "\n".join(head + body + tail)


def cmd_run(args):
    work = os.path.abspath(args.root)
    log = Log(quiet=args.quiet)
    state_path = os.path.join(work, "rounds", "state.json")
    if not os.path.exists(state_path):
        die(f"缺少 {state_path}——请先跑 P2 演练")
    state = json.loads(read_text(state_path))
    rounds = load_rounds(work)
    if not rounds:
        die(f"{work}/rounds 下没有 round-N.json——请先跑 P2 演练")
    text = build(work, state, rounds, args.resume_label)
    out = args.out if os.path.isabs(args.out) else os.path.join(work, args.out)
    write_text(out, text)
    log.info(f"已生成: {out}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="渲染 01_面试对话实录.md")
    p.add_argument("--root", default=".")
    p.add_argument("--out", default="01_面试对话实录.md")
    p.add_argument("--resume-label", default="")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
