# 表格图片转录系统（image-data-recognition）

> **English**: An AI skill + workspace that turns photos and screenshots of tables (printed or handwritten documents, receipts, report forms) into clean Excel files. The AI only looks at each image and submits a small per-image fragment; deterministic Python scripts do the numbering, conversion, validation, filing and archiving. A human only appears twice per batch: to approve one sample transcription, and to resolve the collected ambiguities at the end. Tool-agnostic — works with Codex, DSH, Cursor, Claude, OpenCode, and ZCode.

**把单据、报表、清单的照片丢进去，出来一份干净的 Excel。**

印刷的、手写的、手机拍歪的、扫描件都行。中途不需要你盯着——系统自己干完，只在两个地方找你：**开头的样板确认**和**结尾的问题复核**。

- 当前版本：**V2.0**（技能 V2.0）
- 适用平台：Windows（脚本与批处理为 Windows 编写）
- 所需依赖：Python 3.10+、openpyxl（**有离线安装包，全程不需要联网**）

---

## 它解决什么问题

录表格这件事的痛点从来不是"识别不准"，而是**流程混乱**：

- 传统做法：AI 每遇到一个看不清的字就来问你一次 → 你被迫守在现场 → 一天录不完几张
- 这套做法：AI 先做**一张**给你看 → 你花两分钟确认格式 → AI **闷头把剩下的一次性做完**，拿不准的只记账不打扰 → 最后你**集中看一次问题清单**

一句话：**把 N 次打扰压成 2 次确认。**

## 谁适合用

- 需要把纸质单据、报表、清单批量录成 Excel 的人（仓管、财务、行政、个体经营）
- 手上有一堆表格照片但不想一张张手敲的人
- 想把这套流程接进自己常用 AI 工具的人（不绑定某一家）

**不适合**：需要实时逐条录入、要求 AI 全自动且不许人工确认的场景。这套系统**故意保留了人工确认点**——因为表格识别没有 100% 准确，不确认就等于把错误数据放进你的账里。

## 工作流程

```
① 把照片丢进 inbox/
        ↓
② 对 AI 说「处理今天的图」
        ↓
③ 建任务（脚本）：建 runs/日期_编号/、复制原图、算指纹、写图片台账
        ↓
④ 【人】确认样板  ←── 只有遇到没录过的新表型才需要
        ↓
⑤ AI 逐张看图，每张只交一小段「识别片段」；脚本负责编号、换算、必填、落盘
        ↓
⑥ 【人】审一次 task_review.xlsx：填「员工决定」+「是否归档」两列
        ↓
⑦ 对 AI 说「审核完成」→ 脚本导入决定 → 产出 final.xlsx + facts.jsonl + 原图自动归档
```

**中途断电、下班走人都不丢进度**——每处理完一张立即落盘，回来说一句「继续处理」就只补没做完的部分，已填过的决定不会被覆盖。

## 它不会做的事（重要）

- ❌ 不会瞎猜数字——看不清就标出来问你，不编一个"看起来合理"的值
- ❌ 不会替你补一个"算出来"的数——合计栏对不上会报出来，不自动改
- ❌ 不会偷偷改你确认过的数据
- ❌ 不需要数据库、不需要账号、不需要联网、不需要 GitHub

## 核心设计

| 设计 | 为什么这么做 |
|---|---|
| **样板先行** | 直接问用户"这表有哪些列"太抽象；做一张出来给他看，他一眼就知道对不对 |
| **AI 只交识别片段** | AI 只管看图，交一小段"这张图上我看到了什么"；编号、换算、必填、落盘全由脚本做，AI 没有手写 `raw.json` 的机会 |
| **脚本层（tools/）** | 建任务、提交识别、注册表型、导入审核、导出长表、收尾报告都是确定性脚本：同样的输入给同样的输出，不靠 AI 记性 |
| **版式契约（schemas）** | 每种表型的结构记成一份 JSON 契约，同类表格以后自动校验，不用每次重新问 |
| **raw_value / value 双字段** | 原样记录 + 解读值分开存，从根上区分"图上写的"和"我理解的"，杜绝静默篡改 |
| **唯一裁判脚本** | `validate_export.py` 是唯一有权决定数据合格与否的组件，AI 不能绕过它自己宣称成功 |
| **问题账本** | 所有疑点集中记在一个文件里，最后一次性交给人工，而不是散落在对话记录里 |
| **review 用 Excel、导入用脚本** | 你在 Excel 里填，`导入审核.py` 直接读单元格生成权威的 `decisions.json`——中间不再有人工誊抄 |
| **标准答案库（golden）** | 你确认过的结果攒起来，成为后续判定的基准 |
| **工具无关** | 技能文件用通用 `SKILL.md` 格式，一份源文件装进 6 种 AI 工具 |

## 安装

### 方式一：一键装（推荐）

1. 把整个文件夹放到你想放的任意位置（**路径含中文没问题**）
2. 双击 `tools\一键安装（双击）.bat`
   - 检查 Python（缺了会中文提示怎么装）
   - 自动装好依赖（**优先用自带离线包，不联网**）
   - 把技能装进本机所有受支持的 AI 工具
3. 用任意一个 AI 工具打开这个文件夹，把照片丢进 `inbox`，说「处理今天的图」

### 方式二：让 AI 自己装

把这句话发给新电脑上的 AI：

> 打开这个文件夹，读根目录的 `AI自安装说明.md`，照着把环境、工作文件夹和技能都装好，装完告诉我结果。

