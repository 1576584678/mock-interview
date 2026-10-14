#!/usr/bin/env python3
"""简历 → resume.json

规则优先（确定性抽取）；语义类字段（岗位定位、软性描述）留给 agent 后续增强。

用法:
  python parse_resume.py --in 简历.pdf --out resume.json [--text-out resume.txt]

核心产出是 claims 清单：
  claims.quantitative —— 量化声明（供 P4 验真：150 次提交 / 111 个单测 / 99.98%）
  claims.mechanism    —— 机制声明（供 P4 验真：三级幂等键 / 墓碑补拉 / 业务指纹）
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Log, base_parser, check_input, die, now, write_json, write_text  # noqa: E402
from extract_text import extract  # noqa: E402

# ------------------------------------------------------------------ 章节识别

SECTION_ALIASES = {
    "basic": ["个人信息", "基本信息", "联系方式", "个人资料", "求职意向"],
    "summary": ["个人优势", "核心优势", "自我评价", "个人总结", "个人简介", "技术优势", "个人亮点"],
    "skills": ["专业技能", "技能清单", "技术栈", "技能特长", "掌握技能", "技能"],
    "experience": ["工作经历", "工作经验", "职业经历", "工作履历", "工作经验与项目"],
    "projects": ["项目经历", "项目经验", "项目实践", "主要项目", "项目"],
    "opensource": ["开源项目", "开源与独立产品", "独立产品", "个人作品", "作品集", "开源贡献", "个人项目"],
    "education": ["教育背景", "教育经历", "学历", "教育"],
    "other": ["获奖", "证书", "荣誉", "培训", "语言能力", "兴趣爱好"],
}

_ALIAS_LOOKUP = {}
for _sec, _names in SECTION_ALIASES.items():
    for _n in _names:
        _ALIAS_LOOKUP[_n] = _sec

MARKER_RE = re.compile(r"^[\s>*+·•●○\-—–]+")
PERIOD_RE = re.compile(
    r"(20\d{2})\s*[.\-/年]?\s*(\d{1,2})?\s*月?\s*[-~—–?至到]{1,2}\s*"
    r"(?:(20\d{2})\s*[.\-/年]?\s*(\d{1,2})?\s*月?|(至今|今|present|now|Present))",
    re.I,
)
QUANT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|％|次|个|项|条|家|份|万条|万|亿|ms|毫秒|秒|分钟|小时|天|QPS|qps|倍|页|人|个月)"
)
# 「这个数字有口径」的判据：分子分母、统计窗口、数据来源、对比基准……
# 注意：只认**能自证统计方式**的词，不认「定义」这种宽泛词（"产品定义"会误伤）。
DEF_RE = re.compile(
    r"口径|分子|分母|定义为|计算方式|统计(?:窗口|周期|口径|来源|范围)|"
    r"取自|来源是|依据|基准|埋点|监控数据|压测|采样|样本|环比|同比|对比|"
    r"P9\d|P95|中位数|均值|阈值|目标是|以[^。；\n]{0,12}为(?:准|口径)"
)

# 前缀型量化声明：QPS 5000 / TPS 2000 / 日均 10 万条
PREFIX_QUANT_RE = re.compile(
    r"(QPS|qps|TPS|tps|PV|UV|DAU|MAU|日均|峰值)\s*[:：]?\s*(\d+(?:[.,]\d+)?)\s*(万条|万|亿|条|次)?"
)
MECH_NOUN = r"(机制|策略|模型|算法|架构|框架|体系|链路|方案|模式|组件)"
MECH_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9+/,、\s]{2,26}?)" + MECH_NOUN)
MECH_VERB_RE = re.compile(r"(引入|设计|实现|采用|自研|落地|搭建|构建|建立|通过|重构)")
PROF_RE = re.compile(r"(精通|熟练|熟悉|掌握|了解|深耕|具备)[：:、\s]*([^，,；;。\n]{1,20})")
EDU_RE = re.compile(r"(本科|硕士|博士|研究生|大专|大学|学院|MBA|学士)")


def strip_marker(s: str) -> str:
    """去掉列表符号与 Markdown 标题符。

    注意：'#' 只在这里剥离，**不算列表符号**——否则 "## 工作经历" 会被
    误判为列表项，章节识别会整体失效（见 looks_like_bullet）。
    """
    return MARKER_RE.sub("", s).strip().lstrip("#＃").strip()


def is_heading(s: str) -> bool:
    t = strip_marker(s).strip("#＃ ").strip()
    if not t or len(t) > 14:
        return False
    if t in _ALIAS_LOOKUP:
        return True
    return any(t == n or t.startswith(n) for n in _ALIAS_LOOKUP)


def section_of(s: str):
    """章节判定。

    bug 教训：is_heading 允许前缀匹配（「项目经历 · 商业项目」算标题），
    而 section_of 只做精确匹配，两者口径不一致会让这类标题被判成正文，
    整段项目经历因此被并进「工作经历」。这里必须与 is_heading 用同一口径。
    """
    t = strip_marker(s).strip("#＃ ").strip()
    if not t or len(t) > 14:
        return None
    if t in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[t]
    for n, sec in _ALIAS_LOOKUP.items():
        if t.startswith(n):
            return sec
    return None


def looks_like_bullet(s: str) -> bool:
    return bool(re.match(r"^[\s>*+·•●○\-—–]+", s))


def should_merge(prev: str, s: str) -> bool:
    """判断 s 是否是 prev 的续行（PDF 会在右边界把一句话截断）。

    曾经的 bug：无条件合并短行，导致「李岳临」与下一行标题被粘成一行，
    姓名抽取因此抓到后文无关的二字词。所以这里必须**保守**：
    只有出现明确的续行信号才合并。
    """
    if not s:
        return False
    # 上一行是章节标题时，绝不能被下一行吸收（否则标题被污染，章节识别整体失效）
    if is_heading(prev):
        return False
    # 「在职」「离职」是状态词，必须独立成行，否则会粘进公司名
    if s in STATUS_WORDS:
        return False
    if looks_like_bullet(s) or is_heading(s):
        return False
    if PERIOD_RE.search(s):
        return False
    # 含分隔符的行是结构化行（职位行、技能行），不是续行
    if "｜" in s or "|" in s:
        return False
    if re.search(r"[。！？；;:：)）]$", prev):
        return False
    if len(s) > 30:
        return False
    # 明确的续行信号，从严：
    #   1) 上一行以空格收尾（右边界截断的典型信号）
    #   2) 本行是 1-2 字的碎片（如「索」「5」）
    #   3) 上一行足够长（≥34），说明是被右边界截断的正文
    # 注意：早期版本的「本行 ≤ 12 字就合并」过于宽松，会把「后端框架」
    # 这类技能标签吸进上一行内容里，导致技能解析错位。
    return prev.endswith(" ") or len(s) <= 2 or len(prev) >= 34


def merge_continuation(lines):
    out = []
    for raw in lines:
        s = raw.rstrip()
        if not s.strip():
            out.append("")
            continue
        if not out or not out[-1]:
            out.append(s.strip())
            continue
        s_stripped = s.strip()
        if should_merge(out[-1], s_stripped):
            out[-1] = out[-1] + s_stripped
        else:
            out.append(s_stripped)
    return out


def split_sections(lines):
    """按章节标题切分，返回 {section: [lines]}。无标题时全部归入 'body'。"""
    sections = {}
    cur = "body"
    for ln in lines:
        if not ln.strip():
            continue
        sec = section_of(ln)
        if sec and not looks_like_bullet(ln):
            cur = sec
            sections.setdefault(cur, [])
            continue
        sections.setdefault(cur, []).append(ln)
    return sections


# ------------------------------------------------------------------ 字段抽取

def parse_basic(lines, full_text):
    basic = {"name": None, "title": None, "years": None, "phone": None,
             "email": None, "github": None, "wechat": None, "city": None}

    # 姓名显式声明优先：姓名：张三 / 姓名 张三
    m = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5]{2,4})", full_text)
    if m:
        basic["name"] = m.group(1)

    def _split_head(t):
        """把 `张三 · 后端开发工程师` 这类标题行拆成 (姓名, 职位)。"""
        parts = [p.strip() for p in re.split(r"[·|｜/\\,，\-—–:：]+|\s{2,}", t) if p.strip()]
        name = next((p for p in parts if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", p)
                     and p not in _ALIAS_LOOKUP), None)
        rest = " ".join(p for p in parts if p != name)
        return name, (rest[:40] if rest else None)

    for ln in lines[:12]:
        t = strip_marker(ln)
        if not t:
            continue
        # 头部到第一个章节标题为止（姓名不会出现在"求职意向"之后）
        if t in _ALIAS_LOOKUP:
            break
        if basic["name"] is None:
            n, rest = _split_head(t)
            if n:
                basic["name"] = n
                if basic["title"] is None and rest:
                    basic["title"] = rest
        if basic["name"] is None and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t) \
                and t not in _ALIAS_LOOKUP:
            basic["name"] = t
        if basic["title"] is None and re.search(
            r"(工程师|开发|架构师|技术专家|负责人|经理|主管)", t
        ) and len(t) <= 40:
            basic["title"] = t
        if basic["name"] and basic["title"]:
            break

    m = re.search(r"1[3-9]\d{9}", full_text)
    basic["phone"] = m.group(0) if m else None
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", full_text)
    basic["email"] = m.group(0) if m else None
    m = re.search(r"github\.com/[\w.-]+", full_text, re.I)
    basic["github"] = m.group(0) if m else None
    m = re.search(r"(?:微信|weixin|wechat)\s*[:：]?\s*([\w.-]{3,30})", full_text, re.I)
    basic["wechat"] = m.group(1) if m else None
    m = re.search(r"(\d{1,2})\s*年(?:以上)?[^。\n]{0,12}经验", full_text)
    if not m:
        m = re.search(r"工作\s*(\d{1,2})\s*年", full_text)
    if not m:
        m = re.search(r"([一二三四五六七八九十]+)\s*年[^。\n]{0,12}经验", full_text)
    if m:
        v = m.group(1)
        if v.isdigit():
            basic["years"] = int(v)
    m = re.search(r"(北京|上海|广州|深圳|杭州|成都|武汉|南京|西安|苏州|天津|重庆|长沙)", full_text)
    basic["city"] = m.group(1) if m else None
    return basic


PROF_WORDS = ("精通", "熟练", "熟悉", "掌握", "了解", "深耕")


def _split_category(t: str):
    """兼容两种技能排版：
      同行式： 编程语言：精通 Java；熟练 TypeScript
      两行式： 编程语言
              精通 Java；熟练 TypeScript
    """
    m = re.match(r"^([\u4e00-\u9fa5A-Za-z /]{2,10})\s*[:：]\s*(.+)$", t)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^([\u4e00-\u9fa5A-Za-z]{2,8})\s+([\u4e00-\u9fa5A-Za-z].+)$", t)
    if m:
        cat = m.group(1).strip()
        body = m.group(2).strip()
        # 「AI 工程」这种标签自带空格，不能把空格当分隔符：
        # 只有后半段足够长（像真正的内容）才认作「类别 内容」同行式。
        if not cat.startswith(PROF_WORDS) and len(body) >= 6:
            return cat, body
    return None, t


def _is_short_label(t: str) -> bool:
    """像「编程语言」「缓存 / 消息」这样的短标签（不含内容）。"""
    if not t or len(t) > 12:
        return False
    if re.search(r"[，,；;。！？]", t):
        return False
    if PROF_RE.search(t):
        return False
    return bool(re.match(r"^[\u4e00-\u9fa5A-Za-z /]+$", t))


def parse_skills(lines):
    skills = []
    i = 0
    while i < len(lines):
        t = strip_marker(lines[i])
        if not t:
            i += 1
            continue
        cat, body = _split_category(t)
        if cat is None and _is_short_label(t) and i + 1 < len(lines):
            nxt = strip_marker(lines[i + 1])
            if nxt and not _is_short_label(nxt):
                cat, body = t, nxt
                i += 1  # 消耗掉内容行
        if cat is None:
            i += 1
            continue
        items = [x.strip() for x in re.split(r"[、,，;；]| \| ", body) if x.strip()]
        profs = [f"{a}{b}" for a, b in PROF_RE.findall(body)]
        skills.append({"category": cat, "items": items, "proficiency_claims": profs})
        i += 1
    # 兜底：技能写成整段散文（「精通 A；熟练 B；熟悉 C」）时没有「分类：清单」结构，
    # 上面一条都抽不到——按分隔符切一遍，全部归到一个「专业技能」类里。
    if not skills:
        prose = [strip_marker(l) for l in lines]
        prose = [p for p in prose if p and not _is_short_label(p)]
        blob = " ".join(prose)
        items = [x.strip() for x in re.split(r"[、,，;；]| \| |\s{2,}", blob)
                 if x.strip() and len(x.strip()) <= 40]
        if items:
            skills.append({
                "category": "专业技能",
                "items": items[:40],
                "proficiency_claims": [f"{a}{b}" for a, b in PROF_RE.findall(blob)],
            })
    return skills


def _clean_person(text):
    t = re.sub(r"[（(][^）)]*[）)]", "", text).strip(" ｜|·-—–")
    return t.strip()


STATUS_WORDS = {"在职", "离职", "已离职", "至今", "实习", "兼职"}
TITLE_RE = re.compile(r"(工程师|开发|经理|负责人|主管|架构师|专家|总监)")


def parse_experiences(lines):
    """兼容两种经历排版：
      同行式： 甲公司 ｜ Java 开发工程师 ｜ 2024.03 - 至今
      多行式： 甲公司
              在职
              2024.03 - 至今
              Java 开发工程师 ｜ 企业知识平台核心模块
    """
    exps = []
    consumed = set()
    for idx, ln in enumerate(lines):
        t = strip_marker(ln)
        if not t or idx in consumed:
            continue
        m = PERIOD_RE.search(t)
        if not m:
            continue

        left = _clean_person(t[:m.start()])
        right = _clean_person(t[m.end():])
        company, title = "", ""

        if left:
            parts = [p.strip() for p in re.split(r"[｜|·]", left) if p.strip()]
            company = parts[0] if parts else left
            title = " ".join(parts[1:]) if len(parts) > 1 else ""
            if not title:
                title = right
        else:
            # 日期独占一行：向上找公司，向下找职位
            cands = []  # [(行号, 文本)]
            for j in range(idx - 1, max(-1, idx - 4), -1):
                cand = strip_marker(lines[j])
                if not cand or cand in STATUS_WORDS or is_heading(cand) or j in consumed:
                    continue
                if TITLE_RE.search(cand) and not cands and not title:
                    title = cand
                    continue
                cands.append((j, cand))
            if cands:
                # 公司名被 PDF 拆成两行时（「…（驻场：武汉」+「小米总部）」）拼回去
                if len(cands) >= 2 and re.match(r"^(驻场|（|\()", cands[0][1]):
                    company = f"{cands[1][1]}（{cands[0][1]}）"
                    consumed.update({cands[0][0], cands[1][0]})
                else:
                    company = cands[0][1]
                    consumed.add(cands[0][0])
            if not title:
                for j in range(idx + 1, min(len(lines), idx + 4)):
                    cand = strip_marker(lines[j])
                    if not cand:
                        continue
                    # 「主导…」「负责…」是职责描述，不是岗位名
                    if re.match(r"^(主导|负责|参与|设计|实现|完成|搭建|推动|协助)", cand):
                        continue
                    if TITLE_RE.search(cand):
                        parts = [p.strip() for p in re.split(r"[｜|]", cand) if p.strip()]
                        title = next((p for p in parts if TITLE_RE.search(p)), cand)
                        consumed.add(j)
                        break

        cur = {
            "company": company,
            "title": title,
            "start": f"{m.group(1)}.{int(m.group(2)):02d}" if m.group(2) else m.group(1),
            "end": (f"{m.group(3)}.{int(m.group(4)):02d}"
                    if m.group(3) and m.group(4) else (m.group(3) or m.group(5) or "")),
            "raw": t,
            "stack": [],
            "highlights": [],
        }

        # 收集随后的要点行，直到下一个日期行
        k = idx + 1
        while k < len(lines):
            if k in consumed:
                k += 1
                continue
            nxt = strip_marker(lines[k])
            if not nxt:
                k += 1
                continue
            if PERIOD_RE.search(nxt) or is_heading(nxt):
                break
            if re.search(r"(Spring|Java|Redis|Kafka|MySQL|Docker|Dubbo|Nacos|Vue|React|Python)",
                         nxt) and len(nxt) < 120 and not cur["stack"]:
                cur["stack"] = [x.strip() for x in re.split(r"[·、,，|/]", nxt) if x.strip()]
            else:
                cur["highlights"].append(nxt)
            k += 1
        exps.append(cur)
    return exps


PROJECT_KEY = re.compile(r"(平台|系统|中台|项目|应用|引擎|产品|工具|组件|服务)")


def parse_projects(lines):
    """项目标题判定要保守。

    bug 教训：PDF 文本没有列表符号，若把「所有 ≤40 字的非符号行」都当项目标题，
    真实简历会被炸成 120 个"项目"（含「设计」「模块」这类正文片段）。
    因此只认包含平台/系统/中台…等项目类关键词的短行。
    """
    prjs = []
    cur = None
    for ln in lines:
        t = strip_marker(ln)
        if not t:
            continue
        is_title = (
            not looks_like_bullet(ln)
            and 4 <= len(t) <= 45
            and bool(PROJECT_KEY.search(t))
            and not PERIOD_RE.search(t)
            # 小标题（以冒号结尾）、正文片段（含逗号/顿号）、章节标题都要排除
            and not t.endswith(("：", ":", "。", "，", "、", "；", ";"))
            and not t.startswith(("，", "。", "、", "；", "）", ")"))
            and not re.search(r"[，,、；;]", t)
            and section_of(t) is None
            # 超过 14 字的章节式子标题（「项目经历 · 独立产品…」）也要排除
            and not any(t.startswith(n) for n in _ALIAS_LOOKUP)
        )
        if is_title:
            cur = {"name": t, "period": "", "role": "", "stack": [], "highlights": []}
            prjs.append(cur)
            continue
        if cur is None:
            continue  # 标题之前的内容不计入项目
        m = PERIOD_RE.search(t)
        if m and not cur["period"] and len(t) <= 30:
            cur["period"] = m.group(0).strip()
            continue
        if re.search(r"(Spring|Java|Redis|Kafka|MySQL|Docker|Dubbo|Nacos|Vue|React|Python|Next\.js)",
                     t) and len(t) < 140 and not cur["stack"]:
            cur["stack"] = [x.strip() for x in re.split(r"[·、,，|/]", t) if x.strip()]
            continue
        cur["highlights"].append(t)
    return [p for p in prjs if p["highlights"] or p["name"]][:20]


def parse_education(lines):
    return [strip_marker(x) for x in lines if EDU_RE.search(x)]


def _ym(s: str):
    m = re.match(r"(20\d{2})(?:[.\-/](\d{1,2}))?", s or "")
    if not m:
        return None
    return int(m.group(1)) * 12 + (int(m.group(2)) - 1 if m.group(2) else 0)


def timeline_gaps(experiences):
    spans = []
    for e in experiences:
        a = _ym(e.get("start", ""))
        end = e.get("end", "") or ""
        b = _ym(end) if re.search(r"20\d{2}", end) else 9999 * 12
        if a is not None:
            spans.append((a, b, e))
    spans.sort(key=lambda x: x[0])
    gaps = []
    for i in range(1, len(spans)):
        prev_end = spans[i - 1][1]
        cur_start = spans[i][0]
        months = cur_start - prev_end
        if months >= 2:
            gaps.append({
                "from": spans[i - 1][1] == 9999 * 12 and "至今" or
                        f"{prev_end // 12}.{prev_end % 12 + 1:02d}",
                "to": f"{cur_start // 12}.{cur_start % 12 + 1:02d}",
                "months": months,
                "before_company": spans[i][2].get("company", ""),
                "after_company": spans[i - 1][2].get("company", ""),
            })
    return gaps


def extract_claims(text):
    sents = [s.strip() for s in re.split(r"(?<=[。；;!?！？\n])", text) if s.strip()]

    # PDF 常在数字处断行了（「……准确率达」+「99.98%。」）。这类碎片并回上一句，
    # 否则 claims 里会出现「99.98%。」这种没有上下文的孤立条目，
    # 面试官和验真层都没法用。
    merged = []
    for s in sents:
        if (merged and len(s) <= 14 and re.match(r"^[\d.]+", s)
                and not re.search(r"[\u4e00-\u9fa5]{4,}", s)):
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    sents = merged

    quant, mech, seen_q, seen_m = [], [], set(), set()

    for s in sents:
        if len(s) > 200:
            continue
        for m in QUANT_RE.finditer(s):
            # 去重键包含匹配到的数字本身：同一句里的多个数字都要保留
            key = f"{s[:60]}|{m.group(0)}"
            if key in seen_q:
                continue
            seen_q.add(key)
            quant.append({
                "id": "",
                "text": s,
                "value": float(m.group(1)) if "." in m.group(1) else int(m.group(1)),
                "unit": m.group(2),
                "has_definition": bool(DEF_RE.search(s)),
            })
        for m in PREFIX_QUANT_RE.finditer(s):
            key = f"{s[:60]}|{m.group(0)}"
            if key in seen_q:
                continue
            seen_q.add(key)
            quant.append({
                "id": "",
                "text": s,
                "value": float(m.group(2).replace(",", "")) if "." in m.group(2) else int(m.group(2).replace(",", "")),
                "unit": (m.group(1) + (m.group(3) or "")),
                "has_definition": bool(DEF_RE.search(s)),
            })
        if MECH_RE.search(s):
            mm = MECH_RE.search(s)
            keyword = MECH_VERB_RE.sub("", mm.group(1).strip()).strip(" 的")[:24]
            key = f"{s[:60]}|{keyword}"
            if key in seen_m:
                continue
            seen_m.add(key)
            mech.append({
                "id": "",
                "text": s,
                "keyword": keyword,
                "mechanism_type": mm.group(2),
            })

    for i, c in enumerate(quant, 1):
        c["id"] = f"C-Q{i:02d}"
    for i, c in enumerate(mech, 1):
        c["id"] = f"C-M{i:02d}"
    return {"quantitative": quant, "mechanism": mech}


# ------------------------------------------------------------------ 主流程

def merge_skills_lines(ls):
    """技能段专用合并：只把 1-3 字的碎片并回上一行。

    例如 PDF 把类别「中间件 / 检索」断成「中间件 / 检」+「索」。
    除此之外技能段不做任何合并（否则类别行会被吸进上一行内容）。
    """
    out = []
    for raw in ls:
        t = raw.strip()
        if not t:
            continue
        if out and len(t) <= 3 and _is_short_label(out[-1]) and not looks_like_bullet(raw):
            out[-1] = out[-1] + t
        else:
            out.append(t)
    return out


def parse(text: str, source: str, method: str, warnings):
    raw_lines = [l.rstrip() for l in text.splitlines()]

    # 先切章节（不合并），再按段决定是否合并：
    #   技能段是「类别行 + 内容行」的两行式结构，任何合并都会把类别吸进上一行内容，
    #   所以技能段一律不合并。这也是早期版本技能只认出 3 组的根因。
    raw_sections = split_sections(raw_lines)
    sections = {}
    for sec, ls in raw_sections.items():
        if sec == "skills":
            sections[sec] = merge_skills_lines(ls)
        else:
            sections[sec] = merge_continuation(ls)

    lines = merge_continuation(raw_lines)  # 全文合并版：供 basic 与 claims 使用
    full_text = "\n".join(lines)

    body = sections.get("body", [])
    basic = parse_basic(body + lines[:6], full_text)

    skills = parse_skills(sections.get("skills", []))
    if not skills:  # 无技能章节时从全文降级抽取
        skills = parse_skills([l for l in lines if PROF_RE.search(l)][:20])
        if skills:
            warnings.append("未识别到「专业技能」章节，已从全文降级抽取")

    exps = parse_experiences(sections.get("experience", []))
    if not exps:  # 无章节标题时按时间行降级
        exps = parse_experiences([l for l in lines if PERIOD_RE.search(l)])
        if exps:
            warnings.append("未识别到「工作经历」章节标题，已按时间行降级抽取")

    projects = parse_projects(sections.get("projects", []))
    education = parse_education(sections.get("education", []))
    claims = extract_claims(full_text)
    gaps = timeline_gaps(exps)

    if not exps:
        warnings.append("未抽取到任何工作经历，请人工核对")
    if not claims["quantitative"]:
        warnings.append("未抽取到量化声明，P4 验真层将无输入")
    if len(text) < 200:
        warnings.append("文本过短，抽取结果可能不完整")

    return {
        "basic": basic,
        "summary": [strip_marker(x) for x in sections.get("summary", []) if x.strip()],
        "skills": skills,
        "experiences": exps,
        "projects": projects,
        "education": education,
        "opensource": [strip_marker(x) for x in sections.get("opensource", []) if x.strip()],
        "timeline_gaps": gaps,
        "claims": claims,
        "_meta": {
            "source": os.path.abspath(source),
            "extractor": method,
            "generated_at": now(),
            "chars": len(text),
            "sections_found": sorted(sections.keys()),
            "warnings": warnings,
        },
    }


def main():
    ap = base_parser("简历 → resume.json")
    ap.add_argument("--text-out", dest="text_out", default=None,
                    help="同时导出抽取到的纯文本（便于人工核对）")
    args = ap.parse_args()

    check_input(args.inp)
    log = Log(args.quiet)
    log.step(f"解析简历: {args.inp}")

    if args.dry_run:
        print(f"[dry-run] 将解析 {args.inp} → {args.out}")
        return

    r = extract(args.inp, log)
    warnings = list(r.get("warnings") or [])
    data = parse(r["text"], args.inp, r["method"], warnings)

    if args.text_out:
        write_text(args.text_out, r["text"])
    write_json(args.out, data)

    b = data["basic"]
    log.info(
        f"姓名={b['name']} 年限={b['years']} 经历={len(data['experiences'])} "
        f"项目={len(data['projects'])} 技能组={len(data['skills'])} "
        f"空窗={len(data['timeline_gaps'])} "
        f"量化声明={len(data['claims']['quantitative'])} "
        f"机制声明={len(data['claims']['mechanism'])}"
    )
    for w in data["_meta"]["warnings"]:
        log.warn(w)
    if not data["experiences"]:
        die("未能抽取到工作经历，请检查简历结构或改用 --text-out 人工核对", 2)


if __name__ == "__main__":
    main()
