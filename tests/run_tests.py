#!/usr/bin/env python3
"""P1 解析层 + P2 演练层回归测试（纯函数调用，不使用子进程管道）。

用法:
  python tests/run_tests.py

覆盖：
  1. extract_text 对 .md 的抽取
  2. parse_resume 关键字段：姓名/年限/经历数/空窗/技能
  3. claims：量化声明与机制声明是否被单独抽出
  4. parse_jd：薪资区间/维度/业务场景/硬性要求/加分项/隐藏痛点
  5. P2：风险点种子、轮次编排、模板渲染、契约校验、中断续跑、DoD 覆盖校验
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

from extract_text import extract          # noqa: E402
from parse_jd import parse_jd             # noqa: E402
from parse_resume import parse            # noqa: E402
from common import Log, write_json        # noqa: E402
import orchestrate as orch                # noqa: E402
import risks as risks_mod                 # noqa: E402

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

    print("\n=== 8. P2 风险种子 ===")
    res = parse(open(RESUME, encoding="utf-8").read(), RESUME, "md", [])
    jd = parse_jd(open(JD, encoding="utf-8").read())
    rs = risks_mod.seed_risks(res, jd)
    ids = [r["id"] for r in rs]
    check("风险点编号连续", ids == [f"R-{i:02d}" for i in range(1, len(rs) + 1)], ids)
    check("风险点非空", len(rs) >= 3, len(rs))
    check("含时间线风险", any(r["type"] == "时间线" for r in rs),
          [r["type"] for r in rs])
    check("严重度有区分", len({r["severity"] for r in rs}) >= 2,
          {r["severity"] for r in rs})
    check("每条都有追问方向", all(r.get("probe_hint") for r in rs))

    print("\n=== 9. P2 轮次编排（假跑：不调用模型）===")
    p2root = os.path.join(HERE, "tmp", "p2root")
    shutil.rmtree(p2root, ignore_errors=True)  # 清掉上一轮产物，避免假绿
    os.makedirs(os.path.join(p2root, "rounds"), exist_ok=True)
    write_json(os.path.join(p2root, "resume.json"), res)
    write_json(os.path.join(p2root, "jd.json"), jd)

    def ns(**kw):
        kw.setdefault("quiet", True)
        return argparse.Namespace(**kw)

    orch.cmd_init(ns(root=p2root, resume=os.path.join(p2root, "resume.json"),
                     jd=os.path.join(p2root, "jd.json"), max_rounds=3, max_risks=12))
    st = orch.load_state(p2root)
    plan_dims = sorted(d for v in st["plan"].values() for d in v["dims"])
    plan_risks = sorted(r for v in st["plan"].values() for r in v["risks"])
    check("计划覆盖全部硬性维度", plan_dims == sorted(st["must_dims"]), plan_dims)
    check("计划覆盖全部风险点", plan_risks == sorted(r["id"] for r in st["risks"]))
    check("每轮题量配额合理", all(len(v["dims"]) + 1 <= orch.QUESTIONS_PER_ROUND
                                  for v in st["plan"].values()))

    # 渲染：不能有未注入的占位符
    os.makedirs(os.path.join(p2root, "prompts"), exist_ok=True)
    for name in ("interviewer.md", "candidate.md"):
        with open(os.path.join(ROOT, "prompts", name), encoding="utf-8") as f:
            with open(os.path.join(p2root, "prompts", name), "w", encoding="utf-8") as g:
                g.write(f.read())
    orch.cmd_prepare(ns(root=p2root, round=1))
    body = open(os.path.join(p2root, "rounds", "round-1.interviewer.md"),
                encoding="utf-8").read()
    check("出题模板已完全渲染", "{{" not in body and len(body) > 800, len(body))

    def fake_round(round_no, drop_risk=None):
        """按计划造一份合法出题 + 答题，用于验证 commit/check。"""
        v = st["plan"][str(round_no)]
        qs = []
        for i, d in enumerate(v["dims"] or ["通用"], 1):
            qs.append({"qid": f"Q{i}", "dimension": d, "question": f"{d} 的问题 {i}",
                       "targets_risk": [], "expected_signals": ["有数字与口径"],
                       "followups": [f"追问 {d} 第 1 层", f"追问 {d} 第 2 层"]})
        for j, rid in enumerate(v["risks"], len(qs) + 1):
            if rid == drop_risk:
                continue
            qs.append({"qid": f"Q{j}", "dimension": "通用",
                       "question": f"关于 {rid} 的追问", "targets_risk": [rid],
                       "expected_signals": ["给出边界"], "followups": ["追一层", "追两层"]})
        items = [{"qid": q["qid"], "answer": "这是一个足够长的正式回答" * 3,
                  "duration_seconds": 80, "confidence": "基本确定", "self_flags": [],
                  "followups": [{"question": f, "answer": "追问的回答，说明边界与取舍"}
                                for f in q["followups"]]} for q in qs]
        qp = os.path.join(p2root, "rounds", f"round-{round_no}.interviewer.json")
        ap = os.path.join(p2root, "rounds", f"round-{round_no}.answers.json")
        write_json(qp, {"round": round_no, "theme": v["theme"], "questions": qs})
        write_json(ap, {"round": round_no, "items": items,
                        "candidate_questions": ["团队现状如何？", "对账是人工还是自动？"]})
        return qp, ap

    qp, ap = fake_round(1, drop_risk=st["plan"]["1"]["risks"][0])

    # 契约校验：题量不足必须被拒绝（不能把残缺出题喂给求职者 agent）
    good = json.load(open(qp, encoding="utf-8"))
    write_json(qp, {"round": 1, "theme": "x", "questions": good["questions"][:2]})
    try:
        orch.cmd_prepare_candidate(ns(root=p2root, round=1))
        rejected = False
    except SystemExit as e:
        rejected = e.code != 0
    check("题量不足被拒绝", rejected)
    write_json(qp, good)

    orch.cmd_prepare_candidate(ns(root=p2root, round=1))
    cbody = open(os.path.join(p2root, "rounds", "round-1.candidate.md"),
                 encoding="utf-8").read()
    check("答题模板注入问题且已渲染",
          "{{" not in cbody and "关于 R-" in cbody, len(cbody))

    orch.cmd_commit(ns(root=p2root, round=1, questions=qp, answers=ap))
    st = orch.load_state(p2root)
    check("commit 后状态更新", st["current_round"] == 1 and st["calls"] == 2, st["calls"])
    check("追问层数被记录", len(json.load(open(
        os.path.join(p2root, "rounds", "round-1.json"), encoding="utf-8"))["items"][0]["followups"]) == 2)

    # 覆盖不全时 check 必须失败（表格输出静音，断言已覆盖被检查的逻辑）
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            orch.cmd_check(ns(root=p2root))
            failed = False
        except SystemExit as e:
            failed = e.code != 0
    check("覆盖不全时 check 返回非 0", failed)

    # 中断续跑：直接 prepare 第 2 轮应能继续
    orch.cmd_prepare(ns(root=p2root, round=2))
    check("中断后可从下一轮继续",
          os.path.exists(os.path.join(p2root, "rounds", "round-2.interviewer.md")))

    # 补齐第 1 轮漏掉的风险点 + 第 2、3 轮
    v1 = dict(st["plan"]["1"])
    qp, ap = fake_round(1)
    orch.cmd_commit(ns(root=p2root, round=1, questions=qp, answers=ap))
    for r in (2, 3):
        qp, ap = fake_round(r)
        orch.cmd_commit(ns(root=p2root, round=r, questions=qp, answers=ap))
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            orch.cmd_check(ns(root=p2root))
            passed = True
        except SystemExit as e:
            passed = e.code == 0
    check("全部覆盖后 check 通过", passed)

    print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
