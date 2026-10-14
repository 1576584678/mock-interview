# -*- coding: utf-8 -*-
"""P6 回归测试简历自动检查脚本（不调用模型，纯脚本可复现）。

三份回归简历分别压不同的路径：
- `A_规范完整.md`：口径完整、数字自洽、无空窗 → 期望风险点最少
- `B_注水夸大.md`：提交数/测试数/百分比无口径/技术常识错误/技能全"精通" → 期望风险点最多
- `C_转行薄简历.md`：转行、经历薄、JD 硬性要求大面积缺失 → 期望"能力缺口"最多

用法::

    python tests/check_resumes.py            # 全部通过退出 0，否则 1
    python tests/check_resumes.py --keep     # 保留中间产物（tests/tmp/check/*）
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from common import read_text, write_json  # noqa: E402

PY = sys.executable
RESUMES = {
    "A": os.path.join(HERE, "resumes", "A_规范完整.md"),
    "B": os.path.join(HERE, "resumes", "B_注水夸大.md"),
    "C": os.path.join(HERE, "resumes", "C_转行薄简历.md"),
}
JD = os.path.join(HERE, "jd", "供应链履约.txt")

OK, BAD = [], []


def check(name, cond, detail=""):
    if cond:
        OK.append(name)
        print(f"  [PASS] {name}")
    else:
        BAD.append(f"{name} ← {detail}")
        print(f"  [FAIL] {name}  ← {detail}")


def run(*argv):
    p = subprocess.run([PY] + list(argv), capture_output=True, cwd=ROOT)
    out = (p.stdout or b"").decode("utf-8", "replace")
    err = (p.stderr or b"").decode("utf-8", "replace")
    if p.returncode != 0:
        raise SystemExit(f"命令失败（exit {p.returncode}）: {' '.join(argv)}\n{err[-800:]}")
    return out, err


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args(argv)
    work = os.path.join(HERE, "tmp", "check")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    print("=== JD 解析 ===")
    jd_out = os.path.join(work, "jd.json")
    run("pipeline/parse_jd.py", "--in", JD, "--out", jd_out)
    jd = json.loads(read_text(jd_out))
    check("JD 硬性要求被完整抽取", len(jd["must_have"]) >= 4, len(jd["must_have"]))
    check("JD 维度 ≥ 6", len(jd["dimensions"]) >= 6, len(jd["dimensions"]))
    check("JD 薪资区间解析（单位：元）",
          jd["salary_range"][:2] == [20000, 35000], jd["salary_range"])
    check("JD 隐藏痛点非空", len(jd["hidden_pain_points"]) >= 2, len(jd["hidden_pain_points"]))

    stats = {}
    evidence = {}
    for tag, path in RESUMES.items():
        print(f"=== 简历 {tag}（{os.path.basename(path)}） ===")
        d = os.path.join(work, tag)
        os.makedirs(d, exist_ok=True)
        rjson = os.path.join(d, "resume.json")
        run("pipeline/parse_resume.py", "--in", path, "--out", rjson,
            "--text-out", os.path.join(d, "resume.txt"))
        r = json.loads(read_text(rjson))
        check(f"{tag} 解析出姓名与年限",
              bool((r.get("basic") or {}).get("name")) and (r.get("basic") or {}).get("years") is not None,
              r.get("basic"))
        check(f"{tag} 经历/项目/教育章节齐全",
              bool(r.get("experiences")) and bool(r.get("education")),
              (len(r.get("experiences") or []), len(r.get("education") or [])))
        check(f"{tag} 抽取到 claims（量化 + 机制）",
              len(r.get("claims", {}).get("quantitative", [])) > 0,
              len(r.get("claims", {}).get("quantitative", [])))
        check(f"{tag} 技能项被抽取", len(r.get("skills") or []) >= 1, len(r.get("skills") or []))

        run("pipeline/orchestrate.py", "init", "--resume", rjson, "--jd", jd_out, "--root", d)
        raw_risks = json.loads(read_text(os.path.join(d, "out", "risks.json")))
        risks = raw_risks["risks"] if isinstance(raw_risks, dict) else raw_risks
        ids = [x["id"] for x in risks]
        check(f"{tag} 风险点编号唯一且连续",
              len(set(ids)) == len(ids) and ids == [f"R-{i:02d}" for i in range(1, len(ids) + 1)],
              ids)
        check(f"{tag} 风险点类型/严重度/可验证性齐全",
              all(x.get("type") and x.get("severity") in "🔴🟠🟡⚪" and x.get("verifiability")
                  and x.get("probe_hint") for x in risks), risks[:1])
        counts = {}
        for x in risks:
            counts[x["type"]] = counts.get(x["type"], 0) + 1
        stats[tag] = {"n": len(risks), "counts": counts,
                      "gaps": len(r.get("timeline_gaps") or []),
                      "cov": sum(1 for x in r.get("claims", {}).get("quantitative", [])
                                 if x.get("has_definition")),
                      "quant": len(r.get("claims", {}).get("quantitative", []))}
        # P4 在没有仓库时：不判编造
        ev_out = os.path.join(d, "evidence.json")
        run("pipeline/verify_evidence.py", "--root", d, "--resume", rjson,
            "--text", os.path.join(d, "resume.txt"), "--author", "tester",
            "--out", ev_out, "--md-out", os.path.join(d, "evidence.md"), "--quiet")
        ev = json.loads(read_text(ev_out))
        evidence[tag] = ev
        check(f"{tag} 未提供仓库时一律不判编造",
              ev["summary"]["fabrications"] == []
              and all(not c["is_fabrication"] for c in ev["claims"]), ev["summary"])

    print("=== 验收 A6：故意埋雷的检出率（埋 1 处，要求检出） ===")
    b_ids = {c["id"]: c for c in evidence["B"]["claims"]}
    check("B 里的『Oracle 用 Binlog』被 E-01 检出（当场可判 🔴）",
          b_ids.get("E-01", {}).get("verdict") == "与事实不符"
          and b_ids.get("E-01", {}).get("visibility") == "当场可判"
          and b_ids.get("E-01", {}).get("severity") == "🔴",
          b_ids.get("E-01"))
    check("埋雷检出后仍不判编造（代码/常识层不置 is_fabrication）",
          b_ids.get("E-01", {}).get("is_fabrication") is False)
    check("A/C 没有技术常识类硬伤（无误报）",
          "E-01" not in {c["id"] for c in evidence["A"]["claims"]}
          and "E-01" not in {c["id"] for c in evidence["C"]["claims"]})

    print("=== 差异化（回归测试的意义所在） ===")
    check("B 的风险点不少于 A（注水 → 触发更多规则）",
          stats["B"]["n"] >= stats["A"]["n"], (stats["A"]["n"], stats["B"]["n"]))
    check("B 的『口径缺失 + 夸大词』多于 A",
          stats["B"]["counts"].get("口径缺失", 0) + stats["B"]["counts"].get("夸大词", 0)
          > stats["A"]["counts"].get("口径缺失", 0) + stats["A"]["counts"].get("夸大词", 0),
          (stats["A"]["counts"], stats["B"]["counts"]))
    check("C 的『能力缺口』不少于 A（薄简历 → JD 覆盖不足）",
          stats["C"]["counts"].get("能力缺口", 0) >= stats["A"]["counts"].get("能力缺口", 0),
          (stats["A"]["counts"], stats["C"]["counts"]))
    check("只有 C 触发『硬性门槛』（1 年 < JD 的 3 年以上，HR 初筛硬伤）",
          stats["C"]["counts"].get("硬性门槛", 0) >= 1
          and stats["A"]["counts"].get("硬性门槛", 0) == 0
          and stats["B"]["counts"].get("硬性门槛", 0) == 0,
          {k: v["counts"] for k, v in stats.items()})
    check("B 的量化声明多于 C（注水型数字堆砌）",
          stats["B"]["quant"] > stats["C"]["quant"],
          (stats["B"]["quant"], stats["C"]["quant"]))
    check("A 的量化声明口径覆盖率高于 B",
          stats["A"]["cov"] / max(1, stats["A"]["quant"]) > stats["B"]["cov"] / max(1, stats["B"]["quant"]),
          (stats["A"], stats["B"]))

    print(f"\n===== 回归简历检查: {len(OK)} 通过 / {len(BAD)} 失败 =====")
    for tag in ("A", "B", "C"):
        s = stats[tag]
        print(f"  {tag}: 风险点 {s['n']} 条 {s['counts']} ｜ 量化 {s['quant']} 条"
              f"（带口径 {s['cov']}）｜ 空窗 {s['gaps']} 处")
    for b in BAD:
        print(f"  ! {b}")
    return 1 if BAD else 0


if __name__ == "__main__":
    sys.exit(main())
