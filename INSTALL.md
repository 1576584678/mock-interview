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

`SKILL.md` 是本 skill 的入口（含 YAML frontmatter 的 `name` / `description` 触发词）。
把它所在目录放进你的 agent 环境的 skills 目录，新开会话即可通过
「模拟面试 / 简历体检 / 这份 JD 该要多少薪资 / 核对简历是否属实」等说法被唤起。

自检触发词格式：

```bash
python tools/check_skill.py
```
