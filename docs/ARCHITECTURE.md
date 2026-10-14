# 架构设计

## 1. 五层结构

| 层 | 职责 | 输入 | 输出 | 可单独重跑 |
|---|---|---|---|---|
| L0 输入层 | 把异构输入变成稳定 JSON | 简历文件 / JD 文本 / 代码路径 | `resume.json` `jd.json` `evidence.json` | ✅ |
| L1 分析层 | 匹配分析与风险识别 | L0 产物 | `match.json`（待 P3）`out/risks.json`（已实现：`pipeline/risks.py`） | ✅ |
| L2 演练层 | 双 agent 多轮面试 | `jd.json` 维度 + `resume.json` + `out/risks.json` | `rounds/*.json` + `01_面试对话实录.md` | ✅（按轮） |
| L3 评估层 | 评分、薪资、改写 | L1 + L2 产物 + rubric | `scorecard.csv` `02/03/04*.md` | ✅ |
| L4 产出层 | 渲染与导出 | L3 产物 + templates | 五件套最终文件 | ✅ |

**设计原则**：每层落盘，任一层可重跑。这样改一个 prompt 不需要重跑整条链，也支持断点续跑。

## 2. 数据流

```
简历文件 ──parse_resume──┐
JD 文本  ──parse_jd──────┤
代码路径 ──verify_evidence┤
                          ▼
                   resume.json / jd.json / evidence.json
                          │
            ┌─────────────┼──────────────┐
            ▼             ▼              ▼
      match.json      risks.json    interview_dims.json
                          │              │
                          └──────┬───────┘
                                 ▼
                    ┌─── 轮次循环（round 1..N）───┐
                    │  interviewer → 题目          │
                    │  candidate   → 回答          │
                    │  interviewer → 追问 ×1~3     │
                    └──────────────┬──────────────┘
                                   ▼
                             rounds/*.json
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
              scorecard.csv   薪资建议.md    风险点.md
                    └──────────────┼──────────────┘
                                   ▼
                          复盘报告.md + 修复版简历
```

## 3. 模型调用预算

| 环节 | 次数 | 说明 |
|---|---|---|
| 简历解析（LLM 辅助结构化） | 1 | 正则优先，LLM 兜底 |
| JD 维度抽取 | 1 | |
| 匹配分析 | 1 | |
| 风险点识别 | 1–2 | 有证据时 +1（交叉核对） |
| 演练（3 轮 × (出题+追问+作答)） | 9–18 | **最大头** |
| 评分 | 3 | 按轮评分，避免单次上下文过长 |
| 薪资 / 改写 | 2 | |
| **合计** | **18–30** | 上限 60，超限降轮次 |

**成本控制手段**：轮次可配（默认 3）；追问上限 3 层；L0/L1 结果缓存，重跑 L3 不重复调用。

## 4. 状态与断点续跑

```
<工作区>/.mock-interview/
├── state.json          # 当前阶段、已完成轮次、时间戳
├── resume.json
├── jd.json
├── evidence.json
├── rounds/round-1.json
└── out/                # 五件套最终产物
```

`state.json` 记录阶段间游标；中断后可从最后一次成功落盘的阶段继续。

## 5. 技术选型

| 项 | 选择 | 理由 |
|---|---|---|
| 运行环境 | Python 3（DSH 内置 runtime） | 无额外安装 |
| 第三方依赖 | **尽量为零** | 少一个装不上的包就多一次失败 |
| PDF 解析 | 多级降级：`pdftotext` → LibreOffice 转 txt → 内置轻量解析 → 用户粘贴 | 本轮已实测：无库时手写解析可行但脆弱 |
| DOCX 解析 | python-docx（内置 runtime 已有） | — |
| 模板渲染 | 纯字符串替换 + Markdown | 不引入模板引擎 |
| 报告格式 | Markdown；简历额外支持 HTML → PDF（Edge 无头打印） | 打印 CSS 已验证 |

## 6. 目录职责

| 目录 | 职责 | 是否含逻辑 |
|---|---|---|
| `prompts/` | 所有 LLM 角色定义与输出格式约束 | 否（纯文本） |
| `rubric/` | 评分维度、锚点、雷区规则 | 否（被 prompt 引用） |
| `pipeline/` | 解析、验真、导出脚本 | 是 |
| `templates/` | 输出骨架 | 否 |
| `docs/` | 规划与契约 | 否 |

**约定**：prompt 不写业务逻辑，脚本不写评分标准；两者通过 rubric 文件解耦，改评分标准不需要改代码。
