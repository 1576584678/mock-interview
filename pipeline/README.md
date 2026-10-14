# pipeline · 脚本接口约定

> 本目录实现进度：**P1、P2、P3、P4、P5 已完成**（`common.py` / `extract_text.py` / `parse_resume.py` /
> `parse_jd.py` / `risks.py` / `orchestrate.py` / `score.py` / `salary.py` / `report.py` /
> `transcript.py` / `export_resume.py` / `verify_links.py` / `verify_evidence.py`）；
> 只剩 P6（打包 / 示例 / 安装说明）待做。
> 所有脚本遵循同一约定：**读 JSON / 写 JSON，参数走命令行，不依赖第三方库（优先）。**

## 运行方式

```bash
# P1 解析
python pipeline/parse_resume.py --in 简历.pdf --out out/resume.json --text-out out/resume.txt
python pipeline/parse_jd.py     --in jd.txt   --out out/jd.json

# P2 演练编排（本脚本不调用模型，模型调用由宿主 agent 执行）
python pipeline/orchestrate.py init              --resume out/resume.json --jd out/jd.json --root .
python pipeline/orchestrate.py prepare           --round 1 --root .
python pipeline/orchestrate.py prepare-candidate --round 1 --root .
python pipeline/orchestrate.py commit            --round 1 --root . \
      --questions rounds/round-1.interviewer.json --answers rounds/round-1.answers.json
python pipeline/orchestrate.py check             --root .   # DoD 覆盖校验，未达标返回 1

# P3 评估（评审 agent 把判分写进 judgments/，脚本负责校验与聚合）
python pipeline/score.py run     --root . --judgments judgments --out out/scorecard.json
python pipeline/score.py compare --a out/scorecard.json --b out/scorecard-b.json   # 验收 A1
python pipeline/salary.py --root . --out out/salary.json
python pipeline/report.py --root . --only all --strict   # 02/03/04 三件套

# P5 产出层（五件套补齐 + 编号闭环校验）
python pipeline/transcript.py --root . --resume-label 简历.pdf      # 01
python pipeline/export_resume.py --root . --trim-long-items 900     # 05 HTML
python pipeline/export_resume.py --root . --pdf --confirm           # 05 PDF（人工闸门）
python pipeline/verify_links.py --root . --strict                   # 验收 A5

# P4 验真层（只读：绝不修改被检查仓库）
python pipeline/verify_evidence.py --root . --resume out/real/resume.json \
      --repo coach=C:/project/面试教练 --repo qydn=C:/project/richinfo-qydn-server \
      --author liyuelin --out out/evidence.json
python pipeline/report.py --root . --only 04 --evidence out/evidence.json   # 04 带代码验真列

python tests/run_tests.py                     # 143 项回归测试
```

中文环境建议先设 `PYTHONUTF8=1`，避免控制台编码导致的乱码。

## 实现状态

| 脚本 | 阶段 | 状态 | 说明 |
|---|---|---|---|
| `common.py` | P1 | ✅ | 原子写文件、`as_text` 展平、日志走 stderr、`--dry-run` |
| `extract_text.py` | P1 | ✅ | 多级降级：pdftotext → LibreOffice → 内置解析 |
| `parse_resume.py` | P1 | ✅ | 章节切分、经历/项目/技能/教育、空窗检测、claims 清单 |
| `parse_jd.py` | P1 | ✅ | 章节化归类（要求/职责/加分，零丢失）、薪资、维度、隐藏痛点 |
| `risks.py` | P2 | ✅ | 风险点种子（编号 R-01…，P2 追问目标与 P3 报告共用）|
| `orchestrate.py` | P2 | ✅ | 轮次计划、提示词注入、契约校验、状态落盘、DoD 校验 |
| `score.py` | P3 | ✅ | rubric 驱动评分：维度/权重/雷区/硬性规则全部从 rubric 解析并强校验；无原话不扣分；A1 漂移对比 |
| `salary.py` | P3 | ✅ | 薪资区间 + 推理链 + 依据声明；禁止无出处行情数据（否定感知守卫）|
| `report.py` | P3 | ✅ | 渲染 `02/03/04` 三件套；改写建议"不新增数字"自动校验；编号闭环检查 |
| `transcript.py` | P5 | ✅ | 渲染 `01_面试对话实录.md`：逐字稿 + 追问链层次 + 标记 + 反问 |
| `export_resume.py` | P5 | ✅ | 渲染 `05_改后简历.html`：成对替换（不新增数字）、改动说明打印隐藏、PDF 人工闸门 |
| `verify_links.py` | P5 | ✅ | 验收 A5：五件套编号引用闭环（无悬空 / 无孤岛 / 全部进 04） |
| `verify_evidence.py` | P4 | ✅ | 代码/git 交叉核对：四档判决 + **面试可见性决定严重度**；只读白名单 + 前后 HEAD 比对 + 注释不算证据 |
| `export_resume.py`（P4 复核） | P4 | ⬜ | 待开发 |

