# -*- coding: utf-8 -*-
"""P5 产出层：`05_改后简历.html`（+ 人工确认后的 PDF 导出）。

设计要点
- **只修表述，不造事实**：正文全部来自 `out/real/resume.json`；
  改动只能通过 `judgments/fixes.json` 的 `replace_from → replace_to` 成对替换生效。
  - `replace_from` 必须是简历原文的**逐字子串**，找不到就**不替换**并记入"待人工处理"；
  - `replace_to` 里出现的每一个数字都必须已经在简历原文里出现过，否则**整份拒绝**（退出码 1）。
- **通用性**：标题与章节不绑定任何岗位，只写能力域。
- **改动说明**：置于 HTML 顶部（黄底），`@media print { .changelog{display:none} }`
  使其**打印/导出 PDF 时自动隐藏**，不进入正式简历；每条改动都引用 `R-xx`（服务验收 A5）。
- **人工闸门**：`--pdf` 必须同时给 `--confirm`；`--verify-pdf <路径>` 可在不确认的情况下
  出验证件（用于 CI/自检），验证内容包括"改动说明确实没进 PDF"。

用法::

    python pipeline/export_resume.py --root .
    python pipeline/export_resume.py --root . --pdf --confirm         # 出正式 PDF
    python pipeline/export_resume.py --root . --verify-pdf out/_pdf_check.pdf
"""

import argparse
import html
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text, render, write_text, resolve_in  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/microsoft-edge",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]
CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# 风险类型 → 改动说明标签样式
TAG_OF = {
    "事实错误": ("t-fix", "事实错误"),
    "数字不实": ("t-fix", "数字不实"),
    "夸大词": ("t-fix", "措辞注水"),
    "机制存疑": ("t-fix", "机制存疑"),
    "口径缺失": ("t-add", "补口径"),
    "时间线": ("t-add", "补时间说明"),
    "能力缺口": ("t-pos", "不写进简历"),
}
DIGIT_RE = re.compile(r"\d+(?:\.\d+)?")
SENT_END = "。！？；!?;"

# PDF 泄漏哨兵：只出现在改动说明标题里。正文里不得出现（正文措辞已避开"改动说明"字样），
# 否则说明 @media print 没生效。
CHANGELOG_SENTINEL = "本次改动说明"


# ------------------------------------------------------------------ 小工具
def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=False)


def digits_of(text: str) -> set:
    return set(DIGIT_RE.findall(str(text or "")))


def walk_strings(node, path="") -> list:
    """递归收集 JSON 里所有字符串（(路径, 文本) 对），替换只在这些原文上做。"""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).startswith("_"):
                continue
            out += walk_strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += walk_strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        out.append((path, node))
    return out


def merge_fragments(items: list) -> list:
    """把解析器拆碎的片段合并成完整要点（只在片段未以句末标点结束时向后粘）。"""
    out = []
    for raw in items or []:
        s = " ".join(str(raw or "").split())
        if not s:
            continue
        if out and out[-1] and out[-1][-1] not in SENT_END:
            out[-1] = out[-1] + s
        else:
            out.append(s)
    return out


def find_browser() -> str:
    for p in EDGE_CANDIDATES + CHROME_CANDIDATES:
        if os.path.exists(p):
            return p
    return ""


# ------------------------------------------------------------------ 去重
def _shingles(text: str, n: int = 12) -> set:
    t = re.sub(r"\s+", "", str(text or ""))
    if len(t) < n:
        return {t} if t else set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def overlap_ratio(a: str, b: str, n: int = 12) -> float:
    """a 的 n-gram 有多少比例也出现在 b 里。"""
    sa = _shingles(a, n)
    if not sa:
        return 0.0
    return len(sa & _shingles(b, n)) / len(sa)


