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
import salary as salary_mod               # noqa: E402
import score as score_mod                 # noqa: E402
import report as report_mod               # noqa: E402
import transcript as transcript_mod       # noqa: E402
import export_resume as export_mod        # noqa: E402
import verify_links as links_mod          # noqa: E402

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

    print("\n=== 10. P3 评分器（rubric 驱动）===")
    rubric = score_mod.parse_rubric(os.path.join(ROOT, "rubric", "scoring.md"))
    flagdoc = score_mod.parse_flags(os.path.join(ROOT, "rubric", "red_flags.md"))
    dims, codes = rubric["dims"], flagdoc["flags"]
    check("rubric 六个维度解析完整",
          set(dims) == {"正确性", "深度", "一致性", "匹配度", "表达", "风险控制"}, dims)
    check("rubric 权重合计 1.00", abs(sum(dims.values()) - 1) < 1e-9, sum(dims.values()))
    check("通过概率 5 档", len(rubric["tiers"]) == 5, len(rubric["tiers"]))
    check("雷区编号 27 条", len(codes) == 27, len(codes))
    check("档位映射正确",
          score_mod.pass_probability(4.4, rubric["tiers"]) == "强推荐"
          and score_mod.pass_probability(2.0, rubric["tiers"]) == "明确不过")

    transcript = {"round": 1, "items": [
        {"qid": "Q1", "dimension": "JVM", "question": "讲讲 JVM 调优",
         "answer": "这块我没有生产落地经验，我的理解是先看 GC 日志判断是年轻代还是老年代停顿，"
                   "再决定要不要调参；没有真实压测数据之前我不会给具体数字。",
         "markers": ["🟡 示弱"], "targets_risk": ["R-01"],
         "followups": [{"question": "具体参数？", "answer": "参数记不准，不编。"}]},
        {"qid": "Q2", "dimension": "框架", "question": "熔断怎么配的",
         "answer": "熔断按承运商单独配置。", "markers": [], "targets_risk": [], "followups": []},
    ]}
    judg = {"round": 1, "items": [
        {"qid": "Q1", "band": 4, "dims": {k: 4 for k in dims},
         "deductions": [{"reason": "未给具体参数", "quote": "参数记不准，不编",
                         "red_flag": "E-02"}],
         "improvement": "准备一页 GC 调优话术"},
        {"qid": "Q2", "band": 4, "dims": {k: 4 for k in dims},
         "deductions": [{"reason": "作用域说错", "quote": "熔断按承运商单独配置",
                         "red_flag": "M-04"}],
         "improvement": "核对熔断挂载层级"},
    ]}
    rows = score_mod.score_round(transcript, judg, dims, codes, set(), rubric["rules"])
    q1 = next(r for r in rows if r["qid"] == "Q1")
    q2 = next(r for r in rows if r["qid"] == "Q2")
    check("示弱+给推理 → +0.5", q1["score"] == 4.5, q1["score"])
    check("M 类张冠李戴 → 最高 2 分", q2["score"] == 2.0, q2["score"])

    def mut(**kw):
        j = json.loads(json.dumps(judg))
        j["items"][0].update(kw)
        return j

    def rejects(j, label, risk_ids=None):
        try:
            score_mod.score_round(transcript, j, dims, codes, risk_ids or set(),
                                  rubric["rules"])
            return False
        except SystemExit:
            return True

    check("rubric 第 3 节硬性规则可解析", set(rubric["rules"]) ==
          {"编造", "常识错误", "矛盾", "不确定", "答不出"}, set(rubric["rules"]))
    hr_mut = mut(deductions=[], hard_rules=["矛盾"])
    q1_hr = next(r for r in score_mod.score_round(transcript, hr_mut, dims, codes,
                                                  set(), rubric["rules"]) if r["qid"] == "Q1")
    check("与自己简历矛盾 → 扣 1 分", q1_hr["score"] == 3.5, q1_hr["score"])
    check("rubric 外的硬性规则名 → 拒绝",
          rejects(mut(hard_rules=["感觉不行"]), "规则名"))

    check("引用原话改字 → 拒绝计分",
          rejects(mut(deductions=[{"reason": "x", "quote": "参数记不清楚"}]), "改字"))
    check("编造编号（F-01）→ 该题 1 分",
          next(r for r in score_mod.score_round(
              transcript, mut(deductions=[{"reason": "不存在", "quote": "参数记不准，不编",
                                           "red_flag": "F-01"}]), dims, codes, set(),
              rubric["rules"])
               if r["qid"] == "Q1")["score"] == 1.0)
    check("rubric 外维度 → 拒绝",
          rejects(mut(dims={"正确性": 4}), "缺维度"))
    check("非 0.5 步长维度分 → 拒绝",
          rejects(mut(dims={**{k: 4 for k in dims}, "深度": 3.7}), "步长"))
    check("档位给小数 → 拒绝", rejects(mut(band=3.5), "档位"))
    check("rubric 外雷区编号 → 拒绝",
          rejects(mut(deductions=[{"reason": "x", "quote": "参数记不准，不编",
                                   "red_flag": "X-99"}]), "编号"))
    check("不存在的风险点编号 → 拒绝",
          rejects(mut(deductions=[{"reason": "x", "quote": "参数记不准，不编",
                                   "risk_id": "R-99"}]), "风险点"))
    check("扣分不引用原话 → 拒绝",
          rejects(mut(deductions=[{"reason": "感觉不行"}]), "无原话"))

    agg = score_mod.aggregate(rows, dims, rubric["tiers"], codes, [1])
    expect = sum(agg["dimension_scores"][k] * w for k, w in dims.items())
    check("加权总分 = Σ(维度均分 × 权重)",
          abs(agg["weighted_total"] - round(expect, 1)) <= 0.05,
          (agg["weighted_total"], round(expect, 1)))
    check("档位分布被统计", sum(agg["band_distribution"].values()) == 2, agg["band_distribution"])

    print("\n=== 11. P3 评分卡落盘与 A1 对比 ===")
    sc_res = parse(open(RESUME, encoding="utf-8").read(), RESUME, "md", [])
    sc_jd = parse_jd(open(JD, encoding="utf-8").read())
    p3 = os.path.join(HERE, "tmp", "p3root")
    shutil.rmtree(p3, ignore_errors=True)
    os.makedirs(os.path.join(p3, "rounds"), exist_ok=True)
    os.makedirs(os.path.join(p3, "out", "real"), exist_ok=True)
    os.makedirs(os.path.join(p3, "judgments"), exist_ok=True)
    write_json(os.path.join(p3, "out", "real", "resume.json"), sc_res)
    write_json(os.path.join(p3, "out", "real", "jd.json"), sc_jd)
    p3_risks = risks_mod.seed_risks(sc_res, sc_jd)
    write_json(os.path.join(p3, "out", "risks.json"), p3_risks)
    write_json(os.path.join(p3, "rounds", "round-1.json"), transcript)
    write_json(os.path.join(p3, "judgments", "round-1.json"), judg)

    scpath = os.path.join(p3, "out", "scorecard.json")
    with contextlib.redirect_stdout(io.StringIO()):
        score_mod.main(["run", "--root", p3, "--out", scpath,
                        "--rubric", os.path.join(ROOT, "rubric", "scoring.md"),
                        "--red-flags", os.path.join(ROOT, "rubric", "red_flags.md")])
    check("评分卡 JSON 落盘", os.path.exists(scpath))
    check("评分表 CSV 落盘", os.path.exists(os.path.join(p3, "out", "scorecard.csv")))
    card = json.load(open(scpath, encoding="utf-8"))
    check("评分卡带 rubric 版本指纹", bool(card["meta"]["rubric_sha1"]))
    check("评分卡记录逐题明细", len(card["rows"]) == 2)

    # A1：同输入两次运行 → 偏差 0
    b_path = os.path.join(p3, "out", "scorecard-b.json")
    shutil.copyfile(scpath, b_path)
    with contextlib.redirect_stdout(io.StringIO()):
        d = score_mod.cmd_compare(argparse.Namespace(a=scpath, b=b_path, tolerance=0.3))
    check("同输入两次运行偏差 0（A1）", d == 0, d)

    # A1：超阈值必须失败
    bad = json.load(open(scpath, encoding="utf-8"))
    bad["summary"]["weighted_total"] = round(bad["summary"]["weighted_total"] - 0.6, 1)
    write_json(b_path, bad)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            score_mod.cmd_compare(argparse.Namespace(a=scpath, b=b_path, tolerance=0.3))
        exceeded = False
    except SystemExit as e:
        exceeded = e.code != 0
    check("偏差超 0.3 → 判 A1 失败", exceeded)

    print("\n=== 12. P3 薪资与报告 ===")
    sal = salary_mod.build(sc_jd, card, sc_res, p3_risks)
    lo, hi = sal["jd_range"]
    rec = sal["recommended"]
    check("薪资只给区间（3 个数）", set(rec) == {"low", "mid", "high"}, set(rec))
    check("薪资落在 JD 区间内", lo <= rec["low"] <= rec["high"] <= hi,
          (lo, rec, hi))
    check("推理链每步都有依据", all(c.get("basis") for c in sal["reasoning_chain"])
          and len(sal["reasoning_chain"]) >= 4)
    check("依据声明明确未用市场数据", "未使用任何市场行情" in sal["basis_statement"])
    check("报价策略含先问预算与底线", len(sal["negotiation"]) >= 4)

    try:
        salary_mod.build({"salary_range": None}, card, sc_res, p3_risks)
        no_range = False
    except SystemExit:
        no_range = True
    check("JD 无薪资区间 → 拒绝编数字", no_range)

    try:
        salary_mod.guard("这个岗位市场价大概 30K", "测试")
        guarded = False
    except SystemExit:
        guarded = True
    check("无出处行情措辞 → 拒绝产出", guarded)
    try:
        salary_mod.guard("本建议未使用任何市场行情数据", "测试")
        neg_ok = True
    except SystemExit:
        neg_ok = False
    check("免责声明（带否定语）不被误伤", neg_ok)

    with contextlib.redirect_stdout(io.StringIO()):
        salary_mod.cmd_run(argparse.Namespace(
            root=p3, jd=os.path.join("out", "real", "jd.json"),
            scorecard=os.path.join("out", "scorecard.json"),
            resume=os.path.join("out", "real", "resume.json"),
            risks=os.path.join("out", "risks.json"),
            out=os.path.join("out", "salary.json"), quiet=True))
    check("薪资 JSON 落盘", os.path.exists(os.path.join(p3, "out", "salary.json")))

    # 闭环：样例只追问了 R-01，--strict 必须失败
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            report_mod.main(["--root", p3, "--only", "04", "--strict", "--quiet"])
        strict_failed = False
    except SystemExit as e:
        strict_failed = e.code != 0
    check("风险点未全部被追问时 --strict 失败", strict_failed)

    with contextlib.redirect_stdout(io.StringIO()):
        report_mod.main(["--root", p3, "--only", "04", "--quiet"])
    rp = os.path.join(p3, "04_简历风险点.md")
    body = open(rp, encoding="utf-8").read()
    check("04 覆盖全部风险点编号",
          all(r["id"] in body for r in p3_risks), len(p3_risks))
    check("04 标注了被追问位置", "第1轮Q1" in body)
    check("04 含闭环校验段", "闭环校验" in body)
    check("04 无未渲染占位符", "{{" not in body)

    # 02 复盘报告：渲染 + 无占位符 + 分数来自 scorecard
    with contextlib.redirect_stdout(io.StringIO()):
        report_mod.main(["--root", p3, "--only", "02", "--quiet",
                         "--resume-label", "样例简历"])
    rv = open(os.path.join(p3, "02_复盘报告.md"), encoding="utf-8").read()
    check("02 无未渲染占位符", "{{" not in rv, rv.count("{{"))
    check("02 含加权总分", str(card["summary"]["weighted_total"]) in rv)
    check("02 含分轮汇总与维度明细", "分轮汇总" in rv and "维度明细" in rv)

    with contextlib.redirect_stdout(io.StringIO()):
        report_mod.main(["--root", p3, "--only", "03", "--quiet"])
    sal_md = open(os.path.join(p3, "03_薪资建议.md"), encoding="utf-8").read()
    check("03 无未渲染占位符", "{{" not in sal_md)
    check("03 含结论/推理链/依据声明/报价策略",
          all(k in sal_md for k in ("结论", "推理链", "依据声明", "报价策略")))

    # 改写建议不得新增事实
    r0 = p3_risks[0]
    blob = json.dumps(sc_res, ensure_ascii=False)
    try:
        report_mod.validate_rewrite(r0, "同步准确率提升到 99.99%", blob)
        no_new = False
    except SystemExit:
        no_new = True
    check("改写建议引入新数字 → 拒绝", no_new)
    try:
        report_mod.validate_rewrite(r0, "把该句改写为可验证的表述", blob)
        ok_rw = True
    except SystemExit:
        ok_rw = False
    check("不含新事实的改写建议 → 接受", ok_rw)

    # ---------------------------------------------------------------- P5
    print("\n=== 13. P5 实录渲染（01） ===")
    p5 = os.path.join(HERE, "tmp", "p5")
    shutil.rmtree(p5, ignore_errors=True)
    os.makedirs(os.path.join(p5, "rounds"), exist_ok=True)
    r1 = {
        "round": 1, "theme": "开场与动机", "ts": "2026-01-01 10:00:00",
        "items": [{
            "qid": "Q1", "dimension": "开场与动机",
            "question": "介绍一下你自己。", "answer": "我做过 SSO 双会话。",
            "targets_risk": ["R-01"],
            "expected_signals": ["时间线连续"],
            "markers": ["🟡 示弱"],
            "self_flags": ["本回答依据简历的 XX 段，空窗期无简历依据已显式示弱"],
            "confidence": "基本确定", "duration_seconds": 90,
            "followups": [{"question": "空窗期产出？", "answer": "拿不出可审计的交付物。"}],
        }],
        "candidate_questions": ["团队怎么分工？"],
    }
    r2 = dict(r1, round=2, theme="中间件",
              items=[dict(r1["items"][0], qid="Q2", targets_risk=["R-02"])])
    write_json(os.path.join(p5, "rounds", "round-1.json"), r1)
    # 中间产物：格式不同，混进来会把轮次/题数算错
    write_json(os.path.join(p5, "rounds", "round-1.answers.json"), {"answers": [{"qid": "Q1"}]})
    write_json(os.path.join(p5, "rounds", "round-1.interviewer.json"), {"questions": []})
    write_json(os.path.join(p5, "rounds", "round-2.json"), r2)
    write_json(os.path.join(p5, "rounds", "state.json"),
               {"position": "后端工程师", "probed": {"R-01": True, "R-02": True},
                "risks": [{"id": "R-01"}, {"id": "R-02"}], "calls": 2,
                "started_at": "2026-01-01T10:00:00", "finished_at": "2026-01-01T10:20:00"})
    with contextlib.redirect_stdout(io.StringIO()):
        tpath = transcript_mod.main(["--root", p5, "--resume-label", "样例.md", "--quiet"])
    t01 = open(tpath, encoding="utf-8").read()
    check("只认 round-N.json（不被 answers/interviewer 污染）",
          "**轮次**：2 轮" in t01 and "**题目数**：2" in t01)
    check("面试官题面逐字保留", "介绍一下你自己。" in t01 and "空窗期产出？" in t01)
    check("候选人作答逐字保留", "拿不出可审计的交付物。" in t01)
    check("短标记进入作答行", "🟡 示弱" in t01)
    check("自评依据长文本单独成段（不进标记统计）",
          "候选人自评依据" in t01 and "本回答依据简历的 XX 段" in t01)
    check("标记统计只剩短标记", "标记分布 | 🟡 示弱×2" in t01.replace("｜", "|"))
    check("带 [R-xx] 编号", "[R-01]" in t01 and "[R-02]" in t01)
    check("含候选人反问环节", "候选人反问" in t01 and "团队怎么分工？" in t01)
    check("含追问链层次", "**追问 1（面试官）**" in t01)

    print("\n=== 14. P5 简历导出（05） ===")
    sample_resume = {
        "basic": {"name": "张三", "title": "后端开发工程师", "years": "10 年",
                  "phone": "13800000000", "email": "z@example.com"},
        "summary": ["后端架构：做过 12 个系统接入", "数据一致性：同步准确率达 99.98%"],
        "skills": [{"category": "数据库", "items": ["熟悉分库分表、索引优化"]}],
        "experiences": [{"company": "某公司", "title": "工程师", "start": "2024.01",
                         "end": "至今", "raw": "2024.01 - 至今", "stack": ["Java"],
                         "highlights": ["负责企业级平台的", "跨系统同步模块。",
                                        "参与冷热分离改造。"]}],
        "projects": [{"name": "同步平台", "period": "2024.01 - 至今", "role": "",
                      "stack": ["Java"],
                      "highlights": ["负责企业级平台的", "跨系统同步模块。"]}],
        "education": ["某大学 本科"],
        "timeline_gaps": [{"from": "2023.01", "to": "2023.04", "months": 3}],
    }
    write_json(os.path.join(p5, "out", "real", "resume.json"), sample_resume) \
        if os.makedirs(os.path.join(p5, "out", "real"), exist_ok=True) is None else None
    write_json(os.path.join(p5, "out", "risks.json"), {
        "risks": [{"id": "R-01", "type": "口径缺失", "severity": "🟠",
                   "why": "只有数字没有口径"},
                  {"id": "R-02", "type": "能力缺口", "severity": "🟡",
                   "why": "无分库分表落地"}]})
    write_json(os.path.join(p5, "judgments", "fixes.json"), {
        "fixes": [{"risk_id": "R-01", "replace_from": "同步准确率达 99.98%",
                   "replace_to": "同步准确率达 99.98%（口径：一致行数 ÷ 比对总行数）",
                   "note": "补口径"},
                  {"risk_id": "R-09", "replace_from": "这句话原文里不存在",
                   "replace_to": "改不掉", "note": "应当被跳过"}],
        "unfixable": [{"risk_id": "R-02", "reason": "能力缺口不写进简历"}]})
    with contextlib.redirect_stdout(io.StringIO()):
        summ = export_mod.main(["--root", p5, "--quiet"])
    h05 = open(summ["html"], encoding="utf-8").read()
    check("05 无未渲染占位符", "{{" not in h05)
    check("05 成对替换生效", "（口径：一致行数 ÷ 比对总行数）" in h05)
    check("05 未命中的替换被跳过而非报错",
          any("找不到" in s["reason"] for s in summ["skipped"]), summ["skipped"])
    check("05 改动说明引用风险点编号", "[R-01]" in h05 and "[R-02]" in h05)
    check("05 打印时隐藏改动说明",
          ".changelog{display:none!important;" in h05.replace(" ", ""))
    check("05 含 A4 打印规则", "size:A4" in h05)
    check("05 含分页保护规则",
          "page-break-after:avoid" in h05 and "page-break-inside:avoid" in h05)
    check("05 去重：与工作经历重复的项目被合并",
          "同步平台" not in h05.split('<h2 class="sec">项目经历</h2>')[-1]
          if '<h2 class="sec">项目经历</h2>' in h05 else True)
    check("解析碎片被合并成完整句", "负责企业级平台的跨系统同步模块。" in h05)
    check("时间线占位符不含『改动说明』字样（避免 PDF 泄漏误判）",
          "本次改动说明" not in h05.split('<div class="page">')[-1])

    # 新增数字必须整份拒绝
    bad = json.loads(json.dumps(sample_resume, ensure_ascii=False))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            export_mod.apply_fixes(bad, [{"risk_id": "R-01", "replace_from": "12 个系统接入",
                                          "replace_to": "34 个系统接入"}], Log(quiet=True))
        rejected = False
    except SystemExit:
        rejected = True
    check("replace_to 引入新数字 → 整份拒绝", rejected)

    # PDF 人工闸门
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            export_mod.main(["--root", p5, "--pdf", "--quiet"])
        gated = False
    except SystemExit:
        gated = True
    check("--pdf 未加 --confirm → 拒绝导出（人工闸门）", gated)

    # 去重与截断
    check("重叠度计算：同文 1.0 / 无关 0.0",
          export_mod.overlap_ratio("abcdefghijkl", "abcdefghijkl") == 1.0
          and export_mod.overlap_ratio("abcdefghijkl", "012345678901") == 0.0)
    dup = {"experiences": [{"company": "A", "highlights": ["负责企业级平台跨系统同步模块改造"]}],
           "projects": [{"name": "同内容", "highlights": ["负责企业级平台跨系统同步模块改造"]}]}
    dropped = export_mod.drop_duplicate_projects(dup, Log(quiet=True))
    check("高重叠项目被合并", len(dropped) == 1 and len(dup["projects"]) == 0, dropped)
    tl = {"experiences": [{"company": "A", "highlights": ["字" * 50, "字" * 50]}], "projects": []}
    tr = export_mod.trim_long_items(tl, 60, Log(quiet=True))
    check("截断留下可见标记且被记录",
          len(tr) == 1 and "已截断" in tl["experiences"][0]["highlights"][-1], tr)

    print("\n=== 15. P5 五件套闭环校验（A5） ===")
    for name, text in (("01_面试对话实录.md", "R-01 R-02"),
                       ("02_复盘报告.md", "R-01 R-02"),
                       ("03_薪资建议.md", "R-01"),
                       ("04_简历风险点.md", "R-01 R-02"),
                       ("05_改后简历.html", "R-01 R-02")):
        open(os.path.join(p5, name), "w", encoding="utf-8").write(text)
    found = links_mod.scan(p5)
    res = links_mod.check(p5, ["R-01", "R-02"], found, strict=True)
    check("五件套齐全 + 全覆盖 → A5 通过", res["ok"], res)
    check("矩阵识别每个产物的引用", found["03_薪资建议.md"] == ["R-01"], found)
    # 悬空编号
    open(os.path.join(p5, "02_复盘报告.md"), "w", encoding="utf-8").write("R-01 R-02 R-99")
    res2 = links_mod.check(p5, ["R-01", "R-02"], links_mod.scan(p5), strict=True)
    check("引用不存在的编号 → 悬空被识别", res2["dangling"] == ["R-99"] and not res2["ok"], res2)
    # 孤岛 + 未进 04：R-02 从 04 里去掉
    for name, text in (("02_复盘报告.md", "R-01"), ("01_面试对话实录.md", ""),
                       ("05_改后简历.html", ""), ("04_简历风险点.md", "R-01")):
        open(os.path.join(p5, name), "w", encoding="utf-8").write(text)
    res3 = links_mod.check(p5, ["R-01", "R-02"], links_mod.scan(p5), strict=True)
    check("只出现在 1 个产物 → 判为孤岛", "R-02" in res3["orphan"], res3["orphan"])
    check("缺失的编号被识别为未进 04", "R-02" in res3["not_in_04"], res3["not_in_04"])

    print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
