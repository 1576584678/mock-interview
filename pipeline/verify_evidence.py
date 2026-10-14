# -*- coding: utf-8 -*-
"""P4 验真层（护城河）：用**机器可验证的现实**去核对简历声明 → `out/evidence.json`。

与 P1/P2/P3/P5 不同，这一层面对的是"简历说的"与"代码/git 里真实存在的"之间的差距。
它输出的不是建议，而是**会被面试官（部分）当场戳穿的清单**。

两条核心设计（都来自一次真实的用户判断："面试官看不到的东西，不算作假"）：

1. **四档判决只看证据**：可信 / 存疑 / 与事实不符 / 无法验证。
2. **严重度由「面试可见性」决定，不由判决决定**：
   - `当场可判`（领域常识错误，面试官无需材料就能发现）→ 🔴
   - `追问可判`（机制类，深挖 1–3 层必然崩）→ 🟠
   - `需材料才可判`（提交数 / 测试数 / 代码里是否存在某机制）→ 🟡→⚪
   - `不可判`（旧项目、已离职，任何人无法取证）→ ⚪
   因此 `is_fabrication` **只可能由面试实录（P3）置位**，本层永远置 False：
   代码里搜不到 ≠ 面试里说了不存在的东西。

只读保证：所有 git 调用走 `_git()` 白名单（永不写仓库），并在取证前后比对
HEAD 与分支，任何变化都会报错退出。

用法::

    python pipeline/verify_evidence.py --root . \
        --repo coach=C:/project/面试教练 --repo qydn=C:/project/richinfo-qydn-server \
        --author liyuelin --out out/evidence.json
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import Log, die, read_text, write_json, write_text  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# 只允许这些 git 子命令（全部只读）——防止把"验真"变成"改仓库"
GIT_ALLOWED = {"rev-list", "rev-parse", "log", "shortlog", "status", "branch",
               "show", "ls-files", "grep", "cat-file", "diff"}

SCAN_EXT = {".java", ".kt", ".xml", ".yml", ".yaml", ".properties", ".sql", ".gradle"}
MAX_SCAN_FILES = 20000
# 注释行不算证据：命中的必须是**真实生效的代码**，否则会把注释掉的示例当成实现
COMMENT_PREFIX = ("//", "/*", "*", "#", "<!--", "--", ";;")

# 严重度矩阵：可见性优先，判决其次
SEVERITY = {
    ("当场可判", "与事实不符"): "🔴",
    ("当场可判", "存疑"): "🔴",
    ("追问可判", "与事实不符"): "🟠",
    ("追问可判", "存疑"): "🟠",
    ("追问可判", "无法验证"): "🟠",
    ("需材料才可判", "与事实不符"): "🟡",
    ("需材料才可判", "存疑"): "🟡",
    ("需材料才可判", "无法验证"): "⚪",
    ("需材料才可判", "可信"): "⚪",
    ("不可判", "无法验证"): "⚪",
    ("不可判", "存疑"): "⚪",
    ("不可判", "与事实不符"): "⚪",
    ("当场可判", "可信"): "⚪",
    ("追问可判", "可信"): "⚪",
}
INTERVIEW_ACTION = {
    "当场可判": "必改：这是常识级硬伤，面试官不需要任何材料就能发现",
    "追问可判": "必须准备：面试官深挖 1–3 层就会崩，要能讲清机制与失败路径",
    "需材料才可判": "建议改成真实数字即可（面试官不会拉你的仓库；它决定你敢不敢让对方看仓库）",
    "不可判": "只准备话术，不必改简历：任何人都无法取证",
}


# ------------------------------------------------------------------ 规则表
# 每条规则固定「面试可见性」——这是本层的核心判断，不随仓库结果变化。
# verify 类型：common_sense（静态判定）| git_count | test_count | grep（代码符号）
RULES = [
    {
        "id": "E-01", "kind": "tech_common_sense", "verify": "common_sense",
        "trigger": r"Oracle[^。；\n]{0,80}?[Bb]inlog|[Bb]inlog[^。；\n]{0,80}?Oracle",
        "visibility": "当场可判", "verdict": "与事实不符", "confidence": 0.97,
        "title": "Oracle 侧用 Binlog 取增量",
        "why": "binlog 是 MySQL 的机制；Oracle 侧的增量变更需要物化视图日志 / LogMiner / OGG 等。",
        "method": "领域常识判定（无需读代码）",
    },
    {
        "id": "E-02", "kind": "tech_common_sense", "verify": "common_sense",
        "trigger": r"RabbitMQ[^。；\n]{0,40}分区|分区[^。；\n]{0,20}RabbitMQ",
        "visibility": "当场可判", "verdict": "与事实不符", "confidence": 0.95,
        "title": "RabbitMQ 有分区概念",
        "why": "RabbitMQ 没有分区（partition）概念，分区是 Kafka 的概念。",
        "method": "领域常识判定（无需读代码）",
    },
    {
        "id": "E-03", "kind": "quantitative", "verify": "git_count", "risk_types": ["数字不实"],
        "trigger": r"累计[^。；\n]{0,10}?(\d+)\s*次提交|(\d+)\s*次提交",
        "visibility": "需材料才可判", "repo": "qydn",
        "method": "git rev-list --count --author=<作者> HEAD",
        "title": "提交次数",
    },
    {
        "id": "E-04", "kind": "quantitative", "verify": "test_count", "risk_types": ["数字不实"],
        "trigger": r"(\d+)\s*个\s*单元测试|单元测试[^。；\n]{0,10}?(\d+)\s*个",
        "visibility": "需材料才可判", "repo": "coach",
        "method": "统计 src/test 下 @Test 注解数（含 @Test 的类数）",
        "title": "单元测试数量",
    },
    {
        "id": "E-05", "kind": "mechanism", "verify": "grep",
        "trigger": r"业务指纹|指纹判重|指纹",
        "visibility": "需材料才可判", "repo": "coach",
        "method": "grep -ri 'fingerprint|指纹'",
        "patterns": r"fingerprint|指纹|hash\s*判重",
        "miss_verdict": "与事实不符", "miss_why": "代码中不存在该机制",
        "hit_verdict": "可信", "title": "业务指纹判重 / 复活机制",
    },
    {
        "id": "E-06", "kind": "mechanism", "verify": "grep", "risk_types": ["机制存疑"],
        "trigger": r"Redis\s*队列",
        "visibility": "追问可判", "repo": "coach",
        "method": "grep 阻塞消费/确认语义（BLPOP|BRPOP|@RabbitListener|@KafkaListener|consumerGroup）"
                  "，并统计 Redis List 原语",
        "patterns": r"BLPOP|BRPOP|@RabbitListener|@KafkaListener|consumerGroup|consumer_group",
        "miss_verdict": "存疑", "title": "「Redis 队列 + 异步任务调度」的队列语义",
        "miss_why": "代码里只有 Redis List 的原子追加 + 范围读取，没有阻塞消费 / 确认 / 消费位点语义；"
                    "说成「队列」会被追问 BLPOP 与消费位点时问崩",
        "hit_verdict": "可信",
    },
    {
        "id": "E-07", "kind": "mechanism", "verify": "grep", "risk_types": ["机制存疑"],
        "trigger": r"三级幂等键|幂等键",
        "visibility": "追问可判", "repo": "qydn",
        "method": "grep 唯一约束/唯一索引（uniqueKey|unique|唯一索引|uk_）",
        "patterns": r"uniqueKey|Unique|唯一索引|uk_|uniqueKeyName",
        "miss_verdict": "存疑", "title": "幂等键 + 唯一索引兜底",
        "miss_why": "代码里未找到唯一约束/唯一索引支撑，幂等只在应用层缓存或先查后写时会被追问竞态",
        "hit_verdict": "可信",
    },
    {
        "id": "E-08", "kind": "mechanism", "verify": "grep", "risk_types": ["机制存疑"],
        "trigger": r"熔断",
        "visibility": "追问可判", "repo": "qydn",
        "method": "grep 熔断实现（CircuitBreaker|Resilience4j|Sentinel|@SentinelResource）",
        "patterns": r"CircuitBreaker|Resilience4j|Sentinel|@SentinelResource|Hystrix",
        "miss_verdict": "存疑", "title": "平台级熔断",
        "miss_why": "代码里未找到熔断实现，简历里的「熔断」需要能说清阈值、半开与降级路径",
        "hit_verdict": "可信",
    },
    {
        "id": "E-09", "kind": "quantitative", "verify": "definition", "risk_types": ["口径缺失"],
        "trigger": r"准确率达?\s*(\d+(?:\.\d+)?)%|拦截率[^。；\n]{0,20}?(\d+(?:\.\d+)?)%|"
                   r"命中率[^。；\n]{0,20}?(\d+(?:\.\d+)?)%|效率提升[^。；\n]{0,20}?(\d+(?:\.\d+)?)%",
        "visibility": "追问可判", "verdict_if_missing_def": "无法验证",
        "method": "检查该量化声明是否带口径（分子 / 分母 / 统计周期）",
        "title": "百分比类声明的口径",
        "why": "简历里只有结果数字、没有口径——面试官一问分子分母就答不上，等于自曝注水",
    },
    {
        "id": "E-10", "kind": "capability", "verify": "no_source", "risk_types": ["夸大词"],
        "trigger": r"精通\s*JVM|精通\s*Java\s*并发|精通\s*Java",
        "visibility": "追问可判", "verdict": "无法验证",
        "method": "无机器可验证来源（代码不会写「精通」）",
        "title": "「精通 JVM / Java 并发」",
        "why": "能力断言无法用代码验证，只能用面试验货；「精通」越多，面试官验货越狠",
    },
    {
        "id": "E-11", "kind": "mechanism", "verify": "grep", "risk_types": ["夸大词"],
        "trigger": r"分库分表",
        "visibility": "追问可判", "repo": "qydn",
        "method": "grep 分库分表实现（ShardingSphere|sharding-jdbc|分片键|shardingStrategy|TableSharding）",
        "patterns": r"ShardingSphere|sharding-jdbc|shardingStrategy|TableSharding|tableSharding|分库分表|分片键",
        "miss_verdict": "存疑", "title": "分库分表落地",
        "miss_why": "代码里未找到分库分表实现，简历写「熟悉分库分表」但无生产落地证据",
        "hit_verdict": "可信",
    },
    {
        "id": "E-12", "kind": "quantitative", "verify": "no_source", "risk_types": ["时间线"],
        "trigger": r"(\d+)\s*年[^。；\n]{0,10}(经验|开发经验)",
        "visibility": "不可判", "verdict": "无法验证",
        "method": "无外部可取证来源（工作年限由劳动关系证明，不由代码决定）",
        "title": "工作年限",
        "why": "年限只能靠履历与社保材料核对，面试现场不可验证",
    },
]


# ------------------------------------------------------------------ 基础设施
def load_text(root: str, args, resume: dict) -> str:
    cand = args.text if os.path.isabs(args.text) else os.path.join(root, args.text)
    if os.path.exists(cand):
        return read_text(cand)
    blob = []

    def walk(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if not str(k).startswith("_"):
                    walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, str):
            blob.append(n)
    walk(resume)
    return "\n".join(blob)


def _git(repo: str, argv: list, timeout: int = 120) -> tuple:
    if not argv or argv[0] not in GIT_ALLOWED:
        die(f"拒绝执行非只读 git 子命令：{argv[:1]}（验真层只读，绝不修改被检查仓库）")
    cmd = ["git", "--no-optional-locks", "-C", repo] + argv
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        die("找不到 git 可执行文件——验真层需要 git 才能核对提交类声明")
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def repo_state(repo: str) -> dict:
    rc1, branch, _ = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    rc2, head, _ = _git(repo, ["rev-parse", "HEAD"])
    return {"branch": branch.strip() if rc1 == 0 else "?",
            "head": head.strip() if rc2 == 0 else "?"}


def scan_files(repo: str, exts=SCAN_EXT) -> list:
    """只读扫描：返回候选文本文件路径（跳过 .git / 构建产物）。"""
    out, n = [], 0
    skip = {".git", "node_modules", "target", "dist", "build", ".idea", "venv", ".venv"}
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in exts:
                out.append(os.path.join(dirpath, fn))
                n += 1
                if n >= MAX_SCAN_FILES:
                    return out
    return out


def grep_repo(repo: str, patterns: str, log: Log) -> tuple:
    """返回 (命中数, 命中样例, 扫描文件数)。"""
    rx = re.compile(patterns, re.I)
    hits, scanned = [], 0
    for p in scan_files(repo):
        scanned += 1
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    s = line.strip()
                    if s.startswith(COMMENT_PREFIX):
                        continue
                    if rx.search(line):
                        rel = os.path.relpath(p, repo).replace("\\", "/")
                        hits.append(f"{rel}:{i}: {s[:120]}")
                        if len(hits) >= 40:
                            return len(hits), hits, scanned
        except OSError:
            continue
    return len(hits), hits, scanned


def count_tests(repo: str, log: Log) -> dict:
    test_dir = os.path.join(repo, "src", "test")
    if not os.path.isdir(test_dir):
        return {"found": False, "reason": "没有 src/test 目录"}
    rx = re.compile(r"@Test\b")
    total, classes = 0, set()
    for p in scan_files(test_dir, {".java", ".kt"}):
        try:
            t = read_text(p)
        except OSError:
            continue
        n = len(rx.findall(t))
        if n:
            total += n
            classes.add(os.path.relpath(p, repo).replace("\\", "/"))
    return {"found": True, "tests": total, "classes": len(classes),
            "dir": os.path.relpath(test_dir, repo).replace("\\", "/")}


# ------------------------------------------------------------------ 判定
def common_sense(rule: dict, resume: dict) -> tuple:
    """静态判定：与代码无关，靠领域规则（保留入口便于将来挂规则库）。"""
    return rule["verdict"], rule.get("why", ""), {}


def run_rule(rule: dict, text: str, repos: dict, author: str, resume: dict,
             log: Log) -> dict:
    m = re.search(rule["trigger"], text, re.I)
    if not m:
        return {}
    hit_text = " ".join(m.group(0).split())
    expected = None
    if rule["verify"] == "git_count":
        nums = [g for g in m.groups() if g]
        expected = int(nums[0]) if nums else None
    elif rule["verify"] == "test_count":
        nums = [g for g in m.groups() if g]
        expected = int(nums[0]) if nums else None

    out = {
        "id": rule["id"], "kind": rule["kind"], "title": rule["title"],
        "claim_text": hit_text, "visibility": rule["visibility"],
        "method": rule.get("method", ""), "confidence": rule.get("confidence", 0.9),
        "is_fabrication": False,   # 只有面试实录（P3）才能置 True
        "expected": expected,
        "risk_types": rule.get("risk_types") or [],
    }
    repo_alias = rule.get("repo")
    repo_path = repos.get(repo_alias) if repo_alias else None

    if rule["verify"] == "common_sense":
        out["verdict"] = rule["verdict"]
        out["why"] = rule.get("why", "")
        out["observed"] = "领域规则判定（无需材料）"
        out["command"] = "（常识判定，无命令）"
        out["evidence"] = rule.get("why", "")

    elif rule["verify"] == "no_source":
        out["verdict"] = rule["verdict"]
        out["why"] = rule.get("why", "")
        out["observed"] = "无机器可验证来源"
        out["command"] = "（无）"
        out["evidence"] = rule.get("why", "")

    elif rule["verify"] == "definition":
        has_def = any(c.get("has_definition") for c in
                      ((resume.get("claims") or {}).get("quantitative") or [])
                      if str(c.get("text", "")) and
                      str(c.get("value", "")) in hit_text)
        out["verdict"] = "可信" if has_def else rule["verdict_if_missing_def"]
        out["observed"] = "声明自带口径" if has_def else "简历里没有分子/分母/统计周期"
        out["why"] = "" if has_def else rule.get("why", "")
        out["command"] = "（口径检查，无命令）"
        out["evidence"] = out["observed"]

    elif not repo_path:
        out["verdict"] = "无法验证"
        out["visibility"] = "不可判"
        out["observed"] = f"未提供仓库（--repo {repo_alias}=<路径>）"
        out["why"] = "没有可取证的材料，只能按话术准备，不判编造"
        out["command"] = "（需提供仓库）"
        out["evidence"] = out["observed"]

    elif rule["verify"] == "git_count":
        rc, stdout, stderr = _git(repo_path, ["rev-list", "--count",
                                             f"--author={author}", "HEAD"])
        actual = int(stdout.strip()) if rc == 0 and stdout.strip().isdigit() else None
        out["observed"] = actual
        out["command"] = f"git -C {repo_path} rev-list --count --author={author} HEAD"
        out["evidence"] = (stdout.strip() or stderr.strip() or "")[:200]
        if actual is None:
            out["verdict"] = "无法验证"
        elif expected is not None and actual == expected:
            out["verdict"] = "可信"
        else:
            out["verdict"] = "与事实不符"
        out["why"] = (f"简历写 {expected}，实测 {actual}" if actual is not None else "")

    elif rule["verify"] == "test_count":
        res = count_tests(repo_path, log)
        if not res.get("found"):
            out["verdict"] = "无法验证"
            out["observed"] = res.get("reason")
        else:
            out["observed"] = res["tests"]
            out["verdict"] = "可信" if expected == res["tests"] else "与事实不符"
            out["why"] = f"简历写 {expected}，实测 {res['tests']} 处 @Test（{res['classes']} 个测试类）"
        out["command"] = f"统计 {repo_path}/src/test 下 @Test 注解数"
        out["evidence"] = json.dumps(res, ensure_ascii=False)

    elif rule["verify"] == "grep":
        cnt, samples, scanned = grep_repo(repo_path, rule["patterns"], log)
        hit = cnt > 0
        out["observed"] = f"命中 {cnt} 处（扫描 {scanned} 个文件）"
        out["verdict"] = rule["hit_verdict"] if hit else rule["miss_verdict"]
        out["why"] = rule.get("miss_why", "") if not hit else ""
        out["command"] = f"grep -rE '{rule['patterns']}' {repo_path}"
        out["evidence"] = " || ".join(samples[:3])[:400]

    sev = SEVERITY.get((out["visibility"], out["verdict"]), "🟡")
    out["severity"] = sev
    out["interview_action"] = INTERVIEW_ACTION.get(out["visibility"], "")
    return out


def link_risks(claims: list, risks: list) -> None:
    """把验真结论挂到风险点编号上：**只认逐字包含关系**（归一化空白后），
    并要求重叠长度 ≥ 8 字，避免字面相似就乱挂。"""
    def norm(s):
        return re.sub(r"\s+", "", str(s or ""))

    for c in claims:
        ct = norm(c.get("claim_text"))
        ids = []
        if len(ct) >= 8:
            for r in risks:
                orig = norm(r.get("original"))
                probe = norm(r.get("probe_hint"))
                for other in (orig, probe):
                    if not other:
                        continue
                    if ct in other or (len(other) >= 8 and other in ct):
                        ids.append(r.get("id"))
                        break
        # 数字类声明：同数字（如 95% ↔ R-02）
        if not ids:
            nums = set(re.findall(r"\d+(?:\.\d+)?", ct))
            for r in risks:
                orig = norm(r.get("original"))
                if nums and nums <= set(re.findall(r"\d+(?:\.\d+)?", orig)):
                    ids.append(r.get("id"))
        # 同类型：规则表里显式声明了它能解释的风险点类型
        if not ids:
            by_type = {}
            for r in risks:
                by_type.setdefault(str(r.get("type") or ""), []).append(r.get("id"))
            for t in c.get("risk_types") or []:
                ids += by_type.get(t, [])
        c["risk_ids"] = [i for i in dict.fromkeys(ids) if i]


# ------------------------------------------------------------------ 主流程
def verify(args) -> dict:
    root = os.path.abspath(args.root)
    log = Log(quiet=args.quiet)
    resume_path = args.resume if os.path.isabs(args.resume) else os.path.join(root, args.resume)
    if not os.path.exists(resume_path):
        die(f"缺少简历结构化文件 {resume_path}——请先跑 P1 解析")
    resume = json.loads(read_text(resume_path))
    text = load_text(root, args, resume)

    repos, states = {}, {}
    for spec in args.repo or []:
        if "=" not in spec:
            die(f"--repo 需要 别名=路径 形式（收到 {spec}）")
        alias, path = spec.split("=", 1)
        path = os.path.abspath(path)
        if not os.path.isdir(os.path.join(path, ".git")):
            die(f"{path} 不是 git 仓库（验真层需要 git 历史或代码）")
        repos[alias.strip()] = path
        states[alias] = repo_state(path)

    taken_at = time.strftime("%Y-%m-%d %H:%M:%S")
    claims = []
    for rule in RULES:
        c = run_rule(rule, text, repos, args.author, resume, log)
        if c:
            claims.append(c)
            log.info(f"[{c['id']}] {c['severity']} {c['verdict']:5s} "
                     f"({c['visibility']}) {c['title']}")

    risks_path = args.risks if os.path.isabs(args.risks) else os.path.join(root, args.risks)
    risks = []
    if os.path.exists(risks_path):
        raw = json.loads(read_text(risks_path))
        risks = raw.get("risks") if isinstance(raw, dict) else raw
        risks = risks or []
    link_risks(claims, risks)

    # 只读证明：取证前后仓库 HEAD/分支必须完全一致
    after = {a: repo_state(p) for a, p in repos.items()}
    for alias, st in states.items():
        if after.get(alias) != st:
            die(f"仓库 {alias} 在取证过程中发生了变化（{st} → {after.get(alias)}）"
                f"——验真层必须只读，已中止")

    sev_rank = {"🔴": 0, "🟠": 1, "🟡": 2, "⚪": 3}
    vis_rank = {"当场可判": 0, "追问可判": 1, "需材料才可判": 2, "不可判": 3}
    claims.sort(key=lambda c: (vis_rank.get(c["visibility"], 9),
                               sev_rank.get(c["severity"], 9), c["id"]))

    summary = {
        "total": len(claims),
        "by_verdict": {v: sum(1 for c in claims if c["verdict"] == v)
                       for v in ("可信", "存疑", "与事实不符", "无法验证")},
        "by_visibility": {v: sum(1 for c in claims if c["visibility"] == v)
                          for v in ("当场可判", "追问可判", "需材料才可判", "不可判")},
        "must_fix": [c["id"] for c in claims if c["visibility"] == "当场可判"],
        "must_prepare": [c["id"] for c in claims if c["visibility"] == "追问可判"],
        "no_prep_needed": [c["id"] for c in claims
                           if c["visibility"] in ("需材料才可判", "不可判")],
        "fabrications": [c["id"] for c in claims if c.get("is_fabrication")],
    }
    return {
        "taken_at": taken_at,
        "author_filter": args.author,
        "repos": {a: dict(states[a], path=repos[a]) for a in repos},
        "readonly_verified": True,
        "claims": claims,
        "summary": summary,
        "_meta": {"generator": "pipeline/verify_evidence.py",
                  "rules": [r["id"] for r in RULES],
                  "resume": os.path.relpath(resume_path, root).replace("\\", "/"),
                  "note": "严重度由面试可见性决定；is_fabrication 只能由面试实录（P3）置位"},
    }


def to_md(ev: dict) -> str:
    o = ["# 代码 / git 验真清单（P4）", "",
         f"**取证时点**：{ev['taken_at']} ｜ **作者过滤**：`{ev['author_filter']}` ｜ "
         f"**只读校验**：{'通过' if ev['readonly_verified'] else '未通过'}", ""]
    if ev["repos"]:
        o.append("| 仓库别名 | 路径 | 分支 | HEAD |")
        o.append("|---|---|---|---|")
        for a, r in ev["repos"].items():
            o.append(f"| {a} | `{r['path']}` | `{r['branch']}` | `{r['head'][:12]}` |")
        o.append("")
    s = ev["summary"]
    o += [f"**结论分布**：可信 {s['by_verdict']['可信']} ｜ 存疑 {s['by_verdict']['存疑']} ｜ "
          f"与事实不符 {s['by_verdict']['与事实不符']} ｜ 无法验证 {s['by_verdict']['无法验证']}",
          "",
          "> **严重度由「面试可见性」决定**：当场可判（领域常识）🔴 ｜ 追问可判（机制讲不清）🟠 ｜ "
          "需材料才可判（提交数/测试数/代码里有无某机制）🟡→⚪ ｜ 不可判 ⚪。",
          "> 代码里搜不到 **不等于** 面试里说了不存在的东西——`is_fabrication` 只由面试实录置位。", ""]
    o += ["| 编号 | 可见性 | 判决 | 严重度 | 声明 | 实测 | 证据 | 关联风险点 |",
          "|---|---|---|---|---|---|---|---|"]
    for c in ev["claims"]:
        o.append(f"| {c['id']} | {c['visibility']} | {c['verdict']} | {c['severity']} | "
                 f"{c['claim_text'][:40]} | {str(c.get('observed'))[:40]} | "
                 f"`{str(c.get('command',''))[:60]}` | {'、'.join(c.get('risk_ids') or []) or '—'} |")
    o.append("")
    for c in ev["claims"]:
        o += [f"### {c['id']} · {c['title']} · {c['severity']} {c['verdict']}", "",
              f"- **可见性**：{c['visibility']}——{c['interview_action']}",
              f"- **简历原话**：{c['claim_text']}",
              f"- **实测**：{c.get('observed')}",
              f"- **方法**：{c.get('method')}",
              f"- **可复现命令**：`{c.get('command')}`"]
        if c.get("why"):
            o.append(f"- **说明**：{c['why']}")
        if c.get("evidence"):
            o.append(f"- **原始证据**：`{str(c['evidence'])[:300]}`")
        if c.get("risk_ids"):
            o.append(f"- **关联风险点**：{'、'.join(c['risk_ids'])}")
        o.append("")
    return "\n".join(o)


def main(argv=None):
    p = argparse.ArgumentParser(description="P4 验真层：简历声明 × 代码/git 交叉核对")
    p.add_argument("--root", default=".")
    p.add_argument("--resume", default="out/real/resume.json")
    p.add_argument("--text", default="out/resume.txt")
    p.add_argument("--risks", default="out/risks.json")
    p.add_argument("--repo", action="append", default=[],
                   help="重复提供：别名=路径，例如 coach=C:/project/面试教练")
    p.add_argument("--author", default="", help="git 作者过滤（提交类声明必需）")
    p.add_argument("--out", default="out/evidence.json")
    p.add_argument("--md-out", default="out/evidence.md")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    ev = verify(args)
    root = os.path.abspath(args.root)
    out = args.out if os.path.isabs(args.out) else os.path.join(root, args.out)
    write_json(out, ev)
    md = args.md_out if os.path.isabs(args.md_out) else os.path.join(root, args.md_out)
    write_text(md, to_md(ev))
    log = Log(quiet=args.quiet)
    log.info(f"验真清单: {out}")
    s = ev["summary"]
    log.info(f"必改（当场可判）{len(s['must_fix'])} 项 ｜ 必须准备（追问可判）"
             f"{len(s['must_prepare'])} 项 ｜ 无需准备 {len(s['no_prep_needed'])} 项 ｜ "
             f"编造判定 {len(s['fabrications'])} 项")
    return ev


if __name__ == "__main__":
    main()
    sys.exit(0)
