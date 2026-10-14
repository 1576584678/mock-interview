# 完整示例（合成数据，可复现）

这是一份**真实跑通**的完整示例：输入是一份合成的简历 + 一份合成 JD，
输出是标准五件套与本 skill 的全部中间产物。

> 为什么用合成数据：仓库是公开的，真实简历含手机号、邮箱、公司名等个人信息。
> 示例简历 `A_规范完整.md` 同时是回归测试集里的一份（`tests/resumes/`），
> 所以示例里出现的问题都是"可回归"的。

## 输入

| 文件 | 说明 |
|---|---|
| [`input/示例简历.md`](input/示例简历.md) | 6 年 Java 后端，仓储履约方向；数字带口径、主动标注能力边界 |
| [`input/示例JD.txt`](input/示例JD.txt) | Java 开发工程师 · 供应链履约（仓储物流），20K–35K，3–8 年 |

## 产物

| 文件 | 内容 |
|---|---|
| [`work/01_面试对话实录.md`](work/01_面试对话实录.md) | 3 轮逐字稿：题目、追问链层次、候选人标记、反问 |
| [`work/02_复盘报告.md`](work/02_复盘报告.md) | 分维度评分、雷区命中、改进清单 |
| [`work/03_薪资建议.md`](work/03_薪资建议.md) | 期望薪资区间 + 推理链 + 依据声明 |
| [`work/04_简历风险点.md`](work/04_简历风险点.md) | 风险点清单 + 改写建议 + 代码验真列 + 面试前必做 |
| [`work/05_改后简历.html`](work/05_改后简历.html) | 修复版简历（改动说明打印时自动隐藏） |
| `work/out/*.json` | 结构化简历 / JD / 风险点 / 评分 / 薪资 / 验真清单 |
| `work/rounds/*` | 每轮的出题指令、回答、合并后的 `round-N.json` 与 `state.json` |

## 复现命令

> **路径约定**：`--root` 指定工作区后，其余路径**相对工作区**（也可以写绝对路径）。
> 例如 `--root examples/minimal/work --out out/scorecard.json` 指的就是
> `examples/minimal/work/out/scorecard.json`。

```bash
# ① 解析（不调用模型）
python pipeline/parse_resume.py --in examples/minimal/input/示例简历.md \
       --out examples/minimal/work/out/resume.json --text-out examples/minimal/work/out/resume.txt
python pipeline/parse_jd.py --in examples/minimal/input/示例JD.txt \
       --out examples/minimal/work/out/jd.json

# ② 编排（不调用模型）：初始化 + 生成第 N 轮指令
python pipeline/orchestrate.py init --resume examples/minimal/work/out/resume.json \
       --jd examples/minimal/work/out/jd.json --root examples/minimal/work --max-rounds 3
python pipeline/orchestrate.py prepare --round 1 --root examples/minimal/work
#   → 让「面试官 agent」读 rounds/round-1.interviewer.md，写 rounds/round-1.interviewer.json
python pipeline/orchestrate.py prepare-candidate --round 1 --root examples/minimal/work
#   → 让「求职者 agent」读 rounds/round-1.candidate.md，写 rounds/round-1.answers.json
python pipeline/orchestrate.py commit --round 1 --root examples/minimal/work \
       --questions examples/minimal/work/rounds/round-1.interviewer.json \
       --answers   examples/minimal/work/rounds/round-1.answers.json
#   重复 ② 的 prepare/prepare-candidate/commit 到第 3 轮
python pipeline/orchestrate.py check --root examples/minimal/work   # P2 DoD：3 轮 / 维度全覆盖 / 风险点全覆盖

# ③ 评估（评审 agent 写 judgments/round-N.json，其余由脚本确定性完成）
W=examples/minimal/work
python pipeline/score.py    run --root $W --judgments judgments --out out/scorecard.json
python pipeline/salary.py        --root $W --out out/salary.json
python pipeline/verify_evidence.py --root $W --resume out/resume.json --text out/resume.txt \
       --risks out/risks.json --out out/evidence.json --md-out out/evidence.md
python pipeline/report.py        --root $W --only all --strict \
       --resume-label 示例简历.md --evidence out/evidence.json

# ④ 产出
python pipeline/transcript.py    --root $W --resume-label 示例简历.md
python pipeline/export_resume.py --root $W
python pipeline/verify_links.py  --root $W --strict   # 五件套编号闭环（A5）
```

> 本示例**没有提供代码仓库**，所以验真层走的是"未验真"路径（全部声明判"无法验证"、
> 不判编造）。要看验真层的完整能力，见 [`docs/EVIDENCE.md`](../docs/EVIDENCE.md) 的真实仓库案例。

## 实测数据（本示例真实产出）

| 指标 | 数值 |
|---|---|
| 轮次 / 题目 / 追问层 | 3 轮 / **16 题**（6+5+5）/ **46 层**追问 |
| P2 DoD | 3/3 轮、**7/7 维度**、**7/7 风险点**、模型调用 6/60、墙钟 3.2 分钟 |
| 评分 | 加权 **4.1/5**（分轮 4.25 / 4.4 / 4.6，"大概率通过"） |
| 薪资建议 | **25.5K – 30.0K**（中位 27.5K，JD 区间 20K–35K，定位分位 0.515） |
| 风险点 | **7 条**（口径缺失 4、能力缺口 3） |
| 简历改动 | 应用 **3** 条成对替换、**0** 条跳过、**4** 条留给人工（不编数字） |
| 验真层 | 无仓库 → 全部"无法验证/不可判"，**编造判定 0**（刻意不把"查不到"说成造假） |
| A5 闭环 | **通过**：悬空编号 0、孤岛 0、未进 04 为 0 |
| 模型调用合计 | **8 次**（面试官 3 + 求职者 3 + 评审 1 + 改写 1） |

## 这个示例展示了什么

1. **求职者 agent 当场纠正了面试官的题干错误**（"3 个 Topic"在简历里挂在 Kafka，不是 RocketMQ）——
   说明双 agent 不是"面试官随便问、求职者顺着答"。
2. **候选人主动示弱**：多题标注"不确定（已示弱）"并写清盲区（限流/熔断无落地、TiDB 只了解、
   分库分表只在测试环境验证过），没有编造——这是评分规则里"诚实 + 推理路径 = +0.5"的来源。
3. **P2 的 DoD 门禁真的拦得住**：第一轮跑完 `check` 报「分布式数据库维度 0 题」，
   于是第 2 轮按缺口出题，最终 7/7 维度覆盖。