## 约定

```
python pipeline/<script>.py --in <path> --out <path> [options]
```

- 字符集统一 UTF-8；输出文件用 `UTF-8 (no BOM)`。
- 失败时退出码非 0，并把错误写到 stderr，**不产生残缺的输出文件**。
- 所有脚本必须支持 `--dry-run`（只打印将要做什么，不写文件）。

---

## 1. `parse_resume.py`（P1）

**输入**：简历文件（`.pdf` / `.docx` / `.md` / `.txt`）
**输出**：`resume.json`

```json
{
  "basic": { "name": "", "title": "", "years": 12, "contacts": {} },
  "skills": [ { "category": "语言", "items": ["Java", "SQL"], "proficiency_claims": ["精通"] } ],
  "experiences": [
    { "company": "", "title": "", "start": "2026.07", "end": "至今", "stack": [], "highlights": [] }
  ],
  "projects": [ { "name": "", "period": "", "role": "", "highlights": [] } ],
  "education": [],
  "timeline_gaps": [ { "from": "", "to": "", "months": 3 } ],
  "claims": {
    "quantitative": [ { "id": "C-01", "text": "累计输出 150 次提交", "value": 150, "unit": "次" } ],
    "mechanism":    [ { "id": "C-02", "text": "引入业务指纹判重与复活机制", "keyword": "指纹" } ]
  }
}
```

**要点**
- `claims` 是本脚本的**核心产出**：把简历里的量化声明与机制声明单独抽出来，供 P4 验真使用。
- PDF 降级链：`pdftotext` → LibreOffice 转 txt → 内置轻量解析 → 报错并要求用户粘贴。
- 时间线自动排序并检出空窗（连续区间之间的间隔 ≥ 2 个月）。

---

## 2. `parse_jd.py`（P1）

**输入**：JD 文本或文件
**输出**：`jd.json`

```json
{
  "title": "", "salary_range": [20000, 35000],
  "must_have": [ { "dimension": "分布式数据库", "text": "分库分表、读写分离、数据一致性", "weight": 0.3 } ],
  "nice_to_have": [ "仓储物流经验" ],
  "dimensions": [ "JVM", "并发", "中间件", "分布式数据库", "微服务", "业务场景" ],
  "business_context": "供应链履约 / 仓储物流",
  "hidden_pain_points": [ "跨系统数据一致性", "高并发下超卖" ]
}
```

**要点**：`hidden_pain_points` 用于面试官设计场景题，是本脚本相对"关键词提取"的增量价值。

---

## 3. `verify_evidence.py`（P4 · 护城河）✅

**输入**：`--resume`（结构化简历，取其 `claims` 与正文）、`--repo 别名=路径`（可重复）、
`--author`（提交类声明必需）、`--risks`（用于挂编号）
**输出**：`out/evidence.json` + `out/evidence.md`

```bash
python pipeline/verify_evidence.py --root . --resume out/real/resume.json \
      --repo coach=C:/project/面试教练 --repo qydn=C:/project/richinfo-qydn-server \
      --author liyuelin --out out/evidence.json
```

