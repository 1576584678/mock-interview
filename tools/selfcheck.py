# -*- coding: utf-8 -*-
"""P6 · 一键自检：环境是否可用 + 内置回归是否全绿。

面向首次使用者：克隆仓库后先跑这一个命令，就知道环境行不行、代码有没有坏。
**不调用任何模型**，因此可以离线、可重复运行。

用法::

    python tools/selfcheck.py            # 环境 + 测试 + 回归简历 + SKILL.md
    python tools/selfcheck.py --quick    # 只查环境
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = sys.executable
OK, BAD, WARN = [], [], []


def line(name, status, detail=""):
    tag = {"ok": "OK  ", "warn": "WARN", "bad": "FAIL"}[status]
    print(f"  [{tag}] {name}" + (f"  — {detail}" if detail else ""))
    (OK if status == "ok" else BAD if status == "bad" else WARN).append(name)


def env_checks():
    print("=== 1. 环境 ===")
    v = sys.version_info
    line(f"Python {v.major}.{v.minor}.{v.micro}", "ok" if v >= (3, 9) else "bad",
         "" if v >= (3, 9) else "需要 3.9+")
    line("仅用标准库（无需 pip install）", "ok")
    for exe, why in (("git", "P4 验真层核对提交数"), ):
        p = shutil.which(exe)
        line(f"{exe} 可用（{why}）", "ok" if p else "warn",
             p or "未找到；P4 的提交类声明会判『无法验证』")
    browsers = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
    found = next((b for b in browsers if os.path.exists(b)), None) or shutil.which("google-chrome")
    line("浏览器可用（仅导出 PDF 需要）", "ok" if found else "warn",
         found or "未找到；可在浏览器里手动打印 05_改后简历.html")
    for d in ("pipeline", "prompts", "rubric", "templates", "tests"):
        line(f"目录存在：{d}/", "ok" if os.path.isdir(os.path.join(ROOT, d)) else "bad")


def run(title, argv):
    print(f"=== {title} ===")
    p = subprocess.run([PY] + argv, cwd=ROOT, capture_output=True)
    out = (p.stdout or b"").decode("utf-8", "replace")
    tail = [l for l in out.splitlines() if "通过" in l or "结果" in l or "====" in l]
    ok = p.returncode == 0
    line(title, "ok" if ok else "bad",
         (tail[-1] if tail else "") or f"exit {p.returncode}")
    if not ok:
        print((p.stderr or b"").decode("utf-8", "replace")[-800:])
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只查环境，不跑测试")
    args = ap.parse_args(argv)
    env_checks()
    if args.quick:
        print("\n（--quick：跳过测试）")
    else:
        run("回归测试（tests/run_tests.py）", ["tests/run_tests.py"])
        run("3 份回归简历（tests/check_resumes.py）", ["tests/check_resumes.py"])
        run("SKILL.md 触发词自检（tools/check_skill.py）", ["tools/check_skill.py"])
    print(f"\n===== 自检结果: {len(OK)} 通过 / {len(WARN)} 警告 / {len(BAD)} 失败 =====")
    for w in WARN:
        print(f"  ~ {w}（不影响主链，对应能力降级）")
    for b in BAD:
        print(f"  ! {b}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
