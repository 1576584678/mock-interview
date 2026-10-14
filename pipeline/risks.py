#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""风险点种子（P2 追问目标 / P3 风险报告共用）。

设计原则
--------
1. **编号稳定**：R-01… 按（严重度，类型权重，出现顺序）排序后一次性分配，
   P2 的追问目标与 P3 的风险报告沿用同一批编号，`docs/OUTPUT_CONTRACT.md`
   要求的「编号引用闭环」才有物理基础。
2. **只做保守推断**：这里产出的全部是「待验证」项，不是「有罪推定」。
   比如「口径缺失」只说明该数字缺分子分母，不代表数字是假的——
   这正是 `docs/RISKS.md` 里「无法验证 ≠ 编造」的落地。
3. **可解释**：每条都带 `original`（原文）、`why`（为什么会被问倒）、
   `probe_hint`（面试官该往哪追问），P3 直接复用，不重复造轮子。
"""

from __future__ import annotations

import re

from common import as_text

# 严重度排序用
SEV_RANK = {"🔴": 0, "🟠": 1, "🟡": 2, "⚪": 3}

# JD 里出现、但简历可能没有对等证据的技术名词 —— 这些是最值钱的攻击点。
# 前面的比后面的更"技术具体"，同分时优先。
GAP_TOKENS = [
    "熔断", "限流", "降级", "TiDB", "MyCAT", "RocketMQ", "Elasticsearch",
    "分库分表", "读写分离", "JVM", "多线程", "WMS", "TMS",
    "供应链", "履约", "库存", "对账",
]
# 偏"业务名词"的缺口，属于加分/软性话题，不当硬性缺口处理
SOFT_TOKENS = {"供应链", "履约", "库存", "对账"}

# 每类风险进入清单的上限：保证清单"荤素搭配"，
# 不会出现 12 条全被同一类（比如口径缺失）占满的情况。
CAPS = {"硬性门槛": 2, "时间线": 2, "能力缺口": 3, "口径缺失": 4, "夸大词": 2, "机制存疑": 2}

# 学历台阶：JD 要求本科而简历最高学历低于本科时，属于 HR 初筛硬门槛
EDU_ORDER = ["博士", "硕士", "本科", "大专", "专科", "高中", "中专"]

# 高危夸大词：出现这些词就必须能拿出对应深度的证据
HEAVY_TOPICS = [
    "JVM", "并发", "源码", "分布式事务", "分库分表", "架构", "调优", "内核",
]


def _blob(resume: dict) -> str:
    """把简历拼成一整块文本，用于证据检索。"""
    parts = [as_text(resume.get("summary"))]
    for grp in resume.get("skills", []):
        parts.append(as_text(grp.get("category")))
        parts.append(as_text(grp.get("items")))
        parts.append(as_text(grp.get("proficiency_claims")))
    for e in resume.get("experiences", []):
        parts.append(as_text(e.get("company")))
        parts.append(as_text(e.get("title")))
        parts.append(as_text(e.get("stack")))
        parts.append(as_text(e.get("highlights")))
    for p in resume.get("projects", []):
        parts.append(as_text(p.get("name")))
        parts.append(as_text(p.get("stack")))
        parts.append(as_text(p.get("highlights")))
    return " ".join(x for x in parts if x)


def _mk(rid, rtype, severity, verifiability, original, why, probe_hint,
        source="resume"):
    return {
        "id": rid,
        "type": rtype,
        "severity": severity,
        "verifiability": verifiability,
        "original": original.strip(),
        "why": why,
        "probe_hint": probe_hint,
        "source": source,
    }


def _jd_years(jd: dict) -> tuple:
    """从 JD 文本里取年限门槛：返回 (最小值, 最大值或 None)。"""
    texts = [as_text(x.get("text") if isinstance(x, dict) else x)
             for x in (jd.get("must_have", []) + jd.get("duties", []) + jd.get("nice_to_have", []))]
    for t in texts:
        m = re.search(r"(\d{1,2})\s*(?:[-–~至]|到)\s*(\d{1,2})\s*年", t)
        if m:
            return int(m.group(1)), int(m.group(2))
    for t in texts:
        m = re.search(r"(\d{1,2})\s*年(?:以上|及以上|\+)", t)
        if m:
            return int(m.group(1)), None
    return None, None


def _resume_edu(resume: dict) -> str:
    blob = as_text(resume.get("education"))
    for d in EDU_ORDER:
        if d in blob:
            return d
    return ""


def seed_risks(resume: dict, jd: dict, max_risks: int = 12) -> list:
    """从 resume.json / jd.json 推导待验证风险点，返回带 R-xx 编号的清单。"""
    blob = _blob(resume).lower()
    groups = {"硬性门槛": [], "时间线": [], "口径缺失": [], "能力缺口": [],
              "夸大词": [], "机制存疑": []}

    # 0) 硬性门槛：年限 / 学历不满足 JD 的硬条件 —— HR 初筛即可能被卡，
    #    这类问题与"能力"无关，属于**门槛**，所以单独成类且给最高严重度。
    min_years, _max_years = _jd_years(jd)
    years = (resume.get("basic") or {}).get("years")
    if min_years and isinstance(years, (int, float)) and years < min_years:
        groups["硬性门槛"].append(_mk(
            None, "硬性门槛", "🔴", "可验证",
            f"JD 要求 {min_years} 年以上经验，简历自述 {years} 年",
            f"JD 的年限是硬条件（{min_years} 年以上），简历只有 {years} 年，"
            "很可能在 HR 初筛阶段就被过滤，面试机会本身受影响",
            f"如何论证「年限不足但能力对等」：拿出与岗位最相关的产出与独立负责范围",
        ))
    jd_blob = as_text([jd.get("must_have"), jd.get("duties")])
    if "本科" in jd_blob:
        edu = _resume_edu(resume)
        if edu and EDU_ORDER.index(edu) > EDU_ORDER.index("本科"):
            groups["硬性门槛"].append(_mk(
                None, "硬性门槛", "🔴", "可验证",
                f"JD 要求本科及以上，简历最高学历为{edu}",
                "JD 明确写了本科及以上，学历是硬条件，越过它需要额外说明（如在职学历、作品与产出）",
                "学历不满足时的应对：突出可验证产出，必要时说明学历提升计划",
            ))

    # 1) 时间线空窗：必被追问，且简历通常没写原因
    for g in resume.get("timeline_gaps", []):
        months = g.get("months", 0)
        groups["时间线"].append(_mk(
            None, "时间线", "🟠" if months >= 3 else "🟡", "可验证",
            f"{g.get('from')} → {g.get('to')}（约 {months} 个月空窗）",
            "空窗期是面试官必问项，简历未说明原因与期间产出，容易被反复盘问动机",
            "空窗期的原因、期间的产出与求职动机",
        ))

    # 2) 口径缺失：数字没有分子分母。带「%且≥90」的是招牌数字，优先
    seen_q = set()
    for c in resume.get("claims", {}).get("quantitative", []):
        if c.get("has_definition"):
            continue
        text = as_text(c.get("text")).strip()
        if len(text) < 8 or text in seen_q:
            continue
        seen_q.add(text)
        unit = as_text(c.get("unit"))
        val = c.get("value")
        headline = ("%" in unit) and isinstance(val, (int, float)) and val >= 90
        groups["口径缺失"].append((0 if headline else 1, _mk(
            None, "口径缺失", "🟠" if headline else "🟡", "可验证",
            text,
            "只有结果数字没有口径，面试官一问分子分母就答不上，等于自曝注水",
            f"该数字（{val}{unit}）的分子、分母与统计周期",
        )))
    groups["口径缺失"].sort(key=lambda x: x[0])
    groups["口径缺失"] = [x[1] for x in groups["口径缺失"]]

    # 3) 能力缺口：JD 要求了、简历里检索不到对等证据（越"技术具体"越优先）
    seen_gap = set()
    for tag, items in (("硬性要求", jd.get("must_have", [])),
                       ("加分项", jd.get("nice_to_have", []))):
        for it in items:
            text = as_text(it.get("text") if isinstance(it, dict) else it)
            for rank, tok in enumerate(GAP_TOKENS):
                if tok in text and tok.lower() not in blob and (tok, text[:30]) not in seen_gap:
                    seen_gap.add((tok, text[:30]))
                    soft = (tok in SOFT_TOKENS) or tag != "硬性要求"
                    groups["能力缺口"].append((rank, _mk(
                        None, "能力缺口", "⚪" if soft else "🟠", "仅叙述",
                        f"JD {tag}提到「{tok}」：{text}",
                        f"JD 明确要求「{tok}」，简历中检索不到对等经历，面试官会验证是否有迁移能力",
                        f"没有 {tok} 经验时，用相邻能力做迁移论证（说清哪些能做、哪些是盲区）",
                        source="jd",
                    )))
    groups["能力缺口"].sort(key=lambda x: x[0])
    groups["能力缺口"] = [x[1] for x in groups["能力缺口"]]

    # 4) 高危夸大词：精通/深入理解 + 硬核主题
    seen_topic = set()
    for grp in resume.get("skills", []):
        for claim in grp.get("proficiency_claims", []):
            topic = re.sub(r"^(精通|熟练|熟悉|掌握|了解|深入理解)", "", as_text(claim)).strip()
            if not any(h in topic for h in HEAVY_TOPICS):
                continue
            key = topic[:12]
            if key in seen_topic:
                continue
            seen_topic.add(key)
            groups["夸大词"].append(_mk(
                None, "夸大词", "🟡", "可佐证", as_text(claim),
                f"简历声称「{claim}」，面试官会用具体案例与数据验证深度，答不出反而扣分",
                f"{topic} 的一个具体案例：问题现象 → 定位手段 → 改动 → 数据结果",
            ))

    # 5) 机制存疑：讲了机制但需要压强验证
    seen_kw = set()
    for c in resume.get("claims", {}).get("mechanism", []):
        kw = as_text(c.get("keyword"))
        text = as_text(c.get("text")).strip()
        # 纯标题/冒号结尾的碎片（如「后端架构：」）没有可追问的信息量
        if not kw or kw in seen_kw or len(text) < 15 or text.endswith(("：", ":")):
            continue
        seen_kw.add(kw)
        groups["机制存疑"].append(_mk(
            None, "机制存疑", "🟡", "可验证", as_text(c.get("text")),
            f"简历提到「{kw}」，但机制类表述必须能讲清失败路径与代价，否则像背概念",
            f"{kw} 的失败路径：什么情况下会失效、失效后怎么发现、怎么补救",
        ))

    # 按类别上限收集，再按严重度稳定排序（同严重度保持类内优先顺序）
    picked = []
    for gname in ("硬性门槛", "时间线", "口径缺失", "能力缺口", "夸大词", "机制存疑"):
        picked.extend(groups[gname][:CAPS[gname]])
    picked.sort(key=lambda r: SEV_RANK.get(r["severity"], 9))
    picked = picked[:max_risks]
    for i, r in enumerate(picked, 1):
        r["id"] = f"R-{i:02d}"
    return picked


def severities_of(risks: list) -> dict:
    out = {}
    for r in risks:
        out[r["severity"]] = out.get(r["severity"], 0) + 1
    return out


if __name__ == "__main__":
    import io
    import json
    import sys

    p = sys.argv[1] if len(sys.argv) > 1 else "out/real/resume.json"
    q = sys.argv[2] if len(sys.argv) > 2 else "out/real/jd.json"
    res = json.load(io.open(p, encoding="utf-8"))
    jd = json.load(io.open(q, encoding="utf-8"))
    rs = seed_risks(res, jd)
    for r in rs:
        print(f"{r['id']} [{r['severity']} {r['type']}] {r['original'][:64]}")
    print(f"共 {len(rs)} 条", severities_of(rs))
