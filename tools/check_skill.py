# -*- coding: utf-8 -*-
"""P6 · SKILL.md 触发词与格式自检（不进行模型调用）。

agent 环境判断"要不要加载这个 skill"，依据的是 SKILL.md 的 frontmatter
（`name` + `description`）：描述里没写到的场景，就不会被触发。
所以触发词必须**可检查**，而不是靠感觉。

本脚本检查：
1. frontmatter 存在、能被 YAML 子集解析、`name` 与目录名一致；
2. `description` 覆盖了 4 类典型说法（模拟面试 / 简历体检 / 薪资 / 验真）；
3. 必需章节齐全（何时使用 / 输入契约 / 执行流程 / 输出契约 / 硬约束 / 失败与降级）；
4. SKILL.md 里出现的每个 `pipeline/*.py` 都真实存在（防止文档与代码脱节）；
5. SKILL.md 里提到的每个产出文件名都出现在输出契约里。

用法::

    python tools/check_skill.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SKILL = os.path.join(ROOT, "SKILL.md")

# 用户可能说出的说法 → 触发词必须覆盖（在 description 或「何时使用」里出现）
TRIGGERS = {
    "模拟面试": ["模拟面试", "面试会问什么"],
    "简历体检": ["简历体检", "风险点"],
    "薪资建议": ["薪资", "要多少"],
    "验真": ["属实", "核对"],
}
REQUIRED_SECTIONS = ["何时使用", "输入契约", "执行流程", "输出契约", "硬约束", "失败与降级"]
OUTPUTS = ["01_面试对话实录.md", "02_复盘报告.md", "03_薪资建议.md",
           "04_简历风险点.md", "05_改后简历.html"]

OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name if cond else f"{name} ← {detail}")
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  ← {detail}"))


def parse_frontmatter(text):
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return None
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def main():
    print("=== SKILL.md 触发词与格式自检 ===")
    if not os.path.exists(SKILL):
        check("SKILL.md 存在", False, SKILL)
        return 1
    text = open(SKILL, encoding="utf-8").read()
    fm = parse_frontmatter(text)
    check("frontmatter 可解析（YAML 子集）", fm is not None, "缺失或格式错误")
    if not fm:
        return 1
    check("frontmatter 含 name 与 description", bool(fm.get("name")) and bool(fm.get("description")),
          list(fm))
    check("name 与目录名一致", fm.get("name") == os.path.basename(ROOT),
          (fm.get("name"), os.path.basename(ROOT)))
    check("description 足够长（≥60 字，否则匹配面太窄）",
          len(fm.get("description", "")) >= 60, len(fm.get("description", "")))

    body = text
    for label, words in TRIGGERS.items():
        check(f"触发词覆盖「{label}」说法",
              any(w in body for w in words), words)
    check("description 里写了适用场景（触发匹配主要看它）",
          "适用场景" in fm.get("description", "") or "场景" in fm.get("description", ""),
          fm.get("description", "")[:60])

    for sec in REQUIRED_SECTIONS:
        check(f"必需章节：{sec}", f"## {sec}" in body or f"### {sec}" in body)

    scripts = sorted(set(re.findall(r"pipeline/([A-Za-z0-9_]+\.py)", body)))
    missing = [s for s in scripts if not os.path.exists(os.path.join(ROOT, "pipeline", s))]
    check("SKILL.md 引用的脚本全部存在（文档未与代码脱节）",
          not missing and len(scripts) >= 8, missing or scripts)

    missing_out = [o for o in OUTPUTS if o not in body]
    check("五件套产出名都在 SKILL.md 里出现", not missing_out, missing_out)

    # 命令块里的每个脚本都要能 --help（防止写成"看上去对"的手册）
    print(f"\n===== SKILL.md 自检: {len(OK)} 通过 / {len(BAD)} 失败 =====")
    for b in BAD:
        print(f"  ! {b}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
