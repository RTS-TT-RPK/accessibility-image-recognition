# -*- coding: utf-8 -*-
"""导入审核 —— 把用户填好的 task_review.xlsx 变成权威的 decisions.json。

为什么需要它（这是整条流水线上最大的一处自动化收益）：
    AGENTS.md 第 3 节第 13 步要求"把 Excel 内容整理成 decisions.json"。
    过去这一步是 AI 用眼睛把 Excel 抄进 JSON——整条流水线的数据要么来自图片识别、
    要么来自 scripts 的确定性校验，**只有这一步是"人手抄写"**，也最容易抄错：
    抄错一个 issue_id，那条人工决定就静默失效（脚本只会当作"没填"）；
    抄错一个数值，错值就直接进了最终 Excel。
    所以本脚本用 openpyxl 直接读，人只负责在 Excel 里填。

它读什么（**按表头名字定位列，不按固定列号**）：
    页签「问题清单」        问题编号 / 员工决定（必读）  + 备注（有就读）
    页签「文件归档清单」    图片编号 / 是否归档(是/否)（必读） + 新名字（有就读）
    注意：verify 脚本里的 warn_unsaved_decisions() 硬编码了 row[9]，
    列序一变（有人插一列）就会读错列、而且不报错。这里不复制这种脆弱写法。

它写什么：
    <run_dir>/decisions.json —— 唯一权威的人工决定落点。
    **合并，绝不覆盖**：Excel 没提到的条目原样保留，顶层多出来的键也原样保留。
    写入格式与 validate_export.write_json 完全一致：
        json.dump(..., ensure_ascii=False, indent=1)，UTF-8 **不带 BOM**，LF 换行。
    文件内容与将要写入的字节完全相同时，打印「无变化」并**不重写**（mtime 保持稳定）。
    写入方式是"先写 .tmp 再 os.replace"，绝不产生半截文件。

决定的合法写法（复用裁判 validate_export 自己的词表，不发明新语法）：
    通过 / 确认无误 / 照录 ……（APPROVE_WORDS 全集，含 ok/keep 等）
    沿用候选A / 沿用候选B      （CANDIDATE_LABELS，脚本会还原成候选值，绝不把标签当数据）
    见备注
    字段=值 / 计量项=值        （分隔符 ; ； , ， 换行；键值之间 = : ：）
        键名必须是"这张表认识的"：schema 声明的列、raw.json 里出现过的字段、
        计量项名、或问题自己点名的字段。写别的名字要重写。
    未注册词=已认可的词        （UNREGISTERED_TERM 类问题的词表登记，如 `不纯率=杂质`）
    什么都不是的写法一律**拒绝**，并逐条打印 issue_id + 原因 + 合法写法清单。

除格式之外，导入时还会拦下三类"格式合法、但照填没用"的决定（都是实测踩到过的）：
    1) 答非所问：问题问的是「物料」，决定却写 `含水率=0.5%`。裁判执行时既补不上物料、
       又给该行凭空加了个计量项，问题照样阻塞 —— 看着成功、其实什么都没解决。
    2) 文本列裸值：给「必填字段为空：物料」直接填一句"乱填的东西"。裸值对数值/日期
       字段才有意义（0.5% / 2026-09-10 一眼能看出是什么），文本列的裸值无从核对，
       必须写成 `物料=值`。
    3) 数值不合格：`含水率=abc`。判得出类型就必须能解析；判不出类型才不拦
       （`物料=甲醇` 这类正常文本修正不会被误拦）。

用法：
    python tools\\导入审核.py runs\\YYYYMMDD_NNN [--dry-run] [--force]

退出码：
    0  正常（有决定被导入，或本来就没有需要导入的新决定；--dry-run 一律 0）
    2  工作簿读不了（不存在、不是 xlsx、被 Excel 独占）、已有的 decisions.json 读不到，
       或**这次填进来的决定没有一条能落地**（全被拒绝 / 全没填）
       —— 后一种情况加 --force 可以跳过这条检查（但该拒的还是拒，不会写进任何一条坏决定）

按行拒绝 ≠ 按文件拒绝：只要还有一条决定有效，就照常写入，并把被拒绝的行全列出来。
"""
import argparse
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import validate_export as ve          # noqa: E402  —— 唯一裁判，词表与解析一律复用它的

# ---------------------------------------------------------------- 表头名清单
# 每种列给一组候选写法，**前面的优先**。改版式时往这里加一个词即可，
# 不要在下面写死列号。
#
# 为什么要排优先级：审核表里可能同时存在「建议文件名」和用户手加的「新名字」两列，
# 而「新名字」才是他真正表达的意见。若按"先到先得"扫列，脚本会挑中左边那列
# （往往是脚本自己生成的建议名），用户的改名就被**静默忽略**了。
HEADER_ALIASES = {
    "issue_id":    ("问题编号", "问题id", "issue_id", "issueid", "编号"),
    "decision":    ("员工决定", "人工决定", "员工意见", "处理意见", "决定"),
    "note":        ("备注", "员工备注", "批注", "说明"),
    "image_id":    ("图片编号", "图片id", "image_id", "imageid"),
    "confirmed":   ("是否归档(是/否)", "是否归档（是/否）", "是否归档", "确认归档", "归档"),
    "new_name":    ("新名字", "改名", "建议名字", "建议文件名", "文件名"),
}

SHEET_ISSUES = "问题清单"
SHEET_ARCHIVE = "文件归档清单"

# "是/否" 的写法可能被 Excel 自动改掉（是/否/TRUE/TRUE…），宽容一点，但**绝不猜**：
# 认不出来的值进拒绝清单，不当作"是"处理。
YES_WORDS = ("是", "y", "yes", "true", "1", "确认", "归档", "√", "对")
NO_WORDS = ("否", "n", "no", "false", "0", "不", "不归档", "×", "x")

CANDIDATE_LABELS = ve.CANDIDATE_LABELS          # 沿用候选A / 候选B ……
APPROVE_WORDS = ve.APPROVE_WORDS                # 通过 / 确认无误 / 照录 ……
NOTE_ONLY = "见备注"


# ---------------------------------------------------------------- 小工具
def log(msg):
    print("  " + msg)


