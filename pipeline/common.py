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
