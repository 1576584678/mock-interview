# pipeline · 脚本接口约定

> 本目录的实现属于 **P1 / P4 / P5**，当前仅有接口约定，代码待开发。
> 所有脚本遵循同一约定：**读 JSON / 写 JSON，参数走命令行，不依赖第三方库（优先）。**

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

## 3. `verify_evidence.py`（P4 · 护城河）

**输入**：`resume.json`（用其 `claims`）、`--repo <代码仓库路径>`、可选 `--author` / `--since`
**输出**：`evidence.json`

```json
{
  "repo": { "path": "", "branch": "", "head": "", "collected_at": "" },
  "claims": [
    {
      "id": "C-01", "type": "quantitative",
      "text": "累计输出 150 次提交",
      "method": "git log --author=<me> --oneline | wc -l",
      "observed": "148",
      "verdict": "与事实不符",
      "confidence": 0.95
    }
  ]
}
```

**判决四档**：`可信` / `存疑` / `与事实不符` / `无法验证`

**硬约束**
- **只读**：绝不执行任何写操作（`git` 只用 `log/show/lrev-list/status` 等只读命令）。
- 每条结论必须带 `method`（可复现命令），用户能自己验证。
- 记录取证时点、分支、HEAD commit（仓库状态会变）。
- **`无法验证` 不得被判为编造。**
- 作者名需向用户确认（同一人多邮箱会漏统计）。

**回归基线**：见 `docs/EVIDENCE.md` 第 5 节的 7 条实证案例，脚本至少要能复现前 6 条。

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
