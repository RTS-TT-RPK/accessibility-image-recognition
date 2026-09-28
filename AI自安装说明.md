# AI 自安装说明 —— 换电脑后先读这一份

> **这份文档是写给 AI 助手看的**：你（AI）照第 1～6 节做，就能把整套系统在这台新电脑上装好并验证通过，
> 不需要用户懂技术。**人类用户**可以直接跳到第 7 节（手动步骤）或第 8 节（常见问题）。

---

## 0. 装好之后要达到什么效果

用户能做这件事：把单据照片丢进 `inbox` → 对你说"处理今天的图" → 你**逐张看图、每张交一小段识别片段**
（编号、换算、必填、落盘由 `tools/提交识别.py` 完成）→ 把 `task_review.xlsx` 交给用户集中审核一次
（他填「问题清单」的员工决定 + 「文件归档清单」的是否归档）→ 用户回复"审核完成" →
你用 `tools/导入审核.py` 把决定导入，重跑后产出 `final.xlsx` / `facts.jsonl` 并归档原图。

为此需要**三样东西就位**：

| # | 要就位的东西 | 谁负责 |
|---|--------------|--------|
| 1 | Python 运行库 + openpyxl | 你（AI）负责检查和安装 |
| 2 | 工作区文件夹（inbox / runs / archive …） | 你（AI）负责补齐 |
| 3 | 技能装进你自己的技能目录 | 你（AI）负责安装 |

> 流程是**命令驱动**的，每一步照抄 `技能源文件/image-data-recognition/references/命令速查.md` 即可。
> **不要自己拼 `raw.json`**——它只由 `tools/提交识别.py` 写（见第 6 节的验证）。

---

## 1. 先确认工作区在哪

本工作区就是你看到的这个文件夹（里面应该有 `validate_export.py` 和 `AGENTS.md`）。

- **不要新建一个空工作区**，就用现在这个文件夹；
- 记下它的**绝对路径**，后面写 `workspace.txt` 要用；
- 路径里**可能含中文**（例如 `...\图像识别长期工程`），这很正常，不要因此改名或换目录。

---

## 2. 跑一次环境自检（一条命令，先看再动）

在**工作区根目录**执行：

```bash
python tools/环境自检.py            # 给人看的报告
python tools/环境自检.py --json     # 给 AI 读的 JSON（推荐你用这个）
```

报告分四块：必需项（Python / openpyxl）、可选项（Node.js）、工作区文件夹、技能安装情况。
**退出码 0 = 可以干活；1 = 还缺必需项。**

> 如果连 `python` 命令都找不到，说明第 3.2 节的情况，先跳到那里。

---

## 3. 依赖说明：到底需要什么，不需要什么

### 3.1 必需