def die(msg, code=2):
    print("[错误] " + msg)
    sys.exit(code)


def to_display(value):
    """与裁判同款的取值清洗：None -> ""，其余 strip。"""
    return ve.to_display(value)


def norm_header(value):
    """表头归一：去掉所有空白、全角括号转半角、小写化。"""
    text = to_display(value).replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", text).lower()


def find_header(rows, aliases, max_scan=6):
    """在前 max_scan 行里找表头行，返回 (列名->列下标[0 基], 表头行号[1 基])。

    为什么不是固定第 1 行：审核表是人会另存/转存的派生物，
    有人喜欢在上面加一行标题、或把表头往下推。扫 6 行足够覆盖这种改动，
    同时几乎不可能把数据行误当表头（数据里不会出现「问题编号」这种词）。

    匹配按候选词的**先后顺序**逐轮认领：
    第一轮只认每列最想要的那个名字（如「问题编号」「新名字」），
    认不下来的列才在下一轮接受更宽泛的写法（如「编号」「文件名」）。
    这样「问题编号」和用户手加的「编号」列同时存在时，不会张冠李戴。

    入参 rows 是**已经读出来的前几行**（元组序列），而不是 worksheet：
    read_only=True 的流式表没有随机访问的 ws.cell()，
    硬用 ws.cell() 会直接抛 ValueError（"Worksheet is read only"）。
    """
    wanted = {}
    for key, names in aliases.items():
        for name in names:
            wanted[norm_header(name)] = key
    max_rounds = max(len(v) for v in aliases.values()) if aliases else 1

    best = ({}, 0)
    for idx, row in enumerate(list(rows)[:max_scan]):
        cells = [norm_header(v) for v in (row or ())]
        taken, row_map = set(), {}
        for _ in range(max_rounds):
            if len(row_map) == len(aliases):
                break
            for key, names in aliases.items():
                if key in row_map:
                    continue
                for name in names:
                    want = norm_header(name)
                    hit = next((c for c, got in enumerate(cells)
                                if c not in taken and got == want), None)
                    if hit is not None:
                        taken.add(hit)
                        row_map[key] = hit
                        break
        if len(row_map) > len(best[0]):
            best = (row_map, idx + 1)
    return best


def serializable(value):
    """把 openpyxl 读出来的值变成能进 JSON 的东西（datetime 之类转文本）。"""
    import datetime as _dt
    if value is None:
        return None
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        # Excel 里 1234 常被读成 1234.0；只有整数才收，避免把 0.5 变成 "0.5"
        return str(int(value))
    return value


def decision_text(value):
    """决定文本：数字要按 Excel 里的原文读。

    坑：员工在"员工决定"格里填 `含水率=0.5`，Excel 会存成数字 0.5，
    openpyxl 读出来是 float——直接 str() 会得到 "0.5"（这一步没问题），
    但 float 的 repr 在别的值上会变成 "0.30000000000000004"。
    所以整数浮点转成整数字符串，其余一律用 repr 的最短表示。
    """
    val = serializable(value)
    if val is None:
        return ""
    if isinstance(val, float):
        return repr(val)
    return to_display(val)


# ---------------------------------------------------------------- 决定写法校验
def split_pairs(text):
    """按裁判同款分隔符切分 `字段=值` 串，返回 (键值对列表, 认不出的片段列表)。

    分隔符与键值符完全照抄 validate_export.apply_decisions 的正则，
    避免"导入时认、跑裁判时不认"这种两套口径。
    """
    pairs, bad = [], []
    for part in re.split(r"[;；,，\n]+", text):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^\s*(.+?)\s*[=:：]\s*(.+?)\s*$", part)
        if m:
            key, value = m.group(1).strip(), m.group(2).strip()
            if not key or not value:
                bad.append(part)
                continue
            pairs.append((key, value))
        else:
            bad.append(part)
    return pairs, bad


