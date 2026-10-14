# 安装与使用（首次使用者，3 步跑通）

## 0. 环境要求

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows / macOS / Linux | 已在 Windows 上实测 |
| Python | 3.9+ | **只用标准库**，不需要 `pip install` 任何东西 |
| git | 可选 | 只有 P4 验真层（核对提交数）需要 |
| 浏览器 | 可选 | 只有导出 PDF 需要（Edge / Chrome）；不导出就完全不需要 |

> 中文环境建议先设 `PYTHONUTF8=1`，否则 Windows 控制台可能乱码。

## 1. 拿到代码

```bash
git clone https://github.com/1576584678/mock-interview.git
cd mock-interview
```

## 2. 自检（确认环境可用）

```bash
python tools/selfcheck.py
```

它会检查 Python 版本、git / 浏览器是否可用，并跑一遍内置回归测试（144 项 + 3 份回归简历）。
全部通过说明环境没问题。

## 3. 跑一份真实简历

```bash
# ① 解析：简历 + JD → 结构化 JSON
python pipeline/parse_resume.py --in 简历.pdf --out out/resume.json --text-out out/resume.txt
python pipeline/parse_jd.py     --in jd.txt   --out out/jd.json

# ② 初始化并生成第 1 轮的面试官指令
python pipeline/orchestrate.py init    --resume out/resume.json --jd out/jd.json --root .
python pipeline/orchestrate.py prepare --round 1 --root .

# ③ 让 agent 读指令、写回答，然后逐轮推进（详见 SKILL.md 的「执行命令」）
```

> 完整链路（含双 agent 面试、评分、薪资、复盘、修复版简历、代码验真）见
> [`SKILL.md`](../SKILL.md) 的「执行命令」一节；每一步都是**独立的命令行脚本**，
> 中间产物都落在 `rounds/` `out/` 里，随时可以中断续跑。

## 想直接看结果？

仓库里有现成的完整示例（输入 + 五件套产物）：

```bash
ls examples/minimal              # 输入：示例简历.md / 示例JD.txt
ls examples/minimal/work          # 产物：01–05 五件套 + out/*.json
```

## 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| `parse_resume.py` 报"未找到 pdftotext / LibreOffice" | 正常，会自动降级到内置解析；解析结果建议对照原文核对（复杂排版可能丢结构） |
| `prepare` 报找不到 `prompts/interviewer.md` | 已修：提示词属于 skill 资产，会回退到 skill 目录下的 `prompts/` |
| `--pdf` 导出失败，报 `platform_channel.cc ... 拒绝访问` | 你在受限沙箱里运行。PDF 导出需要浏览器进程间通信（命名管道），请在正常桌面环境执行，或直接在浏览器里打印 `05_改后简历.html`（改动说明打印时自动隐藏） |
| 验真层报"未提供仓库" | 正常：没给 `--repo` 时所有声明都判"无法验证"，**不会**判编造 |
| 想换岗位 | 只需要换 `jd.txt`；维度、硬性要求、隐藏痛点都会重新抽取 |

## 把 skill 装进 agent 环境

`SKILL.md` 是本 skill 的入口（YAML frontmatter 里的 `name` + `description` 决定它何时被唤起）。
两种装法，任选其一。

### 方式 A：用 skills CLI 装（推荐，跨 agent 通用）

```bash
npx skills add 1576584678/mock-interview -g -a codex -y
```

- `-g` 装到用户级（所有项目可用）；省略则装进当前项目。
- `-a <agent>` 选择目标 agent。**选 `codex` / `cline` / `warp` / `zed` 这类，全局路径是
  `~/.agents/skills/`**，这正是 DSH 会扫描的目录；装给 Claude Code 就用 `-a claude-code`
  （落到 `~/.claude/skills/`）。
- 先看它发现了什么：`npx skills add 1576584678/mock-interview --list`
  （实测输出：`Found 1 skill` → `mock-interview`）。

### 方式 B：直接放进 DSH 的 skill 目录

```bash
git clone https://github.com/1576584678/mock-interview.git ~/.dsh/skills/mock-interview
```

DSH 从这几个根目录扫描（按优先级），目录里既可以是 `<名字>/SKILL.md`，也可以是单个 `<名字>.md`：

| 作用域 | 目录 |
|---|---|
| 项目级 | `<项目根>/.dsh/skills/`、`<项目根>/.agents/skills/` |
| 用户级 | `~/.dsh/skills/`（可用 `DSH_HOME` 覆盖）、`~/.agents/skills/`（可用 `DSH_AGENTS_HOME` 覆盖） |
| 自定义 | 配置项 `customSkillDirs` 里列出的目录 |
| 内置 | DSH 自带（打包在 `app.asar` 内，只读） |

> **项目根**怎么定的：从当前工作目录往上找到**第一个含 `.git` 的目录**；找不到就是当前目录本身。
> 所以项目级安装对"在哪个目录启动 agent"敏感，个人使用建议用用户级。

### 装完怎么确认

```bash
cd ~/.agents/skills/mock-interview   # 或你选择的安装目录
python tools/selfcheck.py            # 环境 + 163 项单测 + 34 项回归简历 + 17 项 SKILL.md 自检
```

实测：把仓库装到 `~/.agents/skills/mock-interview` 后，`python tools/selfcheck.py` 输出
**12 通过 / 0 警告 / 0 失败**，即整份 skill 自包含可运行。

**不需要重启**：DSH 对 skill 根目录挂了文件监视器，新装的 skill 会被自动发现——
实测在本会话安装后，技能目录当场就出现了 `mock-interview`。

### 换到别的 agent

同一份 skill 遵循通用的 Agent Skills 规范（`SKILL.md` + frontmatter）。只要目标 agent 支持该规范，
用 `npx skills add ... -a <agent>` 即可；也可以用 `npx skills use 1576584678/mock-interview@mock-interview`
**不安装、直接用一次**。唯一硬性要求是 frontmatter 里的 `name` 必须匹配
`^[a-z0-9]+(?:-[a-z0-9]+)*$`（`mock-interview` 符合），且 `description` 非空。

自检触发词格式：

```bash
python tools/check_skill.py
```