def drop_duplicate_projects(resume: dict, log: Log, threshold: float = 0.5) -> list:
    """合并"项目经历"里与"工作经历"高度重复的条目（解析器两处都抽了，正文会重复两遍）。

    这是**排版去重**，不删任何事实：被丢弃的条目内容已经出现在工作经历里，
    且丢弃门槛是 12-gram 重叠 ≥ threshold（默认 0.5）。返回被丢弃的 [(名称, 重叠度)]。
    """
    exps = resume.get("experiences") or []
    projs = resume.get("projects") or []
    if not exps or not projs:
        return []
    exp_text = " ".join(str(h) for e in exps for h in (e.get("highlights") or []))
    kept, dropped = [], []
    for p in projs:
        ptext = str(p.get("name", "")) + " " + " ".join(str(h) for h in (p.get("highlights") or []))
        ov = overlap_ratio(ptext, exp_text)
        if ov >= threshold:
            dropped.append((str(p.get("name", "")), round(ov, 2)))
        else:
            kept.append(p)
    if dropped:
        resume["projects"] = kept
        log.info(f"项目经历去重：合并 {len(dropped)} 条与工作经历重复的条目 "
                 f"{[d[0][:18] for d in dropped]}")
    return dropped


def trim_long_items(resume: dict, limit: int, log: Log) -> list:
    """把单条经历/项目的高亮总长超过 limit 的截断，并在截断处留可见标记。

    默认关闭（`--trim-long-items 0`）：本 skill 不静默删除内容。开启后每一条截断
    都会写进改动说明，用户能对着原文核对。
    """
    trimmed = []
    groups = ((resume.get("experiences") or [], "company"),
              (resume.get("projects") or [], "name"))
    for coll, key in groups:
        for it in coll:
            hls = it.get("highlights") or []
            total = sum(len(str(h)) for h in hls)
            if total <= limit:
                continue
            acc, out = 0, []
            for h in hls:
                s = str(h)
                if acc + len(s) > limit:
                    out.append(s[:max(0, limit - acc)] + "…（该条过长，已截断）")
                    break
                out.append(s)
                acc += len(s)
            it["highlights"] = out
            trimmed.append((str(it.get(key, "")), total))
    if trimmed:
        log.info(f"截断 {len(trimmed)} 条超长条目（上限 {limit} 字）："
                 f"{[t[0][:16] for t in trimmed]}")
    return trimmed


# ------------------------------------------------------------------ 替换
def apply_fixes(resume: dict, fixes: list, log: Log) -> tuple:
    """返回 (applied, skipped)；只做成对字符串替换，不发明任何内容。"""
    texts = walk_strings(resume)
    resume_digits = digits_of(json.dumps(resume, ensure_ascii=False))
    applied, skipped = [], []
    for fx in fixes:
        rid = fx.get("risk_id", "")
        src = fx.get("replace_from") or ""
        dst = fx.get("replace_to") or ""
        if not src or not dst:
            skipped.append({"risk_id": rid, "reason": "缺少 replace_from 或 replace_to",
                            "note": fx.get("note", "")})
            continue
        new_digits = digits_of(dst) - resume_digits
        if new_digits:
            die(f"改动 {rid} 的 replace_to 引入了简历里没有的数字 {sorted(new_digits)}"
                f"——本 skill 禁止在简历里新增无法自证的数字")
        hit = 0
        for _p, t in texts:
            if src in t:
                hit += 1
        if not hit:
            skipped.append({"risk_id": rid, "reason": "replace_from 在简历原文中找不到（不替换）",
                            "from": src, "to": dst, "note": fx.get("note", "")})
            log.warn(f"改动 {rid} 未应用：replace_from 未逐字命中")
            continue
        for p, t in texts:
            if src in t:
                node = resume
                for key in re.findall(r"[^.\[\]]+|\[\d+\]", p):
                    if key.startswith("["):
                        node = node[int(key[1:-1])]
                    else:
                        node = node[key]
                if isinstance(node, str):
                    node = node.replace(src, dst)
                    _set_path(resume, p, node)
        applied.append({"risk_id": rid, "from": src, "to": dst,
                        "note": fx.get("note", ""), "hits": hit})
    return applied, skipped