def percent_cell_warning(key, value):
    """员工填的百分数被 Excel 存成数值格时，值会缩水 100 倍。

    实测：在"员工决定"格里输入 `含水率=50%`，Excel 把它当百分数格式的**数字**存，
    openpyxl 读回来是 0.5 —— 与员工看到的 "50%" 差 100 倍。
    本脚本**不猜、不自动换算**（红线），只把这种情况明确报出来，请他改写文本。
    """
    raw = to_display(value)
    ms = list(re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*%", raw))
    if not ms:
        return None
    number = ms[-1].group(1)
    keypos = raw.find(key)
    if keypos >= 0 and ms[-1].start() < keypos:
        return None                     # 百分号在字段名之前，不像是"值被缩放"
    try:
        shown = float(number) / 100.0
    except ValueError:
        return None
    if abs(shown - round(shown, 10)) > 1e-12 and ("%.10g" % shown) in raw.replace("%", ""):
        return ("「%s」在 Excel 里是数值格：读到的是 %s（你看到的应是 %s）。"
                "请把该格改成文本格式后重填，或直接写 %s 的十进制形式 —— 本脚本不做换算。"
                % (key, raw, number + "%", number))
    if abs(shown - 0.5) < 1e-12 and "0.5" in raw.replace("%", ""):
        return ("「%s」在 Excel 里是数值格：格里的数字 0.5 带百分号格式时显示为 50%%。"
                "请确认要录的是 50%% 还是 0.5%%，改成文本格式重填 —— 本脚本不做换算。" % key)
    return None


def build_env(run, schemas, issues=None):
    """准备"哪些字段名/计量项名是合法的"以及"某个字段应该是什么类型"。

    三种来源都要算上：
      1) schema 声明的列（有 type，决定数值怎么校验）；
      2) raw.json 里真实出现过的 fields 键 与 components 名
         （AGENTS.md 第 5 节允许"版式跟样板不一样就照实记"，只看 schema 会漏）；
      3) issues.json 里问题自己点名的 field ——
         这是**人工决定最常写**的那个名字。例如「含水率」既不是 schema 列、
         也没进 components（它就在 uncertain 里），但裁判的
         apply_overrides_to_row(add_if_missing=True) 会把它作为新计量项补进去。
         若这里不认它，人填的 `含水率=0.5%` 就会被误判成"不是合法字段名"而拒掉。
    """
    col_names, col_types = {}, {}
    for sch in (schemas or {}).values():
        for col in (sch.get("columns") or []):
            if not isinstance(col, dict) or not col.get("name"):
                continue
            name = to_display(col["name"])
            ctype = to_display(col.get("type")) or "text"
            if name not in col_names:
                col_names[name] = ctype
            elif col_names[name] != ctype:
                col_names[name] = None          # 同名不同型：类型不可判定，放弃数值校验
            col_types.setdefault(ctype, set()).add(name)
    comp_names = set()
    for row in run.get("rows", []):
        for key in (row.get("fields") or {}).keys():
            k = to_display(key)
            if k and k not in col_names:
                col_names[k] = None             # 出现过，但类型不可判定
        for comp in (row.get("components") or []):
            nm = to_display(comp.get("name"))
            if nm:
                comp_names.add(nm)
    issue_fields = {}                           # 字段名 -> {它出现在哪些问题类型上}
    for issue in (issues or []):
        if not isinstance(issue, dict):
            continue
        fname = to_display(issue.get("field"))
        if fname:
            issue_fields.setdefault(fname, set()).add(to_display(issue.get("type")))
    return {
        "col_names": col_names,
        "comp_names": comp_names,
        "issue_fields": issue_fields,
        "unregistered_terms": set(n for n, types in issue_fields.items()
                                  if "UNREGISTERED_TERM" in types),
        "all_names": set(col_names) | comp_names | set(issue_fields),
        "measure_unit": _measure_unit(schemas),
        "sheet_columns": _sheet_column_types(schemas),
        "schemas_by_label": schemas or {},
    }


def _measure_unit(schemas):
    units = set()
    for sch in (schemas or {}).values():
        meas = sch.get("measurements") or {}
        if meas.get("enabled", True):
            units.add(to_display(meas.get("unit")) or "%")
    return units


def _sheet_column_types(schemas):
    """{工作表名: {列名: 类型}}，用于"这个问题的字段在本表里该是什么类型"。"""
    out = {}
    for label, sch in (schemas or {}).items():
        cols = {}
        for col in (sch.get("columns") or []):
            if isinstance(col, dict) and col.get("name"):
                cols[to_display(col["name"])] = to_display(col.get("type")) or "text"
        out[label] = cols
    return out


def resolve_field_type(field, sheet, env):
    """判断某个字段名在**这张工作表里**该是什么类型；判不出来返回 None。

    顺序很关键：先按这张表的 schema 列判断（那是表型的权威声明），
    判不出来才退回"所有 schema 的列并集"，最后才把计量项名当百分比。
    反过来的话，「物料」这种既在 schema 里、又可能被当成计量项名的词，
    会先被当成百分比，`物料=甲醇` 就会被误判成"不是数值"。
    """
    name = to_display(field)
    if not name:
        return None
    if name in (env.get("comp_names") or set()):
        return "percent"                    # 计量项按百分比算（与 sum_max 同一刻度）
    if name == "row_role":
        return "row_role"
    sheet_cols = (env.get("sheet_columns") or {}).get(to_display(sheet)) or {}
    if name in sheet_cols:
        return sheet_cols[name]
    if name in (env.get("col_names") or {}):
        return env["col_names"][name]
    if name in (env.get("issue_fields") or {}):
        # 问题自己点名的字段，schema 又没管它：按裁判的口径它会被补成**计量项**
        # （apply_overrides_to_row 的 add_if_missing），所以按百分比处理。
        #
        # 例外：UNREGISTERED_TERM 问题的 field 就是"那个未注册的词本身"，
        # 它的决定是**同类词映射**（`不纯率=杂质`），右值天然不是数值，
        # 按百分比校验会把合法的词表决定误拦。所以返回 None = 不做数值校验。
        if "UNREGISTERED_TERM" in (env["issue_fields"][name] or set()):
            return None
        return "percent"
    return None


def looks_numeric(text):
    """这段话看起来是不是"在写一个数"。`0.5%`/`-3`/`1e-05` 算，「北罐区7#」不算。"""
    raw = to_display(text)
    if not raw:
        return False
    body = raw.replace("％", "%").replace("%", "").replace(",", "").replace("，", "") \
              .replace(" ", "").strip()
    if not body:
        return False
    try:
        float(body)                     # 含 1e-05
        return True
    except ValueError:
        pass
    return re.match(r"^[+-]?\d+(\.\d+)?$", body) is not None


def check_value(key, value, vtype, env, sheet=None, explicit=True):
    """决定里的数值是否合格。返回 None 表示通过，否则返回中文原因。

    三个尺度必须分清（这是本工作区最容易出错的地方）：
      * percent 列 / 计量项 —— 图上原文写法，`0.5%` 或裸数 `0.5` 都按百分数解释
        （parse_percent 的约定：裸数按百分数），所以 `含水率=0.5%` 合法；
      * number 列 —— 纯数字，不能带 %；
      * date 列 —— 必须能认出年月日。

    文本修正的例外（**只在键名确实是本表文本列时**才放行）：
    人有时会用 `物料=甲醇` 去修正文本列，也可能写成 `批次=B002批`。
    这些值在 schema 里明明是 text/identifier 列，不该因为"解析不出数值"被拦。
    但反过来，`含水率=abc` 里「含水率」是计量项名，写什么就是什么值，
    **不能**拿"文本修正"当挡箭牌偷偷放行 —— 那等于让错值进 Excel。
    """
    raw = to_display(value)
    if not raw:
        return None
    if vtype == "row_role":
        if raw in ve.ROW_ROLES:
            return None
        return "row_role 只能是 %s，收到的是「%s」" % ("/".join(ve.ROW_ROLES), raw)
    text_column = _is_text_column(key, sheet, env)
    if vtype == "percent":
        if ve.parse_percent(raw) is None:
            if explicit and text_column and not looks_numeric(raw):
                return None             # 键名是本表的文本列：这是文本修正，不是数值
            return ("「%s」要的是数值，但「%s」解析不出百分比。"
                    "图上原文怎么写就怎么写，例如 0.5%% 或 0.5" % (key, raw))
        return None
    if vtype == "number":
        if not ve.value_matches_type(raw, "number"):
            if explicit and text_column and not looks_numeric(raw):
                return None
            return "「%s」声明为数值列，但「%s」不是纯数字（数值列不能带 %% 或单位）" % (key, raw)
        return None
    if vtype == "date":
        if not re.search(r"\d", raw):
            return None                 # 纯说明文字（如「待补」）不算写错日期
        if not ve.value_matches_type(raw, "date"):
            return "「%s」声明为日期列，但「%s」认不出年月日（应写成 2026-09-10 这种）" % (key, raw)
        return None
    return None                         # text / identifier / 判不出类型：不挑


def _is_text_column(name, sheet, env):
    """这个名字是不是**本工作表** schema 里声明的文本/编号列。"""
    sheet_cols = (env.get("sheet_columns") or {}).get(to_display(sheet)) or {}
    if name in sheet_cols:
        return sheet_cols[name] in ("text", "identifier")
    if name in (env.get("col_names") or {}):
        return env["col_names"][name] in ("text", "identifier")
    return False


def registry_lookup(value, env, sheet):
    """右值是不是"本表已认可的词"；是则原样返回它，否则返回 None。

    "已认可"的判定必须与裁判同源，只看这几处：
      ① 这张表 schema 的列名；
      ② 这张表 schema 的 vocabulary 词表（= 内置默认词 + aliases.json 里用户拍板过的词）；
      ③ raw.json 里真实出现过的 fields 键 / components 名；
      ④ issues.json 里问题点名过的 field。
    **不看 issues.json 里所有问题的 field**——只认已认可的名字，
    否则任何出现在问题清单里的词都能互相映射，词表就被污染了。
    """
    text = to_display(value)
    if not text:
        return None
    # 先看词表（最权威：schema 声明的 + aliases.json 里用户拍板过的）
    sch = (env.get("schemas_by_label") or {}).get(to_display(sheet))
    if sch is None:
        sch = (env.get("schemas_by_label") or {}).get("*")
    for words in ((sch or {}).get("vocabulary") or {}).values():
        if text in [to_display(w) for w in (words or [])]:
            return text
    if text in (env.get("all_names") or set()):
        return text
    return None


def validate_decision(text, issue, env):
    """校验一条决定。返回 (ok, info, reason)。

    info["kind"] ∈ approve / candidate / note_only / value / pairs / mixed
    info["overrides"] 是按裁判口径还原后的 {字段: 值}（候选标签已还原成真实值）。
    """
    text = to_display(text)
    itype = to_display(issue.get("type"))
    sheet = to_display(issue.get("sheet"))
    field = to_display(issue.get("field"))
    cands = issue.get("candidates") or []

    if ve.is_approve(text):
        return True, {"kind": "approve", "overrides": {}}, ""

    if text in CANDIDATE_LABELS:
        idx = CANDIDATE_LABELS[text]
        if idx >= len(cands):
            return False, None, ("问题清单里这条没有候选%s，无法沿用（候选列是空的）"
                                 % ("A" if idx == 0 else "B"))
        resolved = to_display(cands[idx])
        if not resolved:
            return False, None, "候选%s 是空的，沿用它会写进一个空值" % ("A" if idx == 0 else "B")
        # 还原后的真实值也要过一遍数值校验，否则标签会把错值带进来
        reason = check_value(field or "候选值", resolved,
                             resolve_field_type(field, sheet, env), env,
                             sheet=sheet, explicit=False)
        if reason:
            return False, None, "候选值本身不合格：%s" % reason
        return True, {"kind": "candidate", "overrides": {}}, ""

    if text == NOTE_ONLY:
        return True, {"kind": "note_only", "overrides": {}}, ""

    pairs, leftovers = split_pairs(text)
    overrides = {}
    unregistered = env.get("unregistered_terms") or set()
    registered_words = env.get("all_names") or set()
    if pairs:
        for key, value in pairs:
            key = to_display(key)
            value = to_display(value)
            if key in unregistered:
                # `不纯率=杂质` 不是"把数值改成杂质"，而是**登记同类词的归类**。
                # 它过不了数值校验（parse_percent("杂质") 必然失败），
                # 但也不能随便放过：右值必须是本表已认可的词，
                # 否则用户等于在决定里发明了一个新词 —— 那正是 aliases 要防的事。
                target = registry_lookup(value, env, sheet)
                if target is None:
                    if looks_numeric(value):
                        return False, None, (
                            "「%s」是未注册的同类词，写数值改不动它。"
                            "这条决定要登记它的归类：%s=已认可的词（例如 %s=杂质）；"
                            "确实要改某个计量项的数值，请直接写 计量项名=值" % (key, key, key))
                    return False, None, (
                        "「%s」是未注册的同类词，这条决定要把它的归类登记下来，"
                        "所以右边必须是一个本表已认可的词（例如 %s=杂质）。"
                        "「%s」目前不算已认可的词。" % (key, key, value))
                overrides[key] = target
                continue
            vtype = resolve_field_type(key, sheet, env)
            if key not in registered_words:
                return False, None, ("「%s」不是这张表的字段，也不是已出现的计量项名。"
                                     "写法应形如 字段名=值（可一次写多个，用逗号或分号隔开）" % key)
            warn = percent_cell_warning(key, value)
            if warn:
                return False, None, warn
            reason = check_value(key, value, vtype, env, sheet=sheet, explicit=True)
            if reason:
                return False, None, reason
            overrides[key] = value
        if leftovers:
            # 裁判遇到认不出的片段是**直接忽略**的；导入端不能沿用这种静默：
            # 多半是关键字段没写等号，静默丢掉等于人工决定悄悄消失。
            return False, None, ("这句话里「%s」认不出是「字段=值」还是裸值 —— "
                                 "请写成 字段名=值 的形式（多个用逗号或分号隔开）"
                                 % "、".join(leftovers))
        # 这张关键的一步：决定有没有**对着这条问题**。
        # 实测过一种真实误填：给「必填字段为空：物料」填了 `含水率=0.5%`，
        # 格式合法、数值也合法，于是被照单收下；可裁判执行时
        # 既补不上物料（含水率不是本行字段），又给这一行凭空加了个计量项，
        # 问题照样阻塞 —— 一条"看起来成功、其实啥也没解决"的人工决定。
        # 这种"答非所问"必须在导入时就拦下，不能等重跑后看问题条数才发现。
        if field and itype != "ROW_ROLE_INVALID" and field not in overrides:
            got = "、".join(overrides.keys()) or "（空）"
            return False, None, (
                "这条问题是关于「%s」的（%s），但决定里写的是「%s」，没有落到「%s」上 —— "
                "照着填不会解决问题，该行仍会阻塞。要修它请写 %s=值。"
                % (field, itype or "类型未知", got, field, field))
        return True, {"kind": "pairs", "overrides": overrides}, ""

    # 没有等号：整句当作"这个问题的那个字段的值"（裁判也是这么兜底的）
    if not field:
        return False, None, ("这句话既不是通过类决定，也没有等号，而这条问题又没有指定字段，"
                             "无从落值。请写成 字段名=值，或填 通过 / 见备注")
    key = field
    if key not in (env.get("all_names") or set()):
        return False, None, ("「%s」不是这张表的字段，也不是已出现的计量项名，"
                             "不能把这句话当成它的值" % key)
    vtype = resolve_field_type(field, sheet, env)
    if vtype in ("text", "identifier"):
        # 裸值只对"数值型/日期型"字段有意义（0.5% / 2026-09-10 一眼能看出是什么）。
        # 普通文本字段的裸值等于**凭空认定**：客服在"物料"格里写「乱填的东西」，
        # 脚本没有任何依据判断这是不是物料名，只能拒绝并请他写清楚。
        return False, None, ("「%s」是文本列，裸值会被原样当成它的内容，无从核对。"
                             "请用「%s=值」写明字段（例如 %s=IPAC）" % (key, key, key))
    warn = percent_cell_warning(key, text)
    if warn:
        return False, None, warn
    reason = check_value(key, text, vtype, env, sheet=sheet, explicit=False)
    if reason:
        return False, None, reason
    return True, {"kind": "value", "overrides": {key: text}}, ""


def accepted_forms_text():
    return ("可用写法：① %s；② 沿用候选A / 沿用候选B；③ 见备注；"
            "④ 字段名=值（多个用逗号或分号隔开，例如 物料=IPAC, 含水率=0.5%%）"
            % " / ".join(sorted(APPROVE_WORDS)))


# ---------------------------------------------------------------- 读审核表
def read_workbook(path, max_scan=6):
    """读审核表，返回 (结构, 错误文本)。

    结构 = {
      "issues":  [(行号, issue_id, decision, note)],
      "archive": [(行号, image_id, 是否归档原文, 新名字)],
      "notes":   [给终端打印的提示],
    }

    全程 read_only=True 流式读：审核表可能有几千行问题，
    整表载入内存没必要；而且这份表随时可能正开在 Excel 里，流式读更不容易撞锁。
    """
    from openpyxl import load_workbook
    if not os.path.exists(path):
        return None, "找不到审核表：%s（先跑 python validate_export.py <run_dir> 生成）" % path
    if os.path.isdir(path):
        return None, "审核表路径是个目录：%s" % path
    try:
        wb = load_workbook(path, read_only=True, data_only=True)
    except PermissionError:
        return None, ("读不了审核表（被占用）：%s\n"
                      "       它多半正开在 Excel 里。请先关闭 Excel 再重跑本命令。" % path)
    except Exception as exc:
        return None, "读不了审核表：%s（%s：%s）" % (path, type(exc).__name__, exc)

    out = {"issues": [], "archive": [], "notes": []}
    try:
        names = list(wb.sheetnames)
        if SHEET_ISSUES not in names and SHEET_ARCHIVE not in names:
            return None, ("审核表里既没有「%s」也没有「%s」页签（实际页签：%s）—— "
                          "这不像是一份 task_review.xlsx" % (SHEET_ISSUES, SHEET_ARCHIVE,
                                                              "、".join(names) or "空"))
        for sheet_name, aliases, kind in (
                (SHEET_ISSUES, {"issue_id": HEADER_ALIASES["issue_id"],
                                "decision": HEADER_ALIASES["decision"],
                                "note": HEADER_ALIASES["note"]}, "issues"),
                (SHEET_ARCHIVE, {"image_id": HEADER_ALIASES["image_id"],
                                 "confirmed": HEADER_ALIASES["confirmed"],
                                 "new_name": HEADER_ALIASES["new_name"]}, "archive")):
            if sheet_name not in names:
                out["notes"].append("审核表里没有「%s」页签，本页决定全部跳过（不是错误）" % sheet_name)
                continue
            ws = wb[sheet_name]
            stream = ws.iter_rows(values_only=True)
            head_rows = []
            for _ in range(max_scan):
                try:
                    head_rows.append(next(stream))
                except StopIteration:
                    break
            cols, header_row = find_header(head_rows, aliases, max_scan)
            if kind == "issues" and ("issue_id" not in cols or "decision" not in cols):
                return None, ("「%s」页签里找不到表头（需要「问题编号」和「员工决定」两列）。"
                              "找到的列：%s" % (sheet_name, _show_cols(cols, aliases)))
            if kind == "archive" and ("image_id" not in cols or "confirmed" not in cols):
                out["notes"].append("「%s」页签里找不到「图片编号」或「是否归档(是/否)」表头，"
                                    "本页决定全部跳过" % sheet_name)
                continue
            if header_row > 1:
                out["notes"].append("「%s」的表头在第 %d 行（不是第 1 行），已按表头名定位"
                                    % (sheet_name, header_row))

            def field(row, key):
                idx = cols.get(key)
                if idx is None or idx >= len(row or ()):
                    return None
                return row[idx]

            if kind == "issues" and "note" not in cols:
                out["notes"].append("「%s」页签没有「备注」列，备注按空处理" % sheet_name)
            if kind == "archive" and "new_name" not in cols:
                out["notes"].append("「%s」页签没有「新名字」列，不做改名" % sheet_name)

            rows = head_rows[header_row:] + list(stream)
            empty_decision = 0
            for offset, row in enumerate(rows):
                rowno = header_row + offset + 1
                if kind == "issues":
                    iid = to_display(serializable(field(row, "issue_id")))
                    dec = decision_text(field(row, "decision"))
                    note = decision_text(field(row, "note"))
                    if not iid and not dec:
                        continue
                    if not dec:
                        empty_decision += 1
                        continue                    # 没填决定 = 没意见，跳过（不猜）
                    out["issues"].append((rowno, iid, dec, note))
                else:
                    iid = to_display(serializable(field(row, "image_id")))
                    raw_conf = to_display(serializable(field(row, "confirmed")))
                    new_name = to_display(serializable(field(row, "new_name")))
                    if not iid:
                        continue
                    if not raw_conf:
                        continue                    # 空 = 没说，**不猜**
                    out["archive"].append((rowno, iid, raw_conf, new_name))
            if kind == "issues" and empty_decision:
                out["notes"].append("「%s」里有 %d 行没填员工决定，已跳过（不猜）"
                                    % (sheet_name, empty_decision))
    finally:
        wb.close()
    return out, ""


def _show_cols(cols, aliases):
    got = "、".join(sorted(cols)) or "无"
    want = "、".join(sorted(set(sum((list(v) for v in aliases.values()), []))))
    return "找到 %s（找的是：%s）" % (got, want)


# ---------------------------------------------------------------- 合并 decisions.json
def load_existing(path):
    """读已有的 decisions.json。

    用 utf-8-sig 读：历史文件可能是记事本另存出来的**带 BOM** 版本，
    用 utf-8 读会在 json.loads 直接抛异常（BOM 不是合法 JSON 字符）。

    读不了怎么办——这里分两种情况，**不能一律当空文档**：
      * 解析失败（文件坏了/被改坏）：退回空文档并**明确报出来**，同时把原文件
        另存一份 .bad 备份，绝不让人填过的决定无声消失；
      * 读不到（PermissionError：文件被别的程序独占）：**直接拒绝继续**。
        这一条是刻意加的：若当成空文档往下走，"合并"就会变成"用 Excel 里的几条
        覆盖掉整个文件"，把用户之前所有决定一次清空——静默的数据丢失，
        比报错严重得多。
    """
    if not os.path.exists(path):
        return {}, "", None
    try:
        with io.open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except PermissionError:
        return None, "", "已有的 decisions.json 读不了：文件被别的程序占用（无权限）。"
    except Exception as exc:
        backup = path + ".bad"
        try:
            import shutil
            shutil.copy2(path, backup)
            saved = "，原文件已备份为 %s" % os.path.basename(backup)
        except Exception:
            saved = "（原文件备份失败）"
        return {}, ("已有的 decisions.json 读不了（%s：%s），本次会以空白文档重写它%s"
                    % (type(exc).__name__, exc, saved)), None
    if not isinstance(data, dict):
        backup = path + ".bad"
        try:
            import shutil
            shutil.copy2(path, backup)
            saved = "，原文件已备份为 %s" % os.path.basename(backup)
        except Exception:
            saved = "（原文件备份失败）"
        return {}, "已有的 decisions.json 顶层不是对象，本次会以空白文档重写它%s" % saved, None
    return data, "", None


def dumps_like_referee(obj):
    """按裁判的写法序列化：ensure_ascii=False, indent=1。

    这里刻意**不加尾随换行**，好让"无变化"的比对与 validate_export.write_json
    写出来的文件字节完全一致——本脚本写一次、裁判再写一次，文件不该有差别。
    """
    return json.dumps(obj, ensure_ascii=False, indent=1)


def merge_decisions(existing, new_issues, new_archive, run_id):
    """合并：Excel 没提到的条目原样保留，顶层多出来的键也原样保留。

    返回 (合并后的文档, 变更清单, 归档条目里真正变了的条数)。
    变更清单里的每一项是一条人话说明。
    """
    doc = dict(existing)                    # 顶层键全部保留
    changes = []
    arc_changed = 0

    if new_issues:
        old_list = doc.get("issue_decisions")
        if not isinstance(old_list, list):
            old_list = []
        index = {}
        merged = []
        for item in old_list:
            if isinstance(item, dict) and item.get("issue_id"):
                index[to_display(item["issue_id"])] = item
            merged.append(item)
        for entry in new_issues:
            iid = entry["issue_id"]
            old = index.get(iid)
            if old is None:
                fresh = {"issue_id": iid, "decision": entry["decision"]}
                if entry.get("note"):
                    fresh["note"] = entry["note"]
                merged.append(fresh)
                changes.append("新增问题决定 %s：%s" % (iid, entry["decision"]))
                continue
            if to_display(old.get("decision")) != entry["decision"]:
                changes.append("修改问题决定 %s：%s -> %s"
                               % (iid, to_display(old.get("decision")) or "（空）", entry["decision"]))
                old["decision"] = entry["decision"]
            if entry.get("note"):
                if to_display(old.get("note")) != entry["note"]:
                    changes.append("修改备注 %s：%s -> %s"
                                   % (iid, to_display(old.get("note")) or "（空）", entry["note"]))
                    old["note"] = entry["note"]
            elif "note" not in old:
                old["note"] = ""
        doc["issue_decisions"] = merged

    if new_archive:
        old_list = doc.get("archive_decisions")
        if not isinstance(old_list, list):
            old_list = []
        index = {}
        merged = []
        for item in old_list:
            if isinstance(item, dict) and item.get("image_id"):
                index[to_display(item["image_id"])] = item
            merged.append(item)
        for entry in new_archive:
            iid = entry["image_id"]
            old = index.get(iid)
            if old is None:
                fresh = {"image_id": iid, "confirmed": entry["confirmed"]}
                if entry.get("suggested_name"):
                    fresh["suggested_name"] = entry["suggested_name"]
                merged.append(fresh)
                changes.append("新增归档决定 %s：%s"
                               % (iid, "归档" if entry["confirmed"] else "不归档"))
                arc_changed += 1
                continue
            touched = False
            if old.get("confirmed") is not entry["confirmed"]:
                changes.append("修改归档决定 %s：%s -> %s"
                               % (iid, old.get("confirmed"), entry["confirmed"]))
                old["confirmed"] = entry["confirmed"]
                touched = True
            if entry.get("suggested_name"):
                if to_display(old.get("suggested_name")) != entry["suggested_name"]:
                    changes.append("修改归档文件名 %s：%s -> %s"
                                   % (iid, to_display(old.get("suggested_name")) or "（空）",
                                      entry["suggested_name"]))
                    old["suggested_name"] = entry["suggested_name"]
                    touched = True
            if touched:
                arc_changed += 1
        doc["archive_decisions"] = merged

    # run_id：已有的一律不动（它是历史留痕）；没有才按目录补上。
    if not to_display(doc.get("run_id")) and run_id:
        doc["run_id"] = run_id
        changes.append("补上 run_id：%s" % run_id)
    return doc, changes, arc_changed


def write_atomic(path, text):
    """先写 .tmp 再 os.replace：绝不产生半截文件。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------- 归档决定
def parse_yes_no(raw):
    text = to_display(raw).lower()
    if text in YES_WORDS:
        return True
    if text in NO_WORDS:
        return False
    return None


def build_archive_entries(rows, plan_by_image):
    """把「文件归档清单」的行变成 archive_decisions 条目。

    "新名字/建议文件名"这一列的语义要小心：审核表里本来就有「建议文件名」列，
    里面是脚本按契约生成的名字。若把它当成"用户改了名字"，则每一行都会被
    报成"改名"，报告立刻变成噪音。所以这里拿 archive_plan.json 里的
    suggested_name 做基准：**和它一样 = 用户没动**，只有真的不一样才算改名。

    返回 (条目列表, 拒绝列表, 改名说明列表)。
    """
    entries, rejected, renamed = [], [], []
    for rowno, image_id, raw_conf, new_name in rows:
        confirmed = parse_yes_no(raw_conf)
        if confirmed is None:
            rejected.append((rowno, image_id,
                             "「是否归档(是/否)」填的是「%s」，认不出是还是否。"
                             "请填 是 或 否（留空 = 不表态，不会被猜成是）" % raw_conf))
            continue
        entry = {"image_id": image_id, "confirmed": confirmed}
        baseline = to_display((plan_by_image.get(image_id) or {}).get("suggested_name"))
        if new_name and new_name != baseline:
            fixed = ve.sanitize_name(new_name, max_len=80)
            entry["suggested_name"] = fixed
            if not baseline:
                renamed.append((image_id, new_name, fixed,
                                "第 %d 行" % rowno, "没有归档计划可作基准，按用户给的名字处理"))
            elif fixed != new_name:
                renamed.append((image_id, new_name, fixed,
                                "第 %d 行" % rowno, "含文件名非法字符或超长，已清洗"))
            else:
                renamed.append((image_id, new_name, fixed,
                                "第 %d 行" % rowno, "与脚本建议的不同，按用户的改名"))
        entries.append(entry)
    return entries, rejected, renamed


# ---------------------------------------------------------------- 主流程
def run_import(run_dir, dry_run=False, force=False):
    run_dir = os.path.abspath(run_dir)
    if not os.path.isdir(run_dir):
        die("目录不存在：%s" % run_dir)

    print("== 导入审核决定：%s ==" % run_dir)
    review_path = os.path.join(run_dir, "task_review.xlsx")
    decisions_path = os.path.join(run_dir, "decisions.json")

    root = os.path.dirname(os.path.dirname(run_dir))
    if os.path.basename(root) != "runs":
        root = os.getcwd()
    run = ve.safe_read(os.path.join(run_dir, "raw.json"), {}) or {}
    schemas = ve.load_schemas(root) if os.path.isdir(os.path.join(root, "schemas")) \
        else {"*": ve.normalize_schema(ve.DEFAULT_SCHEMA)}
    plan_doc = ve.safe_read(os.path.join(run_dir, "archive_plan.json"), {}) or {}
    plan_by_image = {}
    for item in (plan_doc.get("plan") or []):
        if isinstance(item, dict) and item.get("image_id"):
            plan_by_image[to_display(item["image_id"])] = item

    parsed, err = read_workbook(review_path)
    if err:
        die(err + ("\n       （--dry-run 也会走同一条读表路径，所以这不是写权限的问题）"
                   if dry_run else ""), 2)
    for note in parsed["notes"]:
        log("[提示] " + note)

    issues = []
    for issue in ((ve.safe_read(os.path.join(run_dir, "issues.json"), {}) or {}).get("issues") or []):
        if isinstance(issue, dict) and issue.get("issue_id"):
            issues.append(issue)
    issue_by_id = {}
    for issue in issues:
        issue_by_id.setdefault(to_display(issue["issue_id"]), issue)
    # 账本里有、但已 resolved 的问题也要能对上号（人工决定就是为它们填的）
    env = build_env(run, schemas, issues)

    existing, existing_err, existing_block = load_existing(decisions_path)
    if existing_block:
        die(existing_block + "\n"
            "       本次**什么都没有写**（把读不到的文件当空文档合并，等于用它覆盖掉"
            "你之前填过的全部决定）。\n"
            "       请关闭正在打开它的程序（编辑器 / 同步盘 / 杀毒扫描）后重跑。", 2)
    if existing_err:
        log("[注意] " + existing_err)
    old_issue_by_id = {}
    for item in (existing.get("issue_decisions") or []):
        if isinstance(item, dict) and to_display(item.get("issue_id")):
            old_issue_by_id.setdefault(to_display(item["issue_id"]), item)

    new_issues, rejected, unchanged, empty_id = [], [], [], []
    for rowno, issue_id, decision, note in parsed["issues"]:
        if not issue_id:
            empty_id.append(rowno)
            continue
        issue = issue_by_id.get(issue_id)
        if issue is None:
            rejected.append((rowno, issue_id,
                             "问题编号在 issues.json 里找不到（可能是上一轮的旧编号，"
                             "或编号被手改了）。请重跑 python validate_export.py 生成新审核表"))
            continue
        ok, info, reason = validate_decision(decision, issue, env)
        if not ok:
            rejected.append((rowno, issue_id, reason))
            continue
        old = old_issue_by_id.get(issue_id)
        if old is not None and to_display(old.get("decision")) == decision \
                and (not note or to_display(old.get("note")) == note):
            unchanged.append((issue_id, decision))
        new_issues.append({"issue_id": issue_id, "decision": decision, "note": note})

    archive_entries, arc_rejected, renamed = build_archive_entries(
        parsed["archive"], plan_by_image)
    rejected.extend(arc_rejected)

    doc, changes, arc_changed = merge_decisions(existing, new_issues, archive_entries,
                                                to_display(run.get("run_id"))
                                                or os.path.basename(run_dir))

    # ---- 报告 -------------------------------------------------------------
    imported = len(new_issues) - len(unchanged)
    print("")
    print("  已导入：问题决定 %d 条 / 归档决定 %d 条" % (len(new_issues), len(archive_entries)))
    print("  其中真正改动：问题决定 %d 条 / 归档决定 %d 条" % (imported, arc_changed))
    print("  无变化（Excel 与 decisions.json 一致）：问题决定 %d 条 / 归档决定 %d 条"
          % (len(unchanged), len(archive_entries) - arc_changed))
    print("  被拒绝：%d 条；审核表里没填决定的整行跳过" % len(rejected))
    print("  合并后：问题决定 %d 条 / 归档决定 %d 条"
          % (len(doc.get("issue_decisions") or []), len(doc.get("archive_decisions") or [])))
    if empty_id:
        print("  跳过：第 %s 行有决定却没填问题编号，对不上号" % "、".join(str(r) for r in empty_id))
    for image_id, before, after, where, why in renamed:
        print("  [归档改名] %s（%s）：「%s」->「%s」—— %s" % (image_id, where, before, after, why))
    if rejected:
        print("")
        print("  ---- 被拒绝的行（%d 条，均未写入 decisions.json）----" % len(rejected))
        for rowno, issue_id, reason in rejected:
            print("   第 %d 行  %s：%s" % (rowno, issue_id or "（没问题编号）", reason))
        print("  " + accepted_forms_text())
        print("  （这些行只要还留在 Excel 里，下次跑 validate_export.py 会提示"
              "「有决定尚未整理进 decisions.json」——那说的是同一批被拒的行，不是新问题。）")

    # 只有"确实什么都不用写"或"填了的决定一条都没能落地"才提前收工。
    # 注意**不能**用"问题决定为 0"当条件：Excel 的归档页也是决定来源，
    # 问题清单全填错、归档清单全填对时，归档决定照样得写进去。
    filled_total = len(parsed["issues"]) + len(parsed["archive"])
    usable_total = len(new_issues) + len(archive_entries)
    if not usable_total:
        print("")
        if rejected:
            print("  Excel 里填了 %d 条决定，但**每一条都被拒绝**——没有写入任何内容，退出码 2。"
                  % len(rejected))
            print("  请按上面的原因修好 Excel 后重跑。")
            return 2
        print("  审核表里没有已填的决定，也没有需要写入的归档决定 —— 无需改动，退出码 0。")
        return 0
    if rejected and len(rejected) >= filled_total and not force:
        print("")
        print("  [拒绝] Excel 里填了 %d 条决定，全都被拒绝 —— 没有写入任何内容，退出码 2。"
              % filled_total)
        print("  按上面的原因修好 Excel 后重跑；确认要带着这些拒绝继续可加 --force。")
        return 2

    if changes:
        print("")
        print("  ---- 本次变更 ----")
        for line in changes:
            print("    - " + line)

    if rejected:
        print("")
        print("  [注意] 有 %d 条决定被拒绝、未写入（其余已写入）。%s"
              % (len(rejected), "（--force 已生效，带着这些拒绝继续）" if force
                 else "若确认要带着这些拒绝继续，可加 --force。"))

    old_bytes = None
    if os.path.exists(decisions_path):
        try:
            with open(decisions_path, "rb") as f:
                old_bytes = f.read()
        except PermissionError:
            die("读不了已有的 decisions.json（被占用或无权限）。\n"
                "       无法确认「有没有变化」，为安全起见本次不写任何内容。\n"
                "       请关闭正在打开它的程序后重跑。", 2)
        except OSError as exc:
            die("读不了已有的 decisions.json：%s（本次不写任何内容）" % exc, 2)
    new_text = dumps_like_referee(doc)
    same = False
    if old_bytes is not None:
        try:
            same = old_bytes.decode("utf-8-sig") == new_text
        except UnicodeDecodeError:
            same = False

    if same:
        print("")
        print("  无变化：%s 已是最新，未重写（mtime 不变）。" % os.path.basename(decisions_path))
        return 0

    if dry_run:
        print("")
        print("  --dry-run：以上是将会写入的内容，本次**没有写任何文件**。")
        return 0

    # 写之前先探一次可写性：被 Excel 之类独占时给出人话，而不是 traceback
    if os.path.exists(decisions_path):
        try:
            with open(decisions_path, "a", encoding="utf-8"):
                pass
        except PermissionError:
            die("写不了 decisions.json：文件被占用或无写权限。\n"
                "       请先关闭正在打开它的程序（Excel / 编辑器）再重跑。\n"
                "       本次没有写入任何内容。", 2)
        except OSError as exc:
            die("写不了 decisions.json：%s（本次没有写入任何内容）" % exc, 2)
    try:
        write_atomic(decisions_path, new_text)
    except PermissionError:
        die("写不了 decisions.json：目标被占用或无写权限（本次没有写入任何内容，"
            "临时文件可能残留为 decisions.json.tmp，可手动删除）", 2)
    except OSError as exc:
        die("写不了 decisions.json：%s" % exc, 2)

    print("")
    print("  已写入：%s（UTF-8 无 BOM，LF 换行）" % decisions_path)
    print("  下一步：python validate_export.py \"%s\"   （加 --archive 会同时归档原图）"
          % os.path.relpath(run_dir, root))
    print("== 完成 ==")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="把用户填好的 task_review.xlsx 导入成 decisions.json（合并、不覆盖）")
    parser.add_argument("run_dir", help="任务目录，例如 runs/20260910_002")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将要写入的内容，不写任何文件")
    parser.add_argument("--force", action="store_true",
                        help="本次填的决定一条都没能落地时，跳过「退出码 2」这条检查"
                             "（该拒的仍然拒，不会写进任何一条坏决定）")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return run_import(args.run_dir, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
