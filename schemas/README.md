# schemas —— 版式契约库

**每种表格一个 JSON**。它告诉裁判脚本：这张表有哪些列、哪些必填、
计量项怎么归类、合计上限是多少、归档叫什么名字。

裁判脚本 `validate_export.py` 是**通用**的——它不认识任何具体行业。
所有"这张表长什么样"的知识都在这里。

| 文件 | 是什么 |
|------|--------|
| `_default.json` | 通用兜底契约。**找不到专属契约的表型都用它**。改它 = 改所有表型的默认口径 |
| `来料分析_v1.json` | 化工厂来料分析单（示例，可直接删） |
| `库存日报_v1.json` | 化工厂库存日报（示例，可直接删） |

---

## 怎么给新表型加一份契约

### 首选做法：走样板 + `tools\注册表型.py`（**不要手抄草案**）

不要拿一串抽象问题去问车间员工（"这张表一行代表什么？有哪些列？"）——太抽象。
**做出来给他看**：

```bash
# ① 用样板图先识别一张（拿不到新任务就先建一个）
python tools/建任务.py
python tools/提交识别.py runs/YYYYMMDD_NNN --next
python tools/提交识别.py runs/YYYYMMDD_NNN --image IMG_001 --file 片段.json

# ② 出样板给人看（同时推断出一份契约草案）
python tools/生成样板.py runs/YYYYMMDD_NNN
```

它会从这张样板图自动推断出 `样板_契约草案.json`，并生成一份给人看的 `样板.xlsx`。
用户在第 5 页签写意见、确认无误后，**把草案交给脚本落成正式契约**：

```bash
python tools/注册表型.py runs/YYYYMMDD_NNN --name 来料分析 \
  --row-unit "一行 = 一个样品（一个桶 / 一批）" \
  --name-pattern "{date}_{sheet}_{batch}_第{n}张" \
  --notes "用户说桶号那列其实叫罐号，已改"
```

它会：
1. 读 `样板_契约草案.json`；
2. **硬拦**：草案里"还没被人回答的问题"（`row_unit` 还是"（请确认：…）"、有列没列名、
   有重复列名、`sheet_label` 没定）——**有就不许写，让你先去问用户**（`--force` 能绕过，不推荐）；
3. 写 `schemas/<表型名>_vN.json`（同一表改版自动递增，见下文"改版怎么办"）；
4. 写 `runs/<任务>/sample.json`，记下"这个样板什么时候被谁确认过"；
5. **自动跑一遍 `tools/回归测试.py`**，确认新契约没把裁判改坏；
   回归没全绿 → 退出码 2，并提示你立刻检查刚写的契约。

只想检查不想落盘：加 `--dry-run`；想指定用哪张图做样板：加 `--image-id IMG_001`。

**为什么不让 AI 把草案"抄"成正式契约**：契约是整套系统的规则层——抄错一个列名、
漏一个 `required`，后面**每一张图**都会跟着错，而且很难发现。草案本来就是脚本推断的，
落成正式契约也就是脚本搬一次的事，中间不该有第三个手。

**草案里的类型/必填/词表都是机器猜的，一定要按用户的话改**（改法：用 `--notes` 记下用户原话，
契约正文请让脚本从草案生成；确实要微调字段，改完必须跑 `python tools/回归测试.py`）。

### 字段说明（对照理解，不必手写）

```json
{
  "template": "报销单_v1",
  "sheet_label": "报销单",
  "row_unit": "一行 = 一笔报销",
  "columns": [
    {"name": "日期",   "type": "date", "required": true},
    {"name": "报销人", "type": "text", "required": true},
    {"name": "金额",   "type": "number"},
    {"name": "事由",   "type": "text"}
  ],
  "measurements": { "enabled": false },
  "vocabulary": {},
  "fact_keys": {"record_date": "日期", "material_name": "报销人"},
  "archive": {"name_pattern": "{date}_{sheet}_{n}"}
}
```

没有合计概念的表，把 `measurements.enabled` 写成 `false`，脚本就不做合计校验。

### 3. 字段全解

