#!/usr/bin/env python3
"""JD → jd.json

用法:
  python parse_jd.py --in jd.txt --out jd.json

产出：
  salary_range / must_have / nice_to_have / dimensions / business_context
  / hidden_pain_points（用于面试官设计场景题）
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Log, base_parser, check_input, now, read_text, write_json  # noqa: E402

# ------------------------------------------------------------ 维度关键词表

DIMENSION_KEYWORDS = {
    "JVM": ["JVM", "GC", "内存模型", "jstack", "调优", "垃圾回收"],
    "并发": ["多线程", "并发", "线程池", "锁", "AQS", "CAS", "高并发"],
    "框架": ["Spring", "SpringBoot", "Spring Boot", "MyBatis", "Mybatis"],
    "微服务": ["SpringCloud", "Spring Cloud", "Nacos", "Gateway", "OpenFeign",
               "Dubbo", "注册中心", "配置中心", "服务治理"],
    "中间件": ["Redis", "Kafka", "RocketMQ", "RabbitMQ", "Elasticsearch", "ES",
               "Milvus", "消息队列", "MQ"],
    "分布式数据库": ["分库分表", "读写分离", "数据一致性", "TiDB", "MyCAT",
                     "ShardingSphere", "分布式事务", "主从", "分片"],
    "稳定性": ["限流", "降级", "熔断", "容灾", "监控", "告警", "Sentinel"],
    "业务场景": ["仓储", "物流", "履约", "供应链", "库存", "订单", "交易",
                 "WMS", "TMS", "拣货", "波次", "盘点"],
    "工程化": ["CI/CD", "CI", "Docker", "K8s", "Kubernetes", "单元测试",
               "Jenkins", "GitLab"],
    "AI": ["AI", "大模型", "LLM", "RAG", "向量", "算法", "Agent"],
}

BUSINESS_CONTEXTS = [
    (["供应链", "履约", "仓储", "物流", "运单", "快递", "WMS", "TMS"], "供应链履约 / 仓储物流"),
    (["电商", "秒杀", "商品", "订单", "库存"], "电商交易"),
    (["银行", "金融", "支付", "资金", "风控", "保险"], "金融"),
    (["教育", "课程", "题库", "学习"], "教育"),
    (["医疗", "医院", "病历"], "医疗"),
]

# --------------------------------------------------------- 隐藏痛点推导规则

PAIN_RULES = [
    (["数据一致性", "分库分表"], "跨库/跨系统数据一致性如何保证（对账、补偿、幂等）"),
    (["高并发", "库存"], "高并发下的超卖与库存扣减一致性"),
    (["仓储", "物流"], "作业效率与波次/路径优化；异常件与超时件的兜底"),
    (["限流", "降级"], "流量突增时的限流与降级策略，以及故障恢复"),
    (["读写分离"], "写后读一致性（主从延迟）的处理"),
    (["消息", "MQ"], "消息不丢不重与顺序性"),
    (["分片", "扩容"], "分片键选择与平滑扩容/迁移"),
    (["AI", "检索"], "非结构化数据接入与检索质量"),
]

MUST_RE = re.compile(r"(负责|要求|熟悉|掌握|精通|具备|能够|参与|设计|了解|有.{0,6}经验)")
NICE_RE = re.compile(r"(加分|优先|更佳|更好|额外|有.{0,12}者优先|bonus)", re.I)
SALARY_PATTERNS = [
    (r"(\d{2,3})\s*[kK]\s*[-~—–－至到]\s*(\d{2,3})\s*[kK]", 1000),
    (r"(\d{2,3})\s*[-~—–－至到]\s*(\d{2,3})\s*[kK]", 1000),
    (r"(\d{4,6})\s*[-~—–－至到]\s*(\d{4,6})", 1),
    (r"月薪\s*[:：]?\s*(\d{2,3})\s*[-~—–－至到]\s*(\d{2,3})\s*[kK]?", 1000),
]
ANNUAL_RE = re.compile(r"年薪\s*[:：]?\s*(\d{1,3})\s*[-~—–－至到]\s*(\d{1,3})\s*[wW万]")


def split_lines(text):
    out = []
    for raw in text.splitlines():
        t = re.sub(r"^[\s>*+·•●○\-—–\d.、)）]+", "", raw).strip()
        if t:
            out.append(t)
    return out


def parse_salary(text):
    m = ANNUAL_RE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return [round(lo * 10000 / 12), round(hi * 10000 / 12)], "年薪换算为月薪"
    for pat, mult in SALARY_PATTERNS:
        m = re.search(pat, text)
        if m:
            lo, hi = int(m.group(1)) * mult, int(m.group(2)) * mult
            # 按月薪口径校正：如 "20-35K" 已乘 1000
            if lo > 300000:  # 明显是年薪
                return [round(lo / 12), round(hi / 12)], "年薪换算为月薪"
            return [lo, hi], ""
    return None, ""


def parse_jd(text):
    lines = split_lines(text)
    warnings = []

    title = None
    for ln in lines[:4]:
        if re.search(r"(工程师|开发|架构师|专家|负责人)", ln) and len(ln) <= 40:
            title = ln
            break
    salary, salary_note = parse_salary(text)
    if not salary:
        warnings.append("未识别到薪资区间，薪资建议将退化为相对定位")

    must, nice = [], []
    for ln in lines:
        if len(ln) < 4:
            continue
        if NICE_RE.search(ln):
            nice.append(ln)
        elif MUST_RE.search(ln):
            must.append(ln)

    blob = text
    dims = [d for d, kws in DIMENSION_KEYWORDS.items()
            if any(k.lower() in blob.lower() for k in kws)]
    if not dims:
        warnings.append("未识别到任何技术维度，请检查 JD 文本是否完整")

    ctx = "未识别"
    for kws, name in BUSINESS_CONTEXTS:
        if any(k in blob for k in kws):
            ctx = name
            break

    pains = [desc for kws, desc in PAIN_RULES
             if sum(1 for k in kws if k.lower() in blob.lower()) >= 2]
    if not pains:
        pains = [desc for kws, desc in PAIN_RULES
                 if any(k.lower() in blob.lower() for k in kws)][:2]

    must_have = [{"dimension": guess_dimension(x), "text": x} for x in must]
    nice_to_have = nice
    if not must_have:
        warnings.append("未识别到硬性要求行，请检查 JD 格式")
    if not nice_to_have:
        warnings.append("未识别到加分项")

    return {
        "title": title,
        "salary_range": salary,
        "salary_note": salary_note,
        "must_have": must_have,
        "nice_to_have": nice_to_have,
        "dimensions": dims,
        "business_context": ctx,
        "hidden_pain_points": pains,
        "_meta": {
            "source": "",
            "generated_at": now(),
            "chars": len(text),
            "warnings": warnings,
        },
    }


def guess_dimension(line: str) -> str:
    best, hits = "通用", 0
    for d, kws in DIMENSION_KEYWORDS.items():
        n = sum(1 for k in kws if k.lower() in line.lower())
        if n > hits:
            best, hits = d, n
    return best


def main():
    ap = base_parser("JD → jd.json")
    args = ap.parse_args()
    check_input(args.inp)
    log = Log(args.quiet)
    log.step(f"解析 JD: {args.inp}")

    if args.dry_run:
        print(f"[dry-run] 将解析 {args.inp} → {args.out}")
        return

    text = read_text(args.inp)
    data = parse_jd(text)
    data["_meta"]["source"] = os.path.abspath(args.inp)
    write_json(args.out, data)

    log.info(f"岗位={data['title']} 薪资={data['salary_range']} "
             f"维度={data['dimensions']} 硬性={len(data['must_have'])} "
             f"加分={len(data['nice_to_have'])} 场景={data['business_context']}")
    for w in data["_meta"]["warnings"]:
        log.warn(w)


if __name__ == "__main__":
    main()