```json
{
  "taken_at": "2026-02-09 14:31:07",
  "author_filter": "liyuelin",
  "repos": {"qydn": {"path": "C:/project/richinfo-qydn-server", "branch": "main", "head": "…"}},
  "readonly_verified": true,
  "claims": [
    {
      "id": "E-03", "kind": "quantitative", "title": "提交次数",
      "claim_text": "累计输出 150 次提交", "expected": 150, "observed": 148,
      "verdict": "与事实不符", "visibility": "需材料才可判", "severity": "🟡",
      "is_fabrication": false,
      "method": "git rev-list --count --author=<作者> HEAD",
      "command": "git -C <repo> rev-list --count --author=liyuelin HEAD",
      "evidence": "148", "risk_ids": []
    }
  ],
  "summary": {"by_verdict": {}, "by_visibility": {}, "must_fix": [], "must_prepare": [],
              "no_prep_needed": [], "fabrications": []}
}
```

**判决四档**：`可信` / `存疑` / `与事实不符` / `无法验证`

**严重度 = f(可见性, 判决)**，可见性四档：`当场可判` 🔴 ／ `追问可判` 🟠 ／
`需材料才可判` 🟡→⚪ ／ `不可判` ⚪。**`is_fabrication` 在本层恒为 `False`**——
代码里搜不到不等于面试里说了不存在的东西；只有面试实录（P3）能判编造。

**硬约束（均有测试覆盖）**
- **只读**：git 子命令走白名单（`rev-list/rev-parse/log/shortlog/status/branch/show/ls-files/grep/cat-file/diff`），
  其余直接报错退出；取证前后比对分支与 HEAD，任何变化即中止。
- 每条结论必须带 `command`（可复现命令）+ `evidence`（原始命中片段），用户能自己验证。
- 记录取证时点、分支、HEAD commit（仓库状态会变）。
- **`无法验证` 不得被判为编造。**
- **注释行不算证据**（`//` `/*` `*` `#` `--` … 开头的行跳过），只在代码/配置类扩展名里找符号。
- 关键词要能自证：`shard` 这种宽泛词会命中 `requestedShardCount`（索引分片数）→ 必须写成
  `ShardingSphere|sharding-jdbc|分片键|…` 这类不可歧义的符号集。
- 作者名需向用户确认（同一人多邮箱会漏统计）。

**回归基线**：`docs/EVIDENCE.md` 第 5 节的 7 条实证案例（真实仓库上复现 6/7，
未触发的那条由夹具仓库覆盖）+ 夹具仓库单测 18 项。

---

## 4. `export_resume.py`（P5）

**输入**：`05_改后简历.html`
**输出**：`05_改后简历.pdf`

**要点**
- 使用 Edge/Chrome 无头打印（已验证的可行参数）：
  ```
  msedge --headless=old --disable-gpu --no-sandbox --no-first-run \
         --no-pdf-header-footer --print-to-pdf-no-header \
         --user-data-dir=<临时目录> --print-to-pdf=<ascii输出路径> <file:// URL>
  ```
- **输出路径用 ASCII 临时路径再改名**：中文路径直接作为 `--print-to-pdf` 目标时曾失败。
- **等待落盘 10–12 秒**再检查文件（写入有延迟，过早检查会误判失败）。
- 导出前校验：改动说明已隐藏、无页眉页脚、分页点无孤立标题。
- **人工闸门**：必须先经用户确认 HTML，再导出 PDF。

---

## 5. 依赖与降级

| 能力 | 首选 | 降级 |
|---|---|---|
| PDF 文本 | `pdftotext`（如有） | LibreOffice → 内置解析 → 用户粘贴 |
| DOCX 文本 | `python-docx` | LibreOffice → `docx` 解包读 XML |
| HTML → PDF | Edge 无头打印 | Chrome 无头打印 → 用户手动打印 |

所有降级路径都必须打印**明确提示**，让用户知道当前走的是哪条路。
