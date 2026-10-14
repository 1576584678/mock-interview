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

# 6. P3 评估：评审 agent 把判分写进 judgments/round-N.json，脚本校验并聚合
python pipeline/score.py run --root . --judgments judgments --out out/scorecard.json
python pipeline/score.py compare --a out/scorecard.json --b out/scorecard-b.json  # 验收 A1
python pipeline/salary.py --root . --out out/salary.json
python pipeline/report.py --root . --only all --strict          # 02 / 03 / 04

# 7. P5 产出：补齐五件套 + 编号闭环校验
python pipeline/transcript.py --root . --resume-label <简历>     # 01 逐字稿
python pipeline/export_resume.py --root . --trim-long-items 900  # 05 HTML（改动说明打印隐藏）
python pipeline/export_resume.py --root . --pdf --confirm        # 05 PDF（人工闸门）
python pipeline/verify_links.py --root . --strict                # 验收 A5

# 8. P4 验真：只读交叉核对简历声明 × 代码/git（需用户提供仓库路径与 git 作者名）
python pipeline/verify_evidence.py --root . --resume out/real/resume.json \
      --repo <别名>=<仓库路径> --author <git 作者> --out out/evidence.json
python pipeline/report.py --root . --only 04 --evidence out/evidence.json  # 04 带代码验真列
```

> **PDF 导出必须在沙箱外运行**：Chromium 内核需要**命名管道**做进程间通信，
> 而 DSH 沙箱在受限模式下禁止创建命名管道（表现为
> `FATAL:mojo\platform_channel.cc: Check failed: 拒绝访问`）。
> 因此 `--pdf` 请在有完整权限的会话里执行；沙箱内会明确提示并降级为
> "用浏览器打开 `05_改后简历.html` → 打印 → 另存为 PDF"（打印时改动说明自动隐藏）。

要点：
- 编排脚本**不调用模型**——它只渲染指令、校验契约、落盘状态；模型调用由宿主 agent 完成。
- 中断续跑：状态在 `rounds/state.json`，重跑 `prepare` 即从最后一轮继续。
- 契约不满足（题量不足、漏题、引用了不存在的风险点编号）时脚本直接报错退出，**不会**生成半成品喂给下一个 agent。

## 输出契约

一次完整运行产出**五件套**（固定文件名，落在工作区根目录）：

| 文件 | 内容 | 核心要求 |
|---|---|---|
| `01_面试对话实录.md` | 双 agent 逐字稿：面试官提问 → 候选人回答 → 追问链（标注层次） | 字段直接取自 `rounds/round-N.json`，不做二次创作 |
| `02_复盘报告.md` | 分维度评分、雷区命中、改进清单、参考话术 | 全部由 rubric 驱动，无原话不扣分 |
| `03_薪资建议.md` | 期望薪资区间 + 推理链 + 依据声明 | 只给区间；禁止无出处的市场行情数据 |
| `04_简历风险点.md` | 风险点清单（R-xx 编号）+ 改写建议 + 代码验真 + 面试前必做 | 按**面试可见性**排序；不把"查不到"说成编造 |
| `05_改后简历.html` | 修复版简历（+ 可选 PDF） | 只改表述不新增事实；改动说明打印时自动隐藏 |

另有中间产物 `out/*.json`（结构化简历 / JD / 评分 / 薪资 / 验真）与 `rounds/`（每轮问答与状态）。
细节章节规范见 [`docs/OUTPUT_CONTRACT.md`](docs/OUTPUT_CONTRACT.md)；
**可直接查看的完整示例**见 [`examples/minimal/`](examples/minimal/)。

## 硬约束

- **不编造**：未在输入中出现的事实不得写入任何产物。
- **验证优先于判断**：能用 git / 代码核对的，先核对再下结论；核对结果只读，不改动用户仓库。
- **严重度看面试可见性，不看能不能查到**：代码里查不到（提交数、测试数、某个机制名存在与否）
  属于"需材料才可判"，只能建议改成真实数字，**不得称为造假**；只有面试官当场就能发现的
  领域常识错误（如"Oracle 侧用 binlog"）才给最高严重度。**判编造的唯一入口是面试实录**。
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
| `prepare` 报找不到 `prompts/*.md` | 提示词属于 skill 自带资产：脚本会自动回退到 skill 目录下的 `prompts/`；若仍失败，说明 skill 目录不完整 |
| PDF 导出报 `platform_channel.cc ... 拒绝访问` | 当前会话在受限沙箱里（浏览器进程间通信需要命名管道）：改为用浏览器打开 `05_改后简历.html` 打印 |

## 安装与自检

首次使用（含环境检查与一键回归，**不调用模型**）：

```bash
python tools/selfcheck.py      # 环境 + 163 项回归 + 3 份回归简历 + SKILL.md 触发词
python tools/check_skill.py    # 只查本文件的 frontmatter/章节/脚本引用/产出名
```

完整示例（输入 + 五件套产物 + 复现命令）见 [`examples/minimal/`](examples/minimal/)；
安装说明与常见问题见 [`INSTALL.md`](INSTALL.md)。