那份文档是专门写给 AI 看的，包含自检命令、依赖清单和逐条验证步骤。

### 方式三：分步来

```bash
python tools/环境自检.py          # 先看缺什么（退出码 0 = 可以干活）
python tools/安装环境（双击）.bat   # 只装 Python 依赖
python tools/安装技能（双击）.bat   # 只装技能
```

## 技术栈

- **Python 3.10+** — 裁判脚本 `validate_export.py`（约 66 KB，含完整校验规则）、`tools/` 下的流程脚本与安装/打包/自检脚本
- **openpyxl** — 读写 `final.xlsx` / `task_review.xlsx` / `样板.xlsx`
- **Node.js（可选）** — 仅 `tools/DSH技能自检.mjs` 用，缺了不影响主流程
- **无数据库、无服务端、无外部 API**

## 目录结构

| 路径 | 是什么 |
|---|---|
| `inbox/` | 扔图的地方 |
| `runs/` | 每次任务的完整存档（图片台账、识别原文、问题账本、人工决定、长表、审核记录） |
| `archive/` | 归档好的原图，按日期分文件夹 |
| `golden/` | 你确认过的标准答案 |
| `schemas/` | 每种表型的版式契约（JSON） |
| `技能源文件/` | 技能的唯一真源，改了要重跑安装脚本 |
| `tools/` | 流程脚本 + 安装、打包、自检脚本 |
| `docs/` | 历史存档（旧方案、旧评审） |
| `validate_export.py` | 核心裁判脚本（**不要手工改**） |
| `AGENTS.md` | 行为手册 V2.0，给 AI 读的规则 |
| `使用说明.md` | 大白话版使用说明，给用户读 |
| `AI自安装说明.md` | 给 AI 读的部署说明 |

## 常用命令

```bash
python tools/建任务.py                              # 收图建任务（复制原图、算指纹、写台账）
python tools/提交识别.py runs/20260910_001 --next    # 看下一张待识别的图 + 片段格式
python tools/提交识别.py runs/20260910_001 --image IMG_001 --file 片段.json
python tools/生成样板.py runs/20260910_001            # 出样板.xlsx 给人确认
python tools/注册表型.py runs/20260910_001 --name 来料分析 --row-unit "一行 = ……"   # 落成契约
python validate_export.py runs/20260910_001 --init-issues   # 刷新问题账本
python validate_export.py runs/20260910_001                 # 生成审核表 + final.xlsx
python tools/导入审核.py runs/20260910_001            # 把用户填的 Excel 导成 decisions.json
python validate_export.py runs/20260910_001 --archive # 重跑 + 落账 + 归档原图
python tools/导出长表.py runs/20260910_001             # 机器可读长表 facts.jsonl
python tools/收尾报告.py runs/20260910_001 --one-line  # 一句话交付
```

每一步一条命令、照抄即可：完整清单见 `技能源文件/image-data-recognition/references/命令速查.md`。

## 文档

| 文档 | 给谁看 |
|---|---|
| [使用说明.md](使用说明.md) | **用户**（大白话，含常见问题） |
| [AI自安装说明.md](AI自安装说明.md) | **AI 助手**（部署与自检） |
| [AGENTS.md](AGENTS.md) | **AI 助手**（行为规则、红线、数据结构） |
| [runs/README.md](runs/README.md) | 想知道任务文件夹里每个文件是谁写的人 |
| [schemas/README.md](schemas/README.md) | 想给新表型加契约的人 |
| [技能源文件/image-data-recognition/references/架构说明.md](技能源文件/image-data-recognition/references/架构说明.md) | 想理解内部设计的人 |
| [技能源文件/image-data-recognition/references/安装部署说明.md](技能源文件/image-data-recognition/references/安装部署说明.md) | 想手工部署的人 |
| [docs/](docs/) | 历史方案与评审存档 |

## 开源方案调研与安全审计

本项目在选型和安全性上做过成文核查，不是拍脑袋决定的：

- [开源方案调研_20260914.md](开源方案调研_20260914.md) — 核查过有哪些现成方案可复用
- [技术选型审查_20260910.md](技术选型审查_20260910.md) — 依赖是否最新、有无过度设计
- [裁判脚本审计_20260910.md](裁判脚本审计_20260910.md) — 6 个高危 + 7 个中危问题，逐条修复并实测验证

## 测试

两条命令，各管一层：

```bash
python tools/回归测试.py       # 裁判脚本的断言（超百行表格、看不清的字段、必填缺失、重复提交）
python tools/新流程自测.py     # 脚本层的端到端自测（建任务 → 提交 → 拒绝 → 校验 → 长表 → 注册表型）
```

`新流程自测.py` 在系统临时目录里搭一个迷你工作区跑完整流程，**真实工作区只被读取、一个字节都不会改**；
两条命令都以退出码 0 = 全绿、1 = 有失败项收尾。

## 已知限制

- 仅 Windows（`.bat` 安装脚本；Python 部分本身跨平台）
- 识别效果取决于所用 AI 模型的视觉能力，本项目不包含 OCR 引擎
- 表格版式极度不规则的图片仍需人工介入
- 离线依赖包仅覆盖 Windows 的常见 Python 版本

## 参与贡献

欢迎提 Issue 报告问题或建议。提交代码前请先跑一遍 `tools/回归测试.py`，确保没破坏已有行为。

## 开源协议

[MIT License](LICENSE) — 随便用、随便改、商用也行，保留版权声明即可。
