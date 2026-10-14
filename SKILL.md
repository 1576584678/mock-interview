---
name: mock-interview
description: 模拟面试与简历体检。输入简历 + JD（可选代码仓库路径），输出双 agent 面试对话实录、复盘评分报告、期望薪资区间建议、简历风险点清单与修复版简历。适用场景：准备面试、评估简历与岗位匹配度、找出会被面试官当场戳穿的表述、判断期望薪资是否合理。
---

# 模拟面试官

## 何时使用

- 用户提供简历 + JD，要求"模拟面试""面试会问什么"
- 用户要求"简历体检""找简历里的风险点""简历会不会被问倒"
- 用户询问"这份 JD 我该要多少薪资"
- 用户提供简历 + 代码仓库，要求核对简历表述是否属实

## 输入契约

| 输入 | 必需 | 形式 | 说明 |
|---|---|---|---|
| 简历 | ✅ | pdf / docx / md / txt 路径 | 解析失败时降级：先转文本，再请用户粘贴 |
| JD | ✅ | 文本或文件 | 缺失时先反问，不要猜岗位 |
| 代码仓库路径 | ⬜ | 目录路径 | **有则启用验真层，价值最高**；没有则跳过并标注"未验真" |
| 城市 / 目标区间 | ⬜ | 文本 | 用于薪资建议；缺失时按 JD 所在城市推断并声明 |
| 轮次数 | ⬜ | 整数，默认 3 | 3 轮 ≈ 12–18 题；轮次越多成本越高 |

## 执行流程

1. **归一化输入**：解析简历与 JD 为结构化文本；确认缺失项（缺 JD 必须反问）。
2. **JD 维度抽取**：提取硬性要求、加分项、能力维度、岗位真实痛点，输出维度表。
3. **证据验真（可选但优先）**：若拿到代码路径，对简历里的每个量化声明与机制描述做交叉核对，产出 `可信 / 存疑 / 与代码不符 / 无法验证` 四档结论。详见 `docs/EVIDENCE.md`。
4. **面试演练**：面试官 agent 与求职者 agent 按轮次对打；面试官按 JD 维度覆盖设计题目，并对**每一个可疑表述追追问**；求职者只依据简历事实作答。
5. **复盘评估**：按 `rubric/scoring.md` 逐题评分，标记雷区与编造，给出总分与改进项。
6. **产出五件套**：对话实录、复盘报告、薪资建议、风险点清单、修复版简历。
7. **简历修复**（用户确认后）：按风险点清单输出修复版 HTML，经用户检查后再导出 PDF。

### 执行命令（宿主 agent 照此执行）

```bash
# 1-2. 归一化输入 + JD 维度抽取
python pipeline/parse_resume.py --in <简历> --out out/resume.json --text-out out/resume.txt
python pipeline/parse_jd.py     --in <JD>  --out out/jd.json

# 3. 风险点播种（R-01…，P2 追问目标与 P3 风险报告共用同一批编号）
python pipeline/orchestrate.py init --resume out/resume.json --jd out/jd.json --root .

# 4. 每轮循环：渲染出题指令 → 面试官 agent → 渲染答题指令 → 求职者 agent → 契约校验并落盘
python pipeline/orchestrate.py prepare           --round N --root .
#    面试官 agent 读 rounds/round-N.interviewer.md，写 rounds/round-N.interviewer.json
python pipeline/orchestrate.py prepare-candidate --round N --root .
#    求职者 agent 读 rounds/round-N.candidate.md，写 rounds/round-N.answers.json
python pipeline/orchestrate.py commit --round N --root . \
       --questions rounds/round-N.interviewer.json --answers rounds/round-N.answers.json

# 5. DoD 校验：JD 硬性维度全覆盖 + 每条风险点至少被追问 1 次，未达标返回 1
python pipeline/orchestrate.py check --root .
```

要点：
- 编排脚本**不调用模型**——它只渲染指令、校验契约、落盘状态；模型调用由宿主 agent 完成。
- 中断续跑：状态在 `rounds/state.json`，重跑 `prepare` 即从最后一轮继续。
- 契约不满足（题量不足、漏题、引用了不存在的风险点编号）时脚本直接报错退出，**不会**生成半成品喂给下一个 agent。

## 输出契约

见 `docs/OUTPUT_CONTRACT.md`。五件套固定命名、固定章节；风险点带编号并与对话、评分、简历改动互相引用。

## 硬约束

- **不编造**：未在输入中出现的事实不得写入任何产物。
- **薪资只给区间** + 推理链 + 依据声明，禁止输出无出处的市场数据。
- **评分可复现**：同输入两次运行加权总分偏差 ≤ 0.3。
- **合规**：代码与业务数据不外传，产物默认落本地工作区。
- **证据不足时如实标注**，绝不用"大概""通常"填补空白。
- **不美化经历**：目标是表达准确，不是看起来更强。

## 失败与降级

| 情况 | 降级策略 |
|---|---|
| 简历 PDF 解析失败 | 依次尝试 docx → md/txt 转换 → 请用户粘贴文本 |
| 无代码仓库 | 跳过验真层，所有风险点标注"仅基于文本判断" |
| 模型调用超预算 | 降至 1 轮（4–6 题），优先保证复盘与风险点完整 |
| 薪资数据不足 | 只给 JD 区间定位 + 相对位置（中位/偏上/偏下），不编造绝对数字 |
