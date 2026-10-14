#!/usr/bin/env python3
"""P1 解析层回归测试（纯函数调用，不使用子进程管道）。

用法:
  python tests/run_tests.py

覆盖：
  1. extract_text 对 .md 的抽取
  2. parse_resume 关键字段：姓名/年限/经历数/空窗/技能
  3. claims：量化声明与机制声明是否被单独抽出
  4. parse_jd：薪资区间/维度/业务场景/硬性要求/加分项/隐藏痛点
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from extract_text import extract          # noqa: E402
from parse_jd import parse_jd             # noqa: E402
from parse_resume import parse            # noqa: E402
from common import Log                    # noqa: E402

RESUME = os.path.join(HERE, "fixtures", "sample_resume.md")
JD = os.path.join(HERE, "fixtures", "sample_jd.txt")

PASS, FAIL = [], []


def check(name, cond, actual=None):
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    extra = "" if cond else f"  ← 实际: {actual!r}"
    print(f"  [{mark}] {name}{extra}")


def main():
    log = Log(quiet=True)

    print("\n=== 1. extract_text ===")
    r = extract(RESUME, log)
    check("md 抽取成功", r["method"] == "plain-text" and len(r["text"]) > 200, r["method"])
    check("中文无乱码", "张三" in r["text"] and "专业技能" in r["text"])

    print("\n=== 2. parse_resume ===")
    data = parse(r["text"], RESUME, r["method"], [])
    b = data["basic"]
    check("姓名", b["name"] == "张三", b["name"])
    check("手机号", b["phone"] == "13800138000", b["phone"])
    check("邮箱", b["email"] == "zhangsan@example.com", b["email"])
    check("GitHub", b["github"] == "github.com/zhangsan", b["github"])
    check("年限", b["years"] == 8, b["years"])
    check("工作经历数 >= 3", len(data["experiences"]) >= 3, len(data["experiences"]))
    check("技能组数 >= 3", len(data["skills"]) >= 3, len(data["skills"]))
    check("识别到精通/熟练", any(s["proficiency_claims"] for s in data["skills"]))
    check("教育背景", len(data["education"]) >= 1, data["education"])

    print("\n=== 3. 时间线空窗 ===")
    gaps = data["timeline_gaps"]
    check("检出 1 段空窗", len(gaps) == 1, gaps)
    if gaps:
        check("空窗约 7 个月", 6 <= gaps[0]["months"] <= 8, gaps[0]["months"])

    print("\n=== 4. claims 清单 ===")
    q = data["claims"]["quantitative"]
    m = data["claims"]["mechanism"]
    check("量化声明 >= 5 条", len(q) >= 5, len(q))
    check("机制声明 >= 2 条", len(m) >= 2, len(m))
    check("抓到 120 次提交", any(c["value"] == 120 for c in q), [c["value"] for c in q])
    check("抓到 QPS 3000（前缀型）", any("QPS" in str(c["unit"]) and c["value"] == 3000 for c in q),
          [(c["value"], c["unit"]) for c in q])
    check("抓到 45%", any(c["unit"] in ("%", "％") and c["value"] == 45 for c in q),
          [(c["value"], c["unit"]) for c in q])
    check("口径缺失被标记", any(not c["has_definition"] for c in q))
    check("机制关键词非空", all(c["keyword"] for c in m), [c["keyword"] for c in m])
    check("声明带编号", all(c["id"].startswith("C-") for c in q + m))

    print("\n=== 5. parse_jd ===")
    jd = parse_jd(open(JD, encoding="utf-8").read())
    check("薪资区间 20K-35K", jd["salary_range"] == [20000, 35000], jd["salary_range"])
    check("业务场景", jd["business_context"] == "供应链履约 / 仓储物流", jd["business_context"])
    check("识别微服务维度", "微服务" in jd["dimensions"], jd["dimensions"])
    check("识别分布式数据库维度", "分布式数据库" in jd["dimensions"], jd["dimensions"])
    check("识别中间件维度", "中间件" in jd["dimensions"], jd["dimensions"])
    check("硬性要求 >= 4 条", len(jd["must_have"]) >= 4, len(jd["must_have"]))
    check("加分项 >= 1 条", len(jd["nice_to_have"]) >= 1, jd["nice_to_have"])
    check("隐藏痛点 >= 1 条", len(jd["hidden_pain_points"]) >= 1, jd["hidden_pain_points"])
    check("硬性要求带维度标签", all(x["dimension"] for x in jd["must_have"]))

    print("\n=== 6. DOCX 格式 ===")
    docx_path = os.path.join(HERE, "tmp", "sample_resume.docx")
    try:
        import docx
        os.makedirs(os.path.dirname(docx_path), exist_ok=True)
        d = docx.Document()
        for ln in open(RESUME, encoding="utf-8").read().splitlines():
            d.add_paragraph(ln)
        d.save(docx_path)
        rd = extract(docx_path, log)
        check("docx 抽取成功", rd["method"] == "python-docx" and len(rd["text"]) > 200, rd["method"])
        dd = parse(rd["text"], docx_path, rd["method"], [])
        check("docx 姓名", dd["basic"]["name"] == "张三", dd["basic"]["name"])
        check("docx 经历 >= 3", len(dd["experiences"]) >= 3, len(dd["experiences"]))
    except ImportError:
        print("  [SKIP] 未安装 python-docx")

    print("\n=== 7. 真实 PDF（可选，需设 MOCK_INTERVIEW_TEST_PDF）===")
    real = os.environ.get("MOCK_INTERVIEW_TEST_PDF")
    if real and os.path.exists(real):
        rp = extract(real, log)
        check("PDF 走降级链成功", len(rp["text"]) > 500, rp["method"])
        dp = parse(rp["text"], real, rp["method"], [])
        check("PDF 抓到姓名非空", bool(dp["basic"]["name"]), dp["basic"]["name"])
        check("PDF 抓到经历 >= 3", len(dp["experiences"]) >= 3, len(dp["experiences"]))
        check("PDF 抓到量化声明 >= 10", len(dp["claims"]["quantitative"]) >= 10,
              len(dp["claims"]["quantitative"]))
    else:
        print("  [SKIP] 未提供真实 PDF 路径（设置环境变量后本组用例会真实执行）")

    print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
