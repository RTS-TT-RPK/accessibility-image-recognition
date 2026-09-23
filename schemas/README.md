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

### 首选做法：走样板（做出来给人看，不要用问卷问）

不要拿一串抽象问题去问车间员工（"这张表一行代表什么？有哪些列？"）——太抽象。
**做出来给他看**：

```
python tools/生成样板.py runs/YYYYMMDD_NNN
```

它会从这张样板图自动推断出一份契约草案，并生成一份给人看的 `样板.xlsx`。
用户在第 5 页签完意见、确认无误后，把 `样板_契约草案.json` 按下面的字段说明
整理成 `schemas/<表型名>_v1.json` 即可。

**草案里的类型/必填/词表都是机器猜的，一定要按用户的话改。**

### 字段说明（写契约时对照）

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
| `row_unit` | | 一行代表什么（给人看的） |
| `columns[]` | | 列定义。`type` 取 `text`/`date`/`number`/`percent`/`identifier`；`required: true` 表示不能为空 |
| `measurements.enabled` | | 这张表有没有"合计"概念，默认 `true` |
| `measurements.unit` | | 计量单位，默认 `%` |
| `measurements.value_is_fraction` | | `value` 是不是"原文÷100"。**默认按单位自动判断**：`%` → 是；其它 → 不是 |
| `measurements.categories` | | 每类词怎么参与合计，见下表 |
| `measurements.default_category` | | 没列出的类别怎么办，默认 `always` |
| `measurements.sum_max` | | 合计上限。**超过即报严重问题，无容差** |
| `vocabulary` | | 同类词归类表。只写用户拍板过的词 |
| `fact_keys` | | 「成分事实」表输出列 → 从 `raw.json` 哪个字段取值 |
| `archive.name_pattern` | | 归档命名规则；`null` = 直接用模型建议的文件名 |

**类别参与合计的三种方式**：

| 值 | 含义 | 例子 |
|----|------|------|
| `never` | 永不计入合计 | 水（"除水组分之和"） |
| `if_present` | 有值才计入，空白留白不提示 | 杂（空白=留白，不补算） |
| `always` | 必须计入，解析不了就报问题 | 默认 |

### 4. 用户拍板过的词写哪？

- **只对这一张表生效** → 写进这份 schema 的 `vocabulary`；
- **对所有表都生效** → 写进工作区根目录的 `aliases.json`（脚本会自动并进每一份契约）。

两处都支持 `water` / `水类` / `_已确认_水类` 这类写法，脚本会识别成同一类。
**登记过的词，脚本一定用**——不会再出现"加了却没用"的情况。

### 5. 改版怎么办

同一张表改版 = **新增 `_v2`**，旧版**永久保留**。
历史照片按它当时那版解析，这是可追溯性的基础。

---

## 验证

改完 schema 一定要跑：

```
python tools/回归测试.py
```

它用隔离工作区验证裁判行为，**不动真实数据**。

> **提醒**：`schemas/` 里任何 JSON 写坏了，脚本启动时会打印
> 「schema 读不了，已跳过」并继续跑（不会崩），但那份契约就不生效了——
> 看到这行提示要立刻修。
