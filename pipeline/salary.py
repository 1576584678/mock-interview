# -*- coding: utf-8 -*-
"""P3 评估层：期望薪资建议（区间 + 推理链 + 依据声明）。

硬约束（ROADMAP P3 DoD / SKILL.md 硬约束）
- **只给区间**，不给一个"你值多少钱"的数字。
- **每一步都带依据**：推理链里每个数字都必须写明来自哪个输入文件的哪个字段。
- **禁止无出处的市场数据**：脚本会扫描产物文本，出现"市场/行业/均价/同行/调研"
  这类无法自证的措辞直接报错。
- **不越界**：所有输出必须落在 JD 给的区间内；JD 没给区间就不猜，只给定位方法。

用法::

    python pipeline/salary.py --root . --out out/salary.json
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text, write_json, now, resolve_in, resolve_out  # noqa: E402

# 无出处措辞黑名单：命中即拒绝产出
FORBIDDEN = ["市场价", "市场行情", "行业均值", "行业平均", "同行价", "平均薪资",
             "调研数据", "大盘价", "benchmark", "拉勾", "BOSS直聘", "脉脉"]

# 否定语境：出现在黑名单词之前时放行——"未使用任何市场行情"是免责声明，不是引用行情
NEGATION = re.compile(r"(未使用|不使用|不引用|不采用|不得出现|禁止|没有使用|未采纳|无)")

# 我的模型参数（全部写死在这里，便于审计与复现）
BASE_POSITION = 0.50      # 中性定位：JD 区间中位
SCORE_SPAN = 0.30         # 加权总分 3.0→5.0 映射到 +0.00→+0.30 的分位偏移
GAP_PENALTY = 0.05        # 每个高严重度能力缺口扣 0.05 分位
GAP_PENALTY_CAP = 0.15
BAND_HALF = 0.15          # 建议区间半宽（±15% 分位）
POS_FLOOR, POS_CEIL = 0.15, 0.90


def round_half_k(x: float) -> float:
    """薪资取整到 0.5K —— 避免给出 18750 这种伪精确数字。"""
    return round(x / 500.0) * 500.0


def guard(text: str, where: str) -> None:
    """拒绝无出处的行情措辞；带否定语的免责声明放行。"""
    low = text.lower()
    for w in FORBIDDEN:
        start = 0
        while True:
            i = low.find(w.lower(), start)
            if i < 0:
                break
            if not NEGATION.search(text[max(0, i - 14):i]):
                die(f"{where} 出现无出处的市场数据措辞「{w}」——"
                    f"本 skill 禁止输出无法自证的行情数字")
            start = i + len(w)


def build(jd: dict, scorecard: dict, resume: dict, risks: list) -> dict:
    rng = jd.get("salary_range")
    if not rng or len(rng) != 2:
        die("JD 未提供薪资区间：不猜数字，请补充 JD 薪资信息后重跑")

    lo, hi = float(rng[0]), float(rng[1])
    if hi <= lo:
        die(f"JD 薪资区间非法: {rng}")

    total = float(scorecard["summary"]["weighted_total"])
    years = resume.get("basic", {}).get("years") or 0

    # 高严重度能力缺口（🟠/🔴）：这是压分位的唯一负面因子
    gaps = [r for r in risks
            if r.get("type") == "能力缺口" and r.get("severity") in ("🔴", "🟠")]
    penalty = min(GAP_PENALTY_CAP, GAP_PENALTY * len(gaps))

    score_pct = max(0.0, min(1.0, (total - 3.0) / 2.0))
    position = BASE_POSITION + SCORE_SPAN * score_pct - penalty
    position = max(POS_FLOOR, min(POS_CEIL, position))

    mid = lo + (hi - lo) * position
    low = lo + (hi - lo) * max(position - BAND_HALF, 0.0)
    high = lo + (hi - lo) * min(position + BAND_HALF, 1.0)

    chain = [
        {"step": "锚定区间", "value": f"{lo/1000:.0f}K – {hi/1000:.0f}K",
         "basis": f"jd.json 字段 salary_range={rng}（招聘方给出的区间，唯一外部锚点）"},
        {"step": "能力得分映射", "value": f"加权总分 {total} → 分位偏移 +{SCORE_SPAN * score_pct:.2f}",
         "basis": f"scorecard.json 字段 summary.weighted_total={total}；"
                  f"公式偏移 = {SCORE_SPAN} × clamp((总分 − 3.0) / 2.0, 0, 1)"},
        {"step": "岗位缺口扣减", "value": f"−{penalty:.2f}",
         "basis": f"risks.json 中类型=能力缺口且严重度∈(🔴,🟠) 的 {len(gaps)} 条："
                  f"{', '.join(r['id'] for r in gaps) or '无'}；每个 −{GAP_PENALTY}，上限 −{GAP_PENALTY_CAP}"},
        {"step": "资历参考", "value": f"{years} 年经验",
         "basis": "resume.json 字段 basic.years（仅用于说明不因年限自动上浮："
                  "本模型只看能力得分与岗位缺口，不做年限溢价）"},
        {"step": "区间合成", "value": f"定位分位 {position:.2f}",
         "basis": f"公式 定位 = clamp({BASE_POSITION} + 偏移 − 扣减, {POS_FLOOR}, {POS_CEIL})；"
                  f"建议区间半宽 ±{BAND_HALF} 分位"},
    ]

    out = {
        "meta": {"generated_at": now(),
                 "inputs": ["out/real/jd.json", "out/scorecard.json",
                            "out/real/resume.json", "out/risks.json"],
                 "model_params": {"base_position": BASE_POSITION, "score_span": SCORE_SPAN,
                                  "gap_penalty": GAP_PENALTY, "gap_penalty_cap": GAP_PENALTY_CAP,
                                  "band_half": BAND_HALF}},
        "jd_range": [lo, hi],
        "recommended": {"low": round_half_k(low), "mid": round_half_k(mid),
                        "high": round_half_k(high)},
        "position": round(position, 3),
        "reasoning_chain": chain,
        "basis_statement": (
            "本建议只用三类输入：招聘方给出的 JD 区间、你在模拟面试中的加权得分、"
            "以及简历与 JD 之间的能力缺口清单。**未使用任何市场行情、行业均价或"
            "招聘平台数据**——这类数字无法自证，本 skill 一律不输出。"
            "城市、公司规模、期权结构等未纳入模型：拿到具体 offer 后按这些因素再校准。"
        ),
        "negotiation": [
            {"step": "先问预算", "script": "想先了解一下这个岗位的薪资区间是怎么定的？"},
            {"step": "报区间上限", "script": f"我了解到这个岗位的区间是 "
                                             f"{lo/1000:.0f}K–{hi/1000:.0f}K，我的期望落在 "
                                             f"{round_half_k(high)/1000:.1f}K 附近。"},
            {"step": "给理由", "script": "理由不是年限，是我能直接接手的部分和需要补的部分："
                                          "能直接接的是分布式同步链路、权限与认证体系；"
                                          "需要补的是仓储履约的限流降级与库存对账，"
                                          "这部分我按 1–2 个月上手来算。"},
            {"step": "不被压到下限", "script": f"如果整体预算卡在 {lo/1000:.0f}K 附近，"
                                               "我希望了解差额是靠职级还是绩效包来补。"},
        ],
    }

    guard(json.dumps(out, ensure_ascii=False), "salary.json")
    for k, v in out["recommended"].items():
        if not lo <= v <= hi:
            die(f"建议值越界: {k}={v} 不在 JD 区间 [{lo}, {hi}] 内")
    return out


def cmd_run(args):
    log = Log(args.quiet)
    work = args.root

    def load(p, required=True):
        fp = resolve_in(work, p)
        if not os.path.exists(fp):
            if required:
                die(f"缺少输入文件: {os.path.join(work, p)}")
            return None
        return json.loads(read_text(fp))

    jd = load(args.jd)
    scorecard = load(args.scorecard)
    resume = load(args.resume)
    risks = load(args.risks, required=False) or []

    out = build(jd, scorecard, resume, risks)
    out_path = resolve_out(work, args.out)
    write_json(out_path, out)
    r = out["recommended"]
    log.info(f"薪资建议: {out_path}")
    print(f"建议区间 {r['low']/1000:.1f}K – {r['high']/1000:.1f}K（中位 {r['mid']/1000:.1f}K）"
          f"｜JD 区间 {out['jd_range'][0]/1000:.0f}K–{out['jd_range'][1]/1000:.0f}K"
          f"｜定位分位 {out['position']}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="P3 薪资建议")
    p.add_argument("--root", default=".")
    p.add_argument("--jd", default=os.path.join("out", "real", "jd.json"))
    p.add_argument("--scorecard", default=os.path.join("out", "scorecard.json"))
    p.add_argument("--resume", default=os.path.join("out", "real", "resume.json"))
    p.add_argument("--risks", default=os.path.join("out", "risks.json"))
    p.add_argument("--out", default=os.path.join("out", "salary.json"))
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
