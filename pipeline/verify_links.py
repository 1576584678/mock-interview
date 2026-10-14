# -*- coding: utf-8 -*-
"""P5 产出层：验收 A5 —— 五件套编号引用闭环校验。

闭环的定义（三条同时成立才算闭环）：
1. **无悬空编号**：任何产物里出现的 `R-xx` 都必须存在于 `out/risks.json`；
2. **互相引用**：每条风险点至少出现在 **2 个不同产物**里
   （只有 04 提到 = 孤岛，只有 01 提到 = 没进报告）；
3. **清单完整**：每条风险点都必须出现在 `04_简历风险点.md` 里。

另外校验五件套是否齐全（`--strict` 下缺文件即失败）。

用法::

    python pipeline/verify_links.py --root .
    python pipeline/verify_links.py --root . --strict      # 缺文件 / 未闭环 → 退出码 1
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text  # noqa: E402

FIVE = [
    "01_面试对话实录.md",
    "02_复盘报告.md",
    "03_薪资建议.md",
    "04_简历风险点.md",
    "05_改后简历.html",
]
RID_RE = re.compile(r"R-\d{2}")


def scan(work: str) -> dict:
    found = {}
    for name in FIVE:
        p = os.path.join(work, name)
        if os.path.exists(p):
            found[name] = sorted(set(RID_RE.findall(read_text(p))))
    return found


def check(work: str, risk_ids: list, found: dict, strict: bool) -> dict:
    missing_files = [n for n in FIVE if n not in found]
    all_refs = sorted({r for refs in found.values() for r in refs})
    dangling = [r for r in all_refs if r not in risk_ids]
    per_risk = {r: [n for n in FIVE if n in found and r in found[n]] for r in risk_ids}
    orphan = [r for r, files in per_risk.items() if len(files) < 2]
    not_in_04 = [r for r in risk_ids if "04_简历风险点.md" not in per_risk[r]]

    ok = not dangling and not orphan and not not_in_04 and (not strict or not missing_files)
    return {
        "ok": ok,
        "files": found,
        "missing_files": missing_files,
        "risk_count": len(risk_ids),
        "dangling": dangling,
        "orphan": orphan,
        "not_in_04": not_in_04,
        "per_risk": per_risk,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="五件套编号引用闭环校验（验收 A5）")
    p.add_argument("--root", default=".")
    p.add_argument("--risks", default="out/risks.json")
    p.add_argument("--strict", action="store_true", help="缺文件或未闭环 → 退出码 1")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    work = os.path.abspath(args.root)
    log = Log(quiet=args.quiet)
    risks_path = args.risks if os.path.isabs(args.risks) else os.path.join(work, args.risks)
    if not os.path.exists(risks_path):
        die(f"缺少 {risks_path}（风险点清单是闭环的基准）")
    risks = json.loads(read_text(risks_path))
    if isinstance(risks, dict):
        risks = risks.get("risks") or []
    risk_ids = [r.get("id") for r in risks if r.get("id")]

    found = scan(work)
    res = check(work, risk_ids, found, args.strict)

    if not args.quiet:
        log.info("五件套编号引用矩阵（● = 该产物引用了该风险点）")
        names = [n for n in FIVE]
        head = "| 风险点 | " + " | ".join(f"{n.split('_')[1][:6]}" for n in names) + " |"
        log.info(head)
        for r in risk_ids:
            cells = " | ".join("●" if r in found.get(n, []) else "·" for n in names)
            log.info(f"| {r} | {cells} |")
        log.info("")
        if res["missing_files"]:
            log.info("缺少产物：" + "、".join(res["missing_files"]))
        log.info(f"风险点 {res['risk_count']} 条；"
                 f"悬空编号 {len(res['dangling'])}；"
                 f"孤岛（只出现在 1 个产物）{len(res['orphan'])}；"
                 f"未进 04 {len(res['not_in_04'])}")
        if res["dangling"]:
            log.info("  悬空编号：" + "、".join(res["dangling"]))
        for r in res["orphan"]:
            log.info(f"  孤岛：{r} 只出现在 {res['per_risk'][r] or '（无）'}")
        if res["not_in_04"]:
            log.info("  未进 04：" + "、".join(res["not_in_04"]))

    if res["ok"]:
        log.info("== 验收 A5 通过：五件套编号引用闭环 100% ==")
        return res
    if not args.quiet:
        log.info("== 验收 A5 未通过 ==")
    if args.strict:
        die("编号引用闭环未达成——产物之间存在断链，修复后重跑")
    return res


if __name__ == "__main__":
    main()
    sys.exit(0)