| 字段 | 必填 | 说明 |
|------|------|------|
| `template` | ✅ | 契约唯一标识。改了等于换一份契约 |
| `sheet_label` | ✅ | 匹配 `raw.json` 里每行的 `sheet`；`"*"` 表示兜底通配 |
| `row_unit` | | 一行代表什么（给人看的）。**`注册表型.py` 会硬拦：这一项没被人工回答就不写契约** |
| `columns[]` | | 列定义。`type` 取 `text`/`date`/`number`/`percent`/`identifier`；`required: true` 表示不能为空 |
| `measurements.enabled` | | 这张表有没有"合计"概念，默认 `true` |
| `measurements.unit` | | 计量单位，默认 `%` |
| `measurements.value_is_fraction` | | `value` 是不是"原文÷100"。**默认按单位自动判断**：`%` → 是；其它 → 不是 |
| `measurements.categories` | | 每类词怎么参与合计，见下表 |
| `measurements.default_category` | | 没列出的类别怎么办，默认 `always` |
| `measurements.sum_max` | | 合计上限。**超过即报严重问题，无容差** |
| `vocabulary` | | 同类词归类表。只写用户拍板过的词 |
| `fact_keys` | | 「成分事实」表 / `facts.jsonl` 的输出列 → 从 `raw.json` 哪个字段取值 |
| `archive.name_pattern` | | 归档命名规则模板（见下一节）。`null` = 不启用自定义命名 |

**类别参与合计的三种方式**：

| 值 | 含义 | 例子 |
|----|------|------|
| `never` | 永不计入合计 | 水（"除水组分之和"） |
| `if_present` | 有值才计入，空白留白不提示 | 杂（空白=留白，不补算） |
| `always` | 必须计入，解析不了就报问题 | 默认 |

### 4. `archive.name_pattern` 与它的占位符

`--name-pattern` 写进契约的 `archive.name_pattern`，是**归档文件名的模板**。
模板里可用的占位符（就是 `注册表型.py` 命令里那个例子用到的这些）：

| 占位符 | 代表 |
|--------|------|
| `{date}` | 图上认出来的日期（`YYYY-MM-DD`） |
| `{sheet}` | 表型名（这张图是什么表） |
| `{n}` | 同一天、同一表型的第几张（1 起）—— `第{n}张` 里的那个序号 |
| **业务列名**，如 `{批次}`、`{桶号}` | 取**该图第一行数据行**的该列值 |
| **逻辑名**，如 `{batch}`、`{container}` | 先经本契约的 `fact_keys` 映射到列名，再取该列值 |

**谁在读它**：`tools\提交识别.py` 在收下一张图时，按这个模板算出 `suggested_name`
写进 `raw.json`；`validate_export.py` 生成归档计划时优先采用它（没写才退回
`日期_表型_图片编号`）。所以**契约里写了模板就会生效**，不用再手工改名。

> **认不出的占位符会怎样**：直接**从名字里去掉**，绝不把 `{批次}` 这种字面量写进文件名；
> 同时会在提交时打印出来告诉你是哪个占位符没取到值（不静默）。
> 例：契约写 `{date}_{sheet}_{batch}_第{n}张`，但首行没有「批次」列 →
> 得到 `2026-09-10_库存日报_第1张`，并提示 `batch` 没取到值。

**想手工改名**：在 `task_review.xlsx` 的「文件归档清单」页填「新名字」列（或让 AI 写进
`decisions.json` 的 `archive_decisions[].suggested_name`），归档脚本会照用。
注意「建议文件名」列是**机器生成的**，`tools\导入审核.py` 会拿它与归档计划比对——
只有真的不一样才算你改了名，不会把每一行都误报成改名。

### 5. 用户拍板过的词写哪？

- **只对这一张表生效** → 写进这份 schema 的 `vocabulary`；
- **对所有表都生效** → 写进工作区根目录的 `aliases.json`
  （用 `python tools\收录.py aliases runs/YYYYMMDD_NNN` 收，别手抄）。

两处都支持 `water` / `水类` / `_已确认_水类` 这类写法，脚本会识别成同一类。
**登记过的词，脚本一定用**——不会再出现"加了却没用"的情况。

### 6. 改版怎么办

同一张表改版 = **新增 `_v2`**（`注册表型.py` 自动递增，不会覆盖 `_v1`），旧版**永久保留**。
历史照片按它当时那版解析，这是可追溯性的基础。

---

## 验证

改完 schema 一定要跑（`注册表型.py` 也会自动替你跑一次回归）：

```
python tools/回归测试.py
```

它用隔离工作区验证裁判行为，**不动真实数据**。
如果你还动了 `tools/` 下的脚本，再补一条 `python tools/新流程自测.py`。

> **提醒**：`schemas/` 里任何 JSON 写坏了，脚本启动时会打印
> 「schema 读不了，已跳过」并继续跑（不会崩），但那份契约就不生效了——
> 看到这行提示要立刻修。