def _set_path(resume: dict, path: str, value):
    keys = re.findall(r"[^.\[\]]+|\[\d+\]", path)
    node = resume
    for key in keys[:-1]:
        node = node[int(key[1:-1])] if key.startswith("[") else node[key]
    last = keys[-1]
    if last.startswith("["):
        node[int(last[1:-1])] = value
    else:
        node[last] = value


# ------------------------------------------------------------------ 正文
def build_body(resume: dict, log: Log) -> str:
    basic = resume.get("basic") or {}
    out = []

    # 个人优势
    summary = [s for s in (resume.get("summary") or []) if str(s).strip()]
    if summary:
        out.append('<h2 class="sec">个人优势</h2>')
        out.append('<ul class="adv">')
        for s in summary:
            text = " ".join(str(s).split())
            if "：" in text:
                label, rest = text.split("：", 1)
                out.append(f"<li><b>{esc(label)}</b>：{esc(rest)}</li>")
            else:
                out.append(f"<li>{esc(text)}</li>")
        out.append("</ul>")

    # 专业技能
    skills = resume.get("skills") or []
    if skills:
        out.append('<h2 class="sec">专业技能</h2>')
        out.append('<table class="skills">')
        for sk in skills:
            cat = esc(sk.get("category", ""))
            items = "、".join(" ".join(str(i).split()) for i in (sk.get("items") or []))
            out.append(f"<tr><td>{cat}</td><td>{esc(items)}</td></tr>")
        out.append("</table>")

    # 工作经历
    exps = resume.get("experiences") or []
    if exps:
        out.append('<h2 class="sec">工作经历</h2>')
        for e in exps:
            period = e.get("raw") or f"{e.get('start', '')} - {e.get('end', '')}"
            out.append(f'<h3 class="job">{esc(e.get("company", ""))} ｜ '
                       f'{esc(e.get("title", ""))} ｜ {esc(period)}</h3>')
            stack = e.get("stack") or []
            if stack:
                out.append(f'<div class="stack">{esc(" · ".join(map(str, stack)))}</div>')
            hl = merge_fragments(e.get("highlights") or [])
            if hl:
                out.append("<ul>")
                for h in hl:
                    out.append(f"<li>{esc(h)}</li>")
                out.append("</ul>")

    # 项目经历
    projs = resume.get("projects") or []
    if projs:
        out.append('<h2 class="sec">项目经历</h2>')
        for p in projs:
            head = esc(p.get("name", ""))
            if p.get("period"):
                head += f' ｜ {esc(p["period"])}'
            if p.get("role"):
                head += f' ｜ {esc(p["role"])}'
            out.append(f'<h3 class="job">{head}</h3>')
            stack = p.get("stack") or []
            if stack:
                out.append(f'<div class="stack">{esc(" · ".join(map(str, stack)))}</div>')
            hl = merge_fragments(p.get("highlights") or [])
            if hl:
                out.append("<ul>")
                for h in hl:
                    out.append(f"<li>{esc(h)}</li>")
                out.append("</ul>")

    # 教育
    edu = [e for e in (resume.get("education") or []) if str(e).strip()]
    if edu:
        out.append('<h2 class="sec">教育经历</h2>')
        out.append("<ul>")
        for e in edu:
            out.append(f"<li>{esc(' '.join(str(e).split()))}</li>")
        out.append("</ul>")

    # 时间线说明（空窗期）——真实理由必须由本人补充，渲染器不编
    gaps = resume.get("timeline_gaps") or []
    if gaps:
        out.append('<h2 class="sec">时间线说明</h2>')
        out.append("<ul>")
        for g in gaps:
            seg = f"{g.get('from', '')} - {g.get('to', '')}"
            out.append(f"<li>{esc(seg)}（约 {esc(g.get('months', ''))} 个月）："
                       f'<span class="unconfirmed">待补充真实原因与期间产出'
                       f"（编号 R-01 / R-06）</span></li>")
        out.append("</ul>")
    _ = basic
    return "\n".join(out)