| 依赖 | 为什么需要 | 最低版本 |
|------|------------|----------|
| **Python** | `validate_export.py`（唯一裁判脚本）和 `tools\` 下的脚本都是 Python | 3.10 |
| **openpyxl** | 读写 `final.xlsx` / `task_review.xlsx` / `样板.xlsx`；`tools\导入审核.py` 也靠它读用户填的审核表 | 3.1 左右即可 |

### 3.2 可选

| 依赖 | 用途 | 缺了会怎样 |
|------|------|------------|
| Node.js | 只有 `tools\DSH技能自检.mjs` 用（检查 DSH 能否发现技能） | 不影响识别、校验、归档，跳过即可 |

### 3.3 明确不需要

不需要数据库、不需要联网（库有离线包）、不需要任何账号、不需要 GitHub。
**只有**在离线包缺失或损坏时，才需要联网兜底。

### 3.4 `tools\` 里都有什么（新版流程的主角）

| 脚本 | 干什么 | 谁调用 |
|------|--------|--------|
| `tools\建任务.py` | 建 `runs/日期_编号/`、复制原图进 `source/`、算真实 sha256、写 `source/names.json` 台账和**空的** `raw.json` 骨架 | AI |
| `tools\提交识别.py` | **`raw.json` 的唯一写手**：收 AI 的识别片段，校验（不合格就退回收场，退出码 2，不落盘）、编号、`raw_value`→`value` 换算、补必填与单位、原子落盘 | AI |
| `tools\生成样板.py` | 出 `样板.xlsx`（给人确认）和 `样板_契约草案.json` | AI |
| `tools\注册表型.py` | 把草案落成 `schemas/<表型名>_vN.json` + `sample.json`，并自动跑回归测试；**`row_unit` 没被人工确认就拒绝写** | AI |
| `tools\导入审核.py` | 读用户填好的 `task_review.xlsx`，写成权威的 `decisions.json`（合并、绝不覆盖） | AI |
| `tools\导出长表.py` | 出 `runs/<任务>/facts.jsonl`（一行一条事实，给机器读）；被挡住的行不进 | AI |
| `tools\收尾报告.py` | 从磁盘上的账本读数字，出一句话交付摘要；`--one-line` 只打一行 | AI |
| `tools\收录.py` | 用户拍板后，往 `aliases.json` / `golden/` / `FEEDBACK_LOG.md` 追加（只追加、先备份） | AI |
| `tools\回归测试.py` | 自检：裁判脚本的行为没被改坏 | AI |
| `tools\新流程自测.py` | 自检：脚本层端到端没被改坏（临时工作区里跑，真实数据只读） | AI |
| `tools\环境自检.py` | 自检：Python / 依赖 / 技能安装 / `workspace.txt` 编码 | AI / 用户 |
| `tools\初始化工作区.py` | 补齐 `inbox`/`runs`/`archive`/`golden` 等骨架目录（不删不覆盖） | AI / 用户 |
| `tools\安装技能.py` | 把技能装进本机各 AI 工具的技能目录 | AI / 用户 |
| `tools\导出技能包.py` | 打包成压缩包（`--with-golden` 连标准答案库一起带） | 用户 |
| `tools\dsh_plugin_setup.py` | DSH 专用插件安装器（装 / 查 / 卸 `dsh-table-image`）。日常双击 `tools\安装DSH插件（双击）.bat` 即可 | DSH 用户 |

> 退出码约定：**0 = 跑完了**（不代表没有问题，问题在 `issues.json` 里）；**1 = 断言失败或未预料的异常**；
> **2 = 被拒绝 / 参数错误**（这次什么都没写）。

---

## 4. 缺什么补什么

### 4.1 缺 openpyxl（最常见，也最好解决）

**优先离线安装**（不联网、最快、最稳）：

```bash
python -m pip install --no-index --find-links "vendor/wheels" openpyxl
```

`vendor/wheels/` 里已经放了 `openpyxl` 和它依赖的 `et_xmlfile`。

**离线装失败**（包损坏等）再联网兜底：

```bash
python -m pip install openpyxl
```

装完**必须重跑一次** `python tools/环境自检.py` 确认变成 `[OK]`。

### 4.2 缺 Python（这种情况下必须先问用户）

装 Python 是**动这台电脑的系统级操作**，你不能悄悄装。请这样处理：

1. **先问用户**："这台电脑没装 Python，这套系统需要它才能跑。我可以帮你自动装，也可以给你下载链接自己装，你选哪个？"
2. 用户同意自动装 → 按顺序尝试：
   ```bash
   winget install --id Python.Python.3.12 -e --source winget
   ```
   如果 `winget` 不存在或要管理员权限而失败，**不要硬试**，转第 3 步。
3. 转手动：把下面这段原话给用户（照抄即可）：
   > 去 https://www.python.org/downloads/windows/ 下载 Python 3.12，
   > 安装时**务必勾选** "Add python.exe to PATH"，装完**关掉再重新打开** AI 工具，然后告诉我"装好了"。
4. 用户说装好后，**重开终端**再跑 `python tools/环境自检.py` 验证。

> 注意：装完 Python 后当前终端可能读不到新 PATH，**必须重新开一个终端会话**再检测。

### 4.3 缺 Node.js

**直接跳过**，不要为它打扰用户。只在用户明确要求做 DSH 技能自检时，才提一句"要装 Node.js 才能自检"。

---

## 5. 把技能装进你自己的技能目录

### 5.1 一条命令搞定（推荐）

```bash
python tools/安装技能.py            # 装进本机检测到的所有 AI 工具
python tools/安装技能.py --list     # 只看检测到哪些工具，不安装
python tools/安装技能.py --force    # 连没检测到的工具目录也建（以后装了工具就能用）
```

它会：把 `技能源文件/image-data-recognition/` 复制到每个工具的技能目录，并写好 `workspace.txt`。

### 5.2 各工具的技能目录（想手动放时对照）

| 工具 | 技能目录 |
|------|----------|
| Codex | `%USERPROFILE%\.codex\skills\` |
| DSH | `%USERPROFILE%\.dsh\skills\` |
| Cursor | `%USERPROFILE%\.cursor\skills\` |
| Claude | `%USERPROFILE%\.claude\skills\` |
| OpenCode | `%USERPROFILE%\.opencode\skills\` |
| ZCode | `%USERPROFILE%\.zcode\skills\` |
| 通用 agents | `%USERPROFILE%\.agents\skills\` |

要复制的是整个 **`技能源文件\image-data-recognition\`** 文件夹（放进去后路径形如
`...\skills\image-data-recognition\SKILL.md`）。

### 5.3 ⚠️ `workspace.txt` 的编码（换电脑最容易踩的坑，见第 9 节）

技能副本里的 `workspace.txt` 只有一行：工作区的绝对路径。

- **必须存成 UTF-8 带 BOM**（即 `utf-8-sig`）；
- 有 BOM，Windows PowerShell 5.1 默认 `Get-Content` 才读得对；
- 没有 BOM，中文路径会被按 GBK 读成乱码（`图像识别长期工程` → `鍥惧儚璇嗗埆闀挎湡宸ョ▼`），
  技能就找不到工作区了。
- 反过来，**Python 读它要用 `encoding="utf-8-sig"`**，用 `"utf-8"` 会多出一个 `\ufeff` 前缀。

用脚本（第 5.1 节）安装会自动写成正确编码；**手动放的话请用记事本另存为"UTF-8 带 BOM"**。

---

## 6. 验证（必须做，不许跳过）

按顺序确认这五条都过：

| # | 验证 | 命令 / 做法 | 通过标准 |
|---|------|-------------|----------|
| 1 | 环境齐全 | `python tools/环境自检.py` | 退出码 0；必需项都是 `[OK]` |
| 2 | 技能可被发现 | 看自检报告"技能安装情况" | 你现在用的工具显示 `[已装]` |
| 3 | 裁判脚本能跑 | `python validate_export.py runs/20260910_002 --status` | 正常打印任务进度，不报错 |
| 4 | 工作区完整 | 看自检报告"工作区文件夹" | `inbox`/`runs`/`archive`/`golden`/`pending`/`schemas` 齐全 |
| 5 | **脚本层能用** | `python tools/新流程自测.py` | 退出码 **0**，结尾是 `结果：通过 N 项，失败 0 项` |

第 1 条若不通过 → 回第 4 节补依赖；第 3 条若报"缺少 openpyxl" → 回第 4.1 节。

> **第 5 条是这一版新增的**，不要跳过：它会在系统临时目录里搭一个迷你工作区，
> 用假图片把整条流程跑一遍——建任务 → `--next` → 提交识别 → **三种必须拒绝的坏片段**
> → 重复提交与行号稳定 → 校验落账 → 导出长表 → 收尾报告 → 注册表型。
> **真实工作区只被读取，一个字节都不会改**（跑完临时目录自动清理）。
> 它报的是脚本层的问题：如果 `tools/` 下的脚本在这台电脑上跑不通（路径、编码、权限），
> 你在这里就会看到，而不是等用户的第一批照片出了问题才发现。
> 断言条数会随版本增加，所以**认"失败 0 项 / 退出码 0"，不要死记条数**。

> `runs/20260910_001`、`20260910_002` 是**回归测试样本**（假数据），专门用来验证环境，可以放心跑它们。

---

## 7. 手动安装步骤（人类用户看这里）

不想让 AI 动手，就自己点三下：

1. **解压**压缩包，得到 `表格识别系统` 文件夹。
   —— 里面**已经带齐** `inbox`、`runs`、`archive`、`golden`、`pending`、`schemas`，不用自己建。
2. 双击 **`tools\一键安装（双击）.bat`**。它会一口气做完：
   检查 Python → 装 openpyxl（离线）→ 补齐工作文件夹 → 把技能装进本机所有 AI 工具。
   - 提示没装 Python？照屏幕上的中文提示装（务必勾选 "Add python.exe to PATH"），装完再双击一次。
3. 用任意 AI 工具打开 `表格识别系统` 文件夹，把照片丢进 `inbox`，对它说 **"处理今天的图"**。

> 想分步来：`tools\安装环境（双击）.bat`（只装依赖）、`tools\安装技能（双击）.bat`（只装技能）。
> 文件夹万一没补上：双击 `tools\初始化工作区.py`。
> 只想看状态不想装：`python tools\环境自检.py`；想确认脚本层没坏：`python tools\新流程自测.py`。

---

## 8. 换电脑常见问题

| 现象 | 原因 | 怎么办 |
|------|------|--------|
| 双击 bat 一闪而过 | 报错太快没看清 | 右键"以管理员身份运行"，或先开"命令提示符"再运行 |
| 提示没有 Python | 新电脑没装 | 见第 4.2 节 |
| `pip install` 报网络错 | 没网/被墙 | 改用第 4.1 节的**离线**命令（`vendor\wheels`） |
| 装完 Python 还是说找不到 | 当前终端没刷新 PATH | **关掉终端和 AI 工具，重新打开**再试 |
| AI 说找不到工作区 | `workspace.txt` 缺失或乱码 | 见第 5.3 节；或让 AI 重跑 `tools\安装技能.py` |
| 某个工具里看不到技能 | 没装到那个工具 | 重跑 `tools\安装技能.py`，然后**重启那个工具** |
| Excel 打不开生成的表 | 文件被占用 | 先关掉已打开的 Excel，再重跑命令 |
| `新流程自测.py` 报失败项 | `tools/` 下的脚本装坏了/版本不齐 | 把失败项原文发给开发者；**不要**拿真实照片去试 |

---

## 9. 编码坑（换台电脑必踩，务必理解）

**现象**：在 Windows PowerShell 5.1 里直接 `Get-Content workspace.txt`，中文路径变成
`鍥惧儚璇嗗埆闀挎湡宸ョ▼` 这种乱码。

**根因**：Windows PowerShell 5.1（Desktop 版）的 `Get-Content` 不带 `-Encoding` 时，
按**系统 ANSI 代码页**解码；中文 Windows 上就是 GBK（代码页 936）。
而本系统所有文本文件都是 **UTF-8**，没有 BOM 时就被按 GBK 解，中文必然乱。

**本系统的处理方式**（写入端 + 读取端一起改，缺一不可）：

| 端 | 规则 |
|----|------|
| 写入 `workspace.txt` | 用 **UTF-8 带 BOM**（Python：`encoding="utf-8-sig"`） |
| 用 PowerShell 5.1 读 | 加 `-Encoding UTF8`，或改用 AI 的 read 工具 |
| 用 Python 读 | 用 `encoding="utf-8-sig"`（能同时兼容有/无 BOM） |
| `SKILL.md` | **绝不能加 BOM**（YAML frontmatter 必须以 `---` 开头，加了 BOM 技能就解析不了） |
| 脚本写的 JSON（`raw.json`、`decisions.json`、`issues.json`、`names.json`…） | **UTF-8 不带 BOM**、LF 换行（`validate_export.write_json` 的口径） |

**给 AI 的硬规则**：

1. 读本工作区里任何文本文件，**优先用 read 工具**（它按 UTF-8 处理，且能正确跳过 BOM）；
2. 必须用 shell 读时，PowerShell 5.1 下**一律显式写编码**：`Get-Content -Encoding UTF8 <file>`；
3. 读到以 `\ufeff` 开头的路径，**先 strip 掉再用**；
4. 写 `workspace.txt` 一律用 UTF-8 带 BOM；
5. **不要用记事本去编辑 `raw.json` / `decisions.json`**：裁判脚本按 `utf-8` 读它们，
   一旦带上 BOM 会直接抛异常（**退出码 1**）；而 `--status` 用的是"读不出来就当空"的写法，
   会**静默地谎报**成"① 未开始 / 还没识别"。改内容一律走脚本
   （`提交识别.py`、`导入审核.py`）；
6. 遇到乱码不要"猜"原路径——**停下来重新用正确编码读一遍**，或直接问用户。

---

## 10. 装完给用户的汇报模板

> 装好了，这台电脑可以用了。
> · 环境：Python 3.12.10 + openpyxl 3.1.5 ✅
> · 技能已装进：Codex、DSH、Cursor（3 个工具）✅
> · 工作文件夹：inbox / runs / archive / golden / pending / schemas 齐全 ✅
> · 自检和裁判脚本都跑通了 ✅
> · 新流程自测：通过 N 项，失败 0 项 ✅
>
> 接下来：把单据照片丢进 `inbox`，对我说 **"处理今天的图"** 就行。
