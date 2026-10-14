#!/usr/bin/env python3
"""mock-interview · pipeline 共用工具

约定（见 pipeline/README.md）：
- 读 JSON / 写 JSON，参数走命令行
- UTF-8（无 BOM）；失败时不产生残缺输出文件（先写临时文件再原子替换）
- 所有脚本支持 --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys


class Log:
    """日志一律写 stderr，保证 stdout 干净（便于管道）。"""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet

    def _p(self, tag, msg):
        if not self.quiet:
            print(f"[{tag}] {msg}", file=sys.stderr)

    def info(self, m):
        self._p("info", m)

    def step(self, m):
        self._p("step", m)

    def warn(self, m):
        self._p("warn", m)

    def error(self, m):
        self._p("error", m)


def die(msg: str, code: int = 1):
    print(f"[error] {msg}", file=sys.stderr)
    sys.exit(code)


def read_text(path: str) -> str:
    """尽力读取文本，依次尝试常见中文编码。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def write_text(path: str, text: str):
    """原子写入：先写 .tmp 再替换，避免产生残缺文件。"""
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def write_json(path: str, obj):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render(tpl: str, mapping: dict, where: str = "模板") -> str:
    """替换 {{KEY}}（大小写均可）。

    有未提供的占位符时**直接报错**，避免把带空白的提示词喂给模型。
    """
    missing = set()

    def sub(m):
        k = m.group(1)
        if k not in mapping:
            missing.add(k)
            return m.group(0)
        return str(mapping[k])

    out = re.sub(r"\{\{([A-Za-z0-9_]+)\}\}", sub, tpl)
    if missing:
        die(f"{where} 模板占位符未被赋值: {', '.join(sorted(missing))}")
    return out


def as_text(v) -> str:
    """把任意 JSON 值展平成文本。

    解析层产出的字段类型不统一（`summary` 是字符串列表，`highlights` 是列表，
    `basic` 是字典），拼接字符串时到处 `TypeError`。统一在这里兜住。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return " ".join(as_text(x) for x in v.values())
    if isinstance(v, (list, tuple, set)):
        return " ".join(as_text(x) for x in v)
    return str(v)


def base_parser(desc: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=desc)
    p.add_argument("--in", dest="inp", required=True, help="输入文件")
    p.add_argument("--out", dest="out", required=True, help="输出文件路径")
    p.add_argument("--dry-run", action="store_true", help="只打印将要做什么，不写文件")
    p.add_argument("--quiet", action="store_true", help="安静模式")
    return p


def check_input(path: str):
    if not os.path.exists(path):
        die(f"输入文件不存在: {path}")
    if os.path.isdir(path):
        die(f"输入是目录而不是文件: {path}")


def resolve_in(work: str, rel: str, alt_dirs=("out",)) -> str:
    """把相对路径解析到工作区里。

    首次使用者最容易踩的坑：脚本默认路径按"真实跑"布局写成 `out/real/x.json`，
    而用户（和示例）把文件放在 `out/x.json`。规则只有两条，**顺序固定**：

    1. 拼出来的路径存在 → 用它（正常工作区）；
    2. 否则按同名文件回退到 `out/`（`out/real/x.json` → `out/x.json`）。

    ⚠️ 刻意**不做**"先看相对当前目录是否存在"这一步：当工作区恰好是测试夹具、
    而仓库根目录又有同名真实产物时，那样会静默读到错误文件（这条曾经真的踩过）。
    输入路径要么给绝对路径，要么相对于工作区。
    """
    if os.path.isabs(rel):
        return rel
    joined = os.path.join(work, rel)
    if os.path.exists(joined):
        return joined
    for d in alt_dirs:
        alt = os.path.join(work, d, os.path.basename(rel))
        if os.path.exists(alt):
            return alt
    return joined


def resolve_out(work: str, rel: str) -> str:
    """解析输出路径：不制造嵌套目录（`<root>/examples/x/work/out/a.json` 那种）。

    判断依据是"目标目录是否存在"，而不是"文件是否存在"——输出文件本来还不存在。
    只有当下面的两件事同时成立时，才认为用户写的是相对当前目录的完整路径：
    ① 该路径的目录在当前位置存在；② 拼到工作区里的目录不存在。
    """
    if os.path.isabs(rel):
        return rel
    d = os.path.dirname(rel)
    if d and os.path.isdir(d) and not os.path.isdir(os.path.dirname(os.path.join(work, rel))):
        return rel
    return os.path.join(work, rel)