def build_changelog(applied: list, skipped: list, unfixable: list,
                    risks: dict, rewrites: list, general: list) -> str:
    o = []
    o.append("  <h2>📌 本次改动说明（打印 / 导出 PDF 时自动隐藏，不进入正式简历）</h2>")

    def rid_of(rid):
        r = risks.get(rid) or {}
        return r

    if applied:
        o.append("  <h3>一、已自动应用的改动（逐条可回溯到风险点编号）</h3>")
        o.append("  <ol>")
        for a in applied:
            r = rid_of(a["risk_id"])
            cls, label = TAG_OF.get(r.get("type", ""), ("t-fmt", r.get("type", "改动")))
            o.append(f'    <li><span class="tag {cls}">{esc(label)}</span>'
                     f'<b>[{esc(a["risk_id"])}]</b> '
                     f'原：<span class="num">{esc(a["from"])}</span> → '
                     f'改：<b>{esc(a["to"])}</b>'
                     + (f'<br><span class="note">{esc(a["note"])}</span>' if a.get("note") else "")
                     + "</li>")
        o.append("  </ol>")

    n = len(applied)
    manual = []
    for s in skipped:
        manual.append((s["risk_id"], s.get("reason", ""), s.get("note", "")))
    for u in unfixable:
        manual.append((u.get("risk_id", ""), u.get("reason", ""), ""))
    if manual:
        o.append(f'  <h3>二、需人工确认（共 {len(manual)} 条，未自动改动正文）</h3>')
        o.append("  <ol>")
        for rid, why, note in manual:
            r = rid_of(rid)
            cls, label = TAG_OF.get(r.get("type", ""), ("t-fmt", r.get("type", "待确认")))
            o.append(f'    <li><span class="tag {cls}">{esc(label)}</span>'
                     f'<b>[{esc(rid)}]</b> {esc(r.get("why", ""))}'
                     f'<br>处置：{esc(why)}'
                     + (f'<br><span class="note">{esc(note)}</span>' if note else "")
                     + "</li>")
        o.append("  </ol>")

    if rewrites:
        o.append("  <h3>三、可直接采用的改写文案（来自风险评估，未自动替换）</h3>")
        o.append("  <ol>")
        for rw in rewrites:
            if rw.get("risk_id") in {a["risk_id"] for a in applied}:
                continue
            o.append(f'    <li><b>[{esc(rw.get("risk_id", ""))}]</b> {esc(rw.get("text", ""))}'
                     + (f'<br><span class="note">{esc(rw.get("note", ""))}</span>'
                        if rw.get("note") else "") + "</li>")
        o.append("  </ol>")

    if general:
        o.append("  <h3>四、全局表述与结构</h3>")
        o.append("  <ol>")
        for g in general:
            o.append(f"    <li>{esc(g)}</li>")
        o.append("  </ol>")

    o.append('  <div class="note"><b>保守处理（没有改动的地方）：</b>'
             "没有新增任何简历之外、也无法自证的内容；<b>能力缺口类风险点不写进简历</b>"
             "（写了就是送分给面试官），只在面试话术里做迁移论证。"
             f"<br><b>本次共自动应用 {n} 条改动</b>，其余条目见上方清单，"
             "改写建议均经过「不得引入新数字」校验。</div>")
    return "\n".join(o)


def render_resume(resume: dict, changelog: str, body: str, label: str = "") -> str:
    basic = resume.get("basic") or {}
    title = basic.get("title") or "后端开发工程师"
    headline = title
    if basic.get("years"):
        headline += f'（{basic["years"]}）'
    meta_bits = []
    if basic.get("years"):
        meta_bits.append(f'<span><b>{esc(basic["years"])}</b> 经验</span>')
    for key in ("phone", "wechat", "email", "github", "city"):
        if basic.get(key):
            v = str(basic[key])
            if key == "wechat":
                v = "微信 " + v
            meta_bits.append(f"<span>{esc(v)}</span>")
    tpl = read_text(os.path.join(HERE, "..", "templates", "resume.html"))
    return render(tpl, {
        "PAGE_TITLE": esc(f'{basic.get("name", "")} · {title}'.strip(" ·")),
        "NAME": esc(basic.get("name", "")),
        "HEADLINE": esc(headline),
        "META": "\n      ".join(meta_bits),
        "CHANGELOG": changelog,
        "BODY": body,
    }, where=f"templates/resume.html({label})")


# ------------------------------------------------------------------ PDF
def export_pdf(html_path: str, pdf_path: str, log: Log) -> str:
    exe = find_browser()
    if not exe:
        die("找不到 Edge/Chrome——HTML→PDF 需要 Chromium 内核浏览器。"
            "请安装 Edge，或手动用浏览器「打印 → 另存为 PDF」（打印时改动说明会自动隐藏）")
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    tmp = tempfile.mkdtemp(prefix="dsh-pdf-")
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           "--no-pdf-header-footer", "--print-to-pdf-no-header",
           f"--user-data-dir={tmp}", f"--print-to-pdf={os.path.abspath(pdf_path)}", url]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180, check=False)
    except subprocess.TimeoutExpired:
        log.warn("浏览器导出超时（PDF 可能已生成，继续校验）")
    if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1000:
        die(f"PDF 导出失败：{pdf_path} 未生成。请手动用浏览器打印为 PDF")
    return pdf_path


def verify_pdf(pdf_path: str, resume: dict, log: Log) -> dict:
    """用内置 PDF 解析校验：改动说明**不得**进 PDF；姓名必须在。"""
    text, pages = "", 0
    try:
        from extract_text import via_builtin_pdf  # noqa: E402
        parsed = via_builtin_pdf(pdf_path, log)
        if isinstance(parsed, dict):
            text, pages = parsed.get("text") or "", parsed.get("pages") or 0
        elif isinstance(parsed, str):
            text = parsed
    except Exception as e:  # noqa: BLE001
        log.warn(f"PDF 文本校验跳过（解析失败）：{e}")
        return {"checked": False}
    name = str((resume.get("basic") or {}).get("name", ""))
    leaked = CHANGELOG_SENTINEL in text
    res = {
        "checked": True,
        "pages": pages,
        "size": os.path.getsize(pdf_path),
        "name_found": (name in text) if name else True,
        "changelog_leaked": leaked,
        "chars": len(text),
    }
    if leaked:
        die("PDF 里出现了「改动说明」——打印样式未生效，禁止交付该 PDF")
    if not res["name_found"]:
        die("PDF 里读不到姓名——导出可能空白，禁止交付该 PDF")
    log.info(f"PDF 校验通过：{pages} 页、{res['size']} 字节、正文 {res['chars']} 字、"
             f"改动说明未进入 PDF")
    return res


# ------------------------------------------------------------------ CLI
def cmd_run(args):
    work = os.path.abspath(args.root)
    log = Log(quiet=args.quiet)

    def rp(rel, default_dir="out"):
        return resolve_in(work, rel)

    resume_path = rp(args.resume)
    if not os.path.exists(resume_path):
        die(f"缺少简历结构化文件 {resume_path}——请先跑 P1 解析")
    resume = json.loads(read_text(resume_path))

    risks = {}
    if os.path.exists(rp(args.risks)):
        raw = json.loads(read_text(rp(args.risks)))
        if isinstance(raw, dict):
            raw = raw.get("risks") or []
        risks = {r.get("id"): r for r in raw if r.get("id")}

    fixes, unfixable, rewrites = [], [], []
    if os.path.exists(rp(args.fixes)):
        blob = json.loads(read_text(rp(args.fixes)))
        fixes = blob.get("fixes") or []
        unfixable = blob.get("unfixable") or []
        log.info(f"载入 {len(fixes)} 条成对替换建议、{len(unfixable)} 条声明不可替换")
    if os.path.exists(rp(args.rewrites)):
        blob = json.loads(read_text(rp(args.rewrites)))
        rewrites = blob.get("rewrites") or blob.get("fixes") or []

    applied, skipped = apply_fixes(resume, fixes, log)
    log.info(f"已应用 {len(applied)} 条替换，跳过 {len(skipped)} 条")

    general = [
        "标题保持岗位中立，只写能力域，不绑定任何具体岗位或单一赛道。",
        "章节顺序：个人优势 → 专业技能 → 工作经历 → 项目经历 → 教育经历 → 时间线说明。",
        "改动说明置于 HTML 顶部，打印/导出 PDF 时自动隐藏，不进入正式简历。",
    ]
    dropped = drop_duplicate_projects(resume, log)
    if dropped:
        general.append("项目经历去重：合并 " + str(len(dropped)) +
                       " 条与工作经历高度重复的条目（内容已在工作经历中出现，"
                       "去除后正文不再重复两遍）：" +
                       "；".join(f"{n[:20]}（重叠 {r}）" for n, r in dropped))
    if args.trim_long_items > 0:
        trimmed = trim_long_items(resume, args.trim_long_items, log)
        if trimmed:
            general.append(
                f"超长条目截断（上限 {args.trim_long_items} 字）：" +
                "；".join(f"{n[:20]} 原 {c} 字" for n, c in trimmed) +
                "——截断处已留「该条过长，已截断」标记，完整内容请对照简历原文核对。")
    body = build_body(resume, log)
    changelog = build_changelog(applied, skipped, unfixable, risks, rewrites, general)
    html_text = render_resume(resume, changelog, body, label=args.resume)

    out = rp(args.out) if os.path.isabs(args.out) else os.path.join(work, args.out)
    write_text(out, html_text)
    log.info(f"已生成: {out}")

    summary = {"html": out, "applied": applied, "skipped": skipped,
               "unfixable": unfixable, "risk_ids": sorted(risks.keys())}

    if args.pdf or args.verify_pdf:
        if args.pdf and not args.confirm:
            die("导出正式 PDF 需要人工闸门：先打开 HTML 确认无误，再加 --confirm；"
                "只想验证导出链路可用 --verify-pdf out/_pdf_check.pdf")
        target = rp(args.pdf_path) if args.pdf else rp(args.verify_pdf)
        export_pdf(out, target, log)
        summary["pdf"] = target
        summary["pdf_check"] = verify_pdf(target, resume, log)
        if not args.pdf:
            log.info("（这是验证件，不是交付物；正式 PDF 请加 --pdf --confirm）")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description="生成 05_改后简历.html（+ PDF 导出）")
    p.add_argument("--root", default=".")
    p.add_argument("--resume", default="out/real/resume.json")
    p.add_argument("--risks", default="out/risks.json")
    p.add_argument("--fixes", default="judgments/fixes.json")
    p.add_argument("--rewrites", default="judgments/rewrites.json")
    p.add_argument("--out", default="05_改后简历.html")
    p.add_argument("--trim-long-items", type=int, default=0,
                   help="单条经历/项目高亮超过该字数则截断（0=不截断，默认关闭）")
    p.add_argument("--pdf", action="store_true", help="导出正式 PDF（必须同时给 --confirm）")
    p.add_argument("--confirm", action="store_true", help="人工确认闸门")
    p.add_argument("--pdf-path", default="05_改后简历.pdf")
    p.add_argument("--verify-pdf", default="", help="出验证件到指定路径（无需确认）")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    return cmd_run(args)


if __name__ == "__main__":
    main()
    sys.exit(0)
