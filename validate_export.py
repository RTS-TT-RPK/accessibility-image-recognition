# -*- coding: utf-8 -*-
# validate_export.py -- 本系统唯一的裁判脚本（AI 永不自动修改此文件）
#
# 职责：读 raw.json -> 通用不变量校验 -> 类型转换 -> 组分合计校验(整数运算)
#       -> 生成问题账本 issues.json -> 生成 final.xlsx 与 task_review.xlsx
#       -> 生成/执行归档计划 archive_plan.json
#
# 命令行：
#   python validate_export.py <run_dir>                只校验并生成产物(不动原图)
#   python validate_export.py <run_dir> --archive      校验通过后执行归档(移动原图)
#   python validate_export.py <run_dir> --init-issues  只生成/刷新 issues.json
#
import sys, os, io, json, re, hashlib, datetime, shutil, argparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCALE = 100          # 百分比以 0.01% 为整数单位（禁用浮点直加，见定稿第 11 节硬规则 1）
FULL = 100 * SCALE   # 100.00% == 10000

STATUS_OK, STATUS_PENDING, STATUS_BLOCKED = "ok", "pending", "blocked"

# --- 业务口径开关（改动这里即可调整行为，不必动逻辑） ---------------------------
# 水组分是否写入「成分事实」表。注意与合计规则无关：
# 无论此开关如何，"水不计入合计"的规则都不变。默认 True——水含量是有用的业务数据，
# 若只从合计里排除、却不写进事实表，等于交付的 Excel 里永久丢失水分数据。
WATER_IN_FACTS = True
# row_role 缺失或不合法时，是否阻塞该行（不让它进事实表）。
# 默认 True：按"不猜"原则，身份不明的行不能当数据行用（合计行误入事实表是红线问题）。
# 若实际使用中模型经常漏写 row_role 导致大量阻塞，可改为 False（只记问题、不阻塞）。
ROW_ROLE_MISSING_BLOCKS = True

ROW_ROLES = ("data", "summary", "header", "note")

# 合法的 row_role 值；之外的（含缺失）一律记为 ROW_ROLE_INVALID

DEFAULT_ALIASES = {
    "water": ["水", "水分", "含水量", "水份", "含水"],
    "misc":  ["杂", "杂质", "杂分", "其他", "残余", "不纯物"],
}

# --- 版式契约（schema）--------------------------------------------------------
# 这套脚本是**通用**的表格图片转录裁判：表格长什么样、有哪些列、计量项怎么归类、
# 合计规则是什么、归档怎么命名，全部由 schemas/*.json 描述。
# 找不到匹配的 schema 时退回下面这个内置默认——它的行为与原来的"化工厂单据"完全一致，
# 保证历史任务不会因为通用化改造而变样。
DEFAULT_SCHEMA = {
    "template": "_builtin_default",
    "sheet_label": "*",
    "row_unit": "一行 = 一个业务对象",
    "columns": [],
    "measurements": {
        "enabled": True,
        "unit": "%",
        # 每个类别怎么参与合计：
        #   "never"      = 永不计入合计（如"水"）
        #   "if_present" = 有值才计入、空白留白不提示（如"杂"）
        #   "always"     = 必须计入，解析不了就报问题（默认）
        "categories": {"water": "never", "misc": "if_present"},
        "default_category": "always",
        "sum_max": 100.0,
    },
    "vocabulary": {},          # 留空 = 用 DEFAULT_ALIASES
    "fact_keys": {
        "record_date": "日期",
        "location": "位置",
        "batch_id": "批次",
        "container_id": "桶号",
        "material_name": "物料",
        "supplier": "厂商",
    },
    "archive": {"name_pattern": None},
}

COLUMN_TYPES = ("text", "date", "number", "percent", "identifier")

# 「成分事实」表的列序：已知列按这个顺序放，schema 带出来的新列排在其后。
# 保持这个顺序是为了兼容既有下游（老表的列位不变，新列一律追加在末尾）。
FACT_COLUMN_ORDER = ["record_date", "source_type", "location", "batch_id", "container_id",
                     "material_name", "supplier", "component_standard_name",
                     "component_original_name", "numeric_value", "unit", "raw_value",
                     "source_run_id", "source_row_id", "category"]

ILLEGAL = r'[\\/:*?"<>|]'


def die(msg):
    print("[错误] " + msg)
    sys.exit(2)


def log(msg):
    print("  " + msg)


def read_json(path, default=None):
    if not os.path.exists(path):
        if default is not None:
            return default
        die("缺少文件: " + path)
    with io.open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def sha256_of(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# 词表分类的键名归一：aliases.json 里可能写成 water / 水类 / _已确认_水类 等多种形式，
# 统一映射到 water / misc。否则用户按文件里的中文键加词，会"加了却完全不生效"（静默无效）。
ALIAS_CATEGORY_HINTS = (
    ("water", ("water", "水")),
    ("misc", ("misc", "杂")),
)


def canonical_alias_key(key):
    """把 aliases.json 的键归一成 water / misc；认不出来的返回 None（跳过）。"""
    text = str(key).strip().lstrip("_")
    if not text:
        return None
    low = text.lower()
    for canon, hints in ALIAS_CATEGORY_HINTS:
        for hint in hints:
            if hint in low or hint in text:
                return canon
    return None


def load_aliases(root):
    """加载成分词表。返回 (词表, 从 aliases.json 实际读到的词列表)。

    以 DEFAULT_ALIASES 为底，再并入 aliases.json 里用户拍板过的词。
    第二个返回值用于打印，能真实反映"文件里到底贡献了几个词"——
    过去它返回键名个数（含 _说明 这类元数据），打印"已注册 6 条"其实一个词都没算清。
    """
    if not os.path.exists(os.path.join(root, "aliases.json")):
        return dict((k, list(v)) for k, v in DEFAULT_ALIASES.items()), []
    data = read_json(os.path.join(root, "aliases.json"), {})
    merged = dict((k, list(v)) for k, v in DEFAULT_ALIASES.items())
    loaded = []
    for key, words in data.items():
        if not isinstance(words, (list, tuple)):
            continue                      # "其它": {} 之类的元数据，跳过
        canon = canonical_alias_key(key)
        if canon is None:
            continue
        merged.setdefault(canon, [])
        for w in words:
            w = to_display(w)
            if w and w not in merged[canon]:
                merged[canon].append(w)
                loaded.append(w)
    return merged, loaded


def value_matches_type(text, ctype):
    """按 schema 声明的列类型判断一个值是否合格（通用能力，与具体业务无关）。"""
    t = to_display(text)
    if not t:
        return True                     # 空值由"必填"规则去管，这里不重复报
    if ctype == "date":
        return bool(re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", t))
    if ctype == "percent":
        return parse_percent(t) is not None
    if ctype == "number":
        return re.match(r"^[+-]?\d+(\.\d+)?$", t.replace(",", "").replace("，", "")) is not None
    return True                          # text / identifier 不挑


def _deep_merge(base, extra):
    """把 extra 合并进 base 的副本（dict 递归合并，其它类型直接覆盖）。"""
    out = dict(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _merge_words(merged, mapping):
    """把一份 {类别: [词...]} 并进 merged（去重、保留已有顺序）。"""
    for k, words in (mapping or {}).items():
        if not isinstance(words, (list, tuple)):
            continue
        canon = canonical_alias_key(k) or to_display(k)
        if not canon:
            continue
        merged.setdefault(canon, [])
        for w in words:
            w = to_display(w)
            if w and w not in merged[canon]:
                merged[canon].append(w)
    return merged


def normalize_schema(data, extra_vocab=None):
    """把 schema 补齐成完整结构（缺的字段用内置默认填），并算好派生值。

    extra_vocab 用来并入"用户拍板过的词"（aliases.json）——
    那些词对所有表型都生效，不能因为换成 schema 驱动就被丢掉。
    """
    sch = _deep_merge(DEFAULT_SCHEMA, data)
    sch["sheet_label"] = to_display(sch.get("sheet_label")) or "*"
    merged = dict((k, list(v)) for k, v in DEFAULT_ALIASES.items())
    _merge_words(merged, sch.get("vocabulary") or {})
    _merge_words(merged, extra_vocab or {})
    sch["vocabulary"] = merged

    meas = sch["measurements"]
    try:
        meas["_sum_max_units"] = int(round(float(meas.get("sum_max", 100.0)) * SCALE))
    except (TypeError, ValueError):
        meas["_sum_max_units"] = FULL
    # value 字段是不是"换算后的小数"（= 原文/100）。
    # 百分比表型是（89.6% <-> 0.896）；其它单位（小时/公斤…）不是（14 <-> 14）。
    # 这个区别决定了 raw 与 value 该怎么比对，不区分就会误报。
    if "value_is_fraction" in meas:
        meas["_fraction"] = bool(meas["value_is_fraction"])
    else:
        meas["_fraction"] = to_display(meas.get("unit", "%")).strip() in ("%", "％")
    return sch


def load_schemas(root):
    """读取 schemas/*.json，返回 {工作表名: schema}，并保证有 "*" 兜底。

    aliases.json 里用户拍板过的词会并进每一份 schema 的词表——
    它是"全局生效"的，不属于某个表型。
    """
    user_vocab, _loaded = load_aliases(root)
    out = {}
    schemas_dir = os.path.join(root, "schemas")
    if os.path.isdir(schemas_dir):
        for fn in sorted(os.listdir(schemas_dir)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(schemas_dir, fn)
            try:
                with io.open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as exc:
                print("  [提示] schema 读不了，已跳过：%s（%s）" % (fn, exc))
                continue
            if not isinstance(data, dict) or not data.get("template"):
                continue
            label = to_display(data.get("sheet_label")) or "*"
            out[label] = normalize_schema(data, user_vocab)
    out.setdefault("*", normalize_schema(DEFAULT_SCHEMA, user_vocab))
    return out


def schema_for(schemas, sheet):
    """按工作表名挑 schema；没有专属的就用 "*" 兜底。"""
    s = to_display(sheet)
    if s and s in schemas:
        return schemas[s]
    return schemas.get("*") or normalize_schema(DEFAULT_SCHEMA)


def schema_columns(sch):
    """schema 里声明的列名（按声明顺序）。"""
    out = []
    for col in (sch.get("columns") or []):
        if isinstance(col, dict) and col.get("name"):
            out.append(to_display(col["name"]))
    return out


def order_field_columns(detail, schemas):
    """明细表里数据列的先后顺序。

    按 schema 声明的列序排（日期、位置、物料……），schema 没声明的排在后面。
    过去直接 sorted()，中文列名按 Unicode 码点排，业务顺序会被打乱成
    「位置、批次、日期、桶号、物料」这种读起来别扭的样子。
    """
    declared = []
    for sch in (schemas or {}).values():
        for c in schema_columns(sch):
            if c not in declared:
                declared.append(c)
    present = set()
    for rec in detail:
        present.update(rec["fields"].keys())
    return [c for c in declared if c in present] + sorted(present - set(declared))


def classify_component(name, aliases):
    # 返回 (类别, 是否已注册)
    # 注意：只有"疑似同类词"才算未注册（例如含水率/杂质A），
    # 普通成分名（IPAC、DMF、甲醇……）不在此列，避免问题清单被淹没。
    if not name:
        return "comp", True
    for key, words in aliases.items():
        for w in words:
            if not w:
                continue
            if w == name:
                return key, True
            if len(w) >= 2 and (w in name or name in w):
                return key, True
    return "comp", True


def is_suspicious_unregistered(name, aliases, known_terms):
    """是否值得提示"新词"：不在已知名单里，且与已注册同类词有一定相似度。"""
    if not name or name in known_terms:
        return False
    for words in aliases.values():
        for w in words:
            if not w or len(w) < 2:
                continue
            overlap = len(set(w) & set(name))
            if overlap >= max(2, len(w) - 1):
                return True
    return False


def parse_percent(value):
    """百分比文本 -> 以 0.01% 为单位的整数。解析失败返回 None。

    约定：**裸数字按"百分数"解释**（89.6 -> 89.60%，即 8960）。
    支持：89.6% / 89.6 / 全角％ / 1,234.5% / 带空格 / 1e-05（科学计数法）。
    注意：本函数**不是**给"换算后的小数"用的——那要用 decimal_to_units()。
    """
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    neg = text.startswith("-") or text.startswith("－")
    if neg:
        text = text[1:]
    text = (text.replace("％", "%").replace("%", "")
                .replace("，", "").replace(",", "").replace(" ", "").strip())
    number = None
    try:
        number = float(text)                 # 先按原样解析，支持 1e-05
    except ValueError:
        cleaned = re.sub(r"[^0-9.\-]", "", text)   # 再退回宽容解析（如 约90）
        if cleaned in ("", ".", "-"):
            return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
    return int(round(number * SCALE)) * (-1 if neg else 1)


def to_display(value):
    if value is None:
        return ""
    return str(value).strip()


def decimal_to_units(value):
    """把"换算后的小数"转成以 0.01% 为单位的整数：0.896 -> 8960（即 89.60%）。
    与 parse_percent 同一套刻度，用于校验 raw_value 与 value 是否自洽。
    若字符串自带 % 号（模型有时会把百分比直接写进 value），按百分比处理。"""
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    if "%" in text or "％" in text:
        return parse_percent(text)
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace("，", ""))
    if cleaned in ("", ".", "-"):
        return None
    try:
        return int(round(float(cleaned) * FULL))
    except ValueError:
        return None


def fmt_units(units):
    """把 0.01% 单位的整数还原成人看的百分比文本。"""
    if units is None:
        return "?"
    return "%.2f%%" % (units / float(SCALE))


def find_template(row):
    return row.get("template") or row.get("sheet") or ""


def load_known_terms(root):
    """读取所有已注册 schema 里的字段名，用于判断"新词"。"""
    known = set()
    schemas_dir = os.path.join(root, "schemas")
    if os.path.isdir(schemas_dir):
        for fn in os.listdir(schemas_dir):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            try:
                data = read_json(os.path.join(schemas_dir, fn), {})
            except SystemExit:
                continue
            for col in data.get("columns", []):
                if col.get("name"):
                    known.add(str(col["name"]))
            for term in data.get("known_terms", []):
                known.add(str(term))
    return known


ITYPE_SHORT = {
    "OCR_UNCERTAIN": "OCR",
    "TYPE_AMBIGUITY": "TYPE",
    "MISSING_REQUIRED_FIELD": "MISS",
    "COMPONENT_SUM_OVER_100": "SUM",
    "UNREGISTERED_TERM": "TERM",
    "DUPLICATE": "DUP",
    "DATE_AMBIGUITY": "DATE",
    "VALUE_MISMATCH": "VAL",
    "ROW_ROLE_INVALID": "ROLE",
    "INPUT_SANITY": "SAN",
    "TYPE_MISMATCH": "COLT",
    "SHEET_UNKNOWN": "SHEET",
}

# 这些类型**不阻塞数据行**：它们说的是"值得看一眼"，不是"这行数据不能要"。
# 其余所有类型（含 info 级）都会让所在行不进事实表，直到人工处理。
NON_BLOCKING_TYPES = ("DUPLICATE", "UNREGISTERED_TERM", "INPUT_SANITY", "SHEET_UNKNOWN")


def issue_id_for(itype, key, seen):
    """稳定编号：与出现顺序无关，重跑后同一问题的编号不变，人工决定才不会错位。"""
    short = ITYPE_SHORT.get(itype, itype[:4].upper())
    base = "ISS-%s-%s" % (short, key)
    n = seen.get(base, 0) + 1
    seen[base] = n
    return base if n == 1 else "%s-%d" % (base, n)


APPROVE_WORDS = {"通过", "确认", "确认无误", "没问题", "无问题", "照录", "keep", "ok", "OK"}


def is_approve(decision):
    return (decision or "").strip() in APPROVE_WORDS


def percent_to_decimal_text(value):
    """把人工填写的百分比文本转成小数字符串（0.5% -> 0.005，30 -> 0.3）。非数值原样返回。"""
    if value is None:
        return ""
    text = str(value).strip()
    import decimal as _dec
    try:
        if "%" in text or "％" in text:
            num = text.replace("％", "%").replace("%", "").strip()
            return str((_dec.Decimal(num) / 100).normalize())
        return str((_dec.Decimal(text) / 100).normalize())
    except Exception:
        return text


def apply_overrides_to_row(row, overrides, add_if_missing=False):
    comps = row.get("components")
    if comps is None:
        comps = []
        row["components"] = comps
    for key, val in overrides.items():
        matched = False
        for comp in comps:
            name = to_display(comp.get("name"))
            if name == key or (len(key) >= 2 and key in name):
                comp["raw_value"] = val
                comp["value"] = percent_to_decimal_text(val)
                comp["from_human"] = True
                matched = True
                break
        if not matched and add_if_missing:
            comps.append({"name": key, "raw_value": val,
                          "value": percent_to_decimal_text(val), "unit": "%",
                          "from_human": True})


# 审核表"员工决定"下拉框里的标签。用户若直接选了它、又原样留在 decisions.json 里，
# 必须还原成真实候选值，否则「沿用候选A」这四个字会被当成数据写进台账。
CANDIDATE_LABELS = {
    "沿用候选A": 0, "沿用候选a": 0, "候选A": 0,
    "沿用候选B": 1, "沿用候选b": 1, "候选B": 1,
}


def apply_decisions(run, decisions_by_issue, issues):
    """把人工决定落到数据上：形如 名=值 的写法直接改对应成分；通过类决定不改数据。"""
    accepted = set()
    applied = 0
    for issue in issues:
        decision = (decisions_by_issue.get(issue["issue_id"]) or {}).get("decision")
        if not decision:
            continue
        if is_approve(decision):
            accepted.add(issue["issue_id"])
            continue

        text = str(decision).strip()
        cands = issue.get("candidates") or []
        if text in CANDIDATE_LABELS:
            idx = CANDIDATE_LABELS[text]
            if idx < len(cands):
                text = to_display(cands[idx])
            else:
                continue            # 没有对应候选值，这条决定只能忽略（该行保持阻塞）
        elif text == "见备注":
            continue                # 只看备注、不改数据（该行保持阻塞，直到给出真实值）

        # row_role 类问题：决定内容就是它应有的值，要写到行上而不是 fields 里
        if issue["type"] == "ROW_ROLE_INVALID":
            if text in ROW_ROLES:
                for row in run.get("rows", []):
                    if row.get("row_id") in issue["target_ids"]:
                        row["row_role"] = text
                applied += 1
            continue

        overrides = {}
        for part in re.split(r"[;；,，\n]+", text):
            m = re.match(r"^\s*(.+?)\s*[=:：]\s*(.+?)\s*$", part)
            if m:
                overrides[m.group(1)] = m.group(2)
        if not overrides:
            field = issue.get("field") or ""
            if field:
                overrides[field] = text
        if overrides:
            as_component = issue["type"] in ("OCR_UNCERTAIN", "TYPE_AMBIGUITY")
            is_field_issue = issue["type"] in ("MISSING_REQUIRED_FIELD", "DATE_AMBIGUITY")
            for row in run.get("rows", []):
                if row.get("row_id") not in issue["target_ids"]:
                    continue
                fields = row.get("fields")
                if not isinstance(fields, dict):
                    fields = {}
                    row["fields"] = fields
                apply_overrides_to_row(row, overrides, add_if_missing=as_component)
                for k, v in overrides.items():
                    # 只有"确实是本行的字段"或"问题本身就是字段类"时才写进 fields。
                    # 否则（例如决定里写 DMF=0）成分名会变成一个**新字段**，
                    # 最终 Excel 明细表就凭空多出 DMF / 乙酸 这样的假列。
                    if is_field_issue or k in fields:
                        fields[k] = v
                if issue["type"] == "OCR_UNCERTAIN":
                    keep = []
                    for item in row.get("uncertain", []):
                        target = issue.get("field") or ""
                        if target and (item.get("field") == target or target in str(item.get("field", ""))):
                            continue
                        keep.append(item)
                    row["uncertain"] = keep
            applied += 1
    return accepted, applied


def normalize_run(run):
    """把大模型产出的 raw.json 规范化成脚本能安全处理的结构。

    为什么需要：raw.json 是模型生成的，出现 `fields: null`、`components: null`、
    `components: "字符串"`、`uncertain: {...}` 这类畸形很常见。
    过去这些会直接 traceback，任务半途中断、落盘状态不一致。

    原则：**既不崩，也不静默** —— 能救的救回来，救不回来的记进 run["_sanity"]，
    由 build_issues 统一生成问题清单。
    """
    notes = []
    if not isinstance(run, dict):
        return {"run_id": "", "rows": [], "images": [], "_sanity": ["raw.json 顶层不是对象"]}, \
               ["raw.json 顶层不是对象"]

    rows = run.get("rows")
    if not isinstance(rows, list):
        if rows is not None:
            notes.append("rows 不是数组，已按空处理")
        rows = []
    clean_rows = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            notes.append("第 %d 行不是对象，已跳过" % (idx + 1))
            continue
        rid = to_display(row.get("row_id"))
        if not rid:
            rid = "R%03d" % (idx + 1)
            row["row_id"] = rid
            notes.append("第 %d 行缺 row_id，已补为 %s" % (idx + 1, rid))
        if not isinstance(row.get("fields"), dict):
            if row.get("fields") is not None:
                notes.append("%s 的 fields 不是对象，已按空处理" % rid)
            row["fields"] = {}
        for key in ("components", "uncertain", "required_fields"):
            val = row.get(key)
            if val is None:
                row[key] = []
            elif not isinstance(val, list):
                notes.append("%s 的 %s 不是数组，已按空处理" % (rid, key))
                row[key] = []
        row["components"] = [c for c in row["components"] if isinstance(c, dict)]
        row["uncertain"] = [u for u in row["uncertain"] if isinstance(u, dict)]
        if not isinstance(row.get("row_role"), (str, type(None))):
            notes.append("%s 的 row_role 不是文本，已转为文本" % rid)
            row["row_role"] = to_display(row.get("row_role"))
        clean_rows.append(row)
    run["rows"] = clean_rows

    images = run.get("images")
    if not isinstance(images, list):
        if images is not None:
            notes.append("images 不是数组，已按空处理")
        images = []
    run["images"] = [im for im in images if isinstance(im, dict)]

    # 去重：同一 raw.json 里 images 重复时归档会撞名，这里只提示不影响流程
    run["_sanity"] = notes
    return run, notes


def build_issues(run, schemas, unregistered_seen, known_terms=None):
    issues = []
    seen_ids = {}
    known_terms = known_terms or set()

    def add(itype, severity, row, desc, candidates=None, field=None):
        key = "%s-%s" % (row.get("row_id", ""), field or "")
        issues.append({
            "issue_id": issue_id_for(itype, key, seen_ids),
            "run_id": run.get("run_id", ""),
            "type": itype,
            "severity": severity,
            "image": row.get("image_id", ""),
            "sheet": row.get("sheet", ""),
            "target_ids": [row.get("row_id", "")],
            "object": row.get("object_label", ""),
            "field": field or "",
            "description": desc,
            "candidates": candidates or [],
            "machine_status": STATUS_PENDING,
            "human_decision": None,
        })

    # 0) 输入体检：normalize_run 收集到的 raw.json 畸形之处，一律进问题清单（不静默）
    for note in run.get("_sanity", []):
        issues.append({
            "issue_id": issue_id_for("INPUT_SANITY", note, seen_ids),
            "run_id": run.get("run_id", ""),
            "type": "INPUT_SANITY",
            "severity": "warn",
            "image": "",
            "sheet": "",
            "target_ids": [],
            "object": "",
            "field": "",
            "description": "raw.json 结构异常：%s" % note,
            "candidates": [],
            "machine_status": STATUS_PENDING,
            "human_decision": None,
        })

    # 已注册的专属契约名（不含 "*" 兜底）。批量转录时用它盯"表名漂移"：
    # 同一批图里忽然冒出一个没注册的表名，多半是混进了别的表、
    # 或者模型把表名写得跟样板不一致——这类漂移必须让人看见。
    named_sheets = set(k for k in schemas if k != "*")
    unknown_sheets = {}

    for row in run.get("rows", []):
        role = to_display(row.get("row_role"))
        # row_role 必须显式标注且取值合法。绝不能"缺省就当数据行"：
        # 那会让合计/表头/备注行悄悄进事实表（红线），
        # 也会让写成 row_role="total" 这种错值的真实数据行悄悄消失。
        if role not in ROW_ROLES:
            reason = "缺失" if not role else "取值不合法「%s」" % role
            add("ROW_ROLE_INVALID", "warn" if ROW_ROLE_MISSING_BLOCKS else "info", row,
                "row_role %s（合法值：%s）：无法判断这是数据行还是合计/表头行"
                % (reason, "/".join(ROW_ROLES)),
                None, "row_role")
        if role and role != "data":
            continue
        obj = row.get("object_label") or row.get("row_id", "")

        # 本行按工作表名挑 schema：词表、合计规则、列定义都可能随表型不同
        sch = schema_for(schemas, row.get("sheet"))
        aliases = sch["vocabulary"]
        fields = row.get("fields") or {}

        # 表名漂移：批量转录时，某一行的表名忽然不在已注册契约里。
        # 只登记，等循环结束后**按表名汇总成一条**——新表型刚入职时
        # 整张表都没注册，按行报会把问题清单淹掉。
        if named_sheets:
            sname = to_display(row.get("sheet"))
            if sname and sname not in named_sheets:
                unknown_sheets.setdefault(sname, set()).add(to_display(row.get("row_id")))

        # 1) 看不清 / 未识别
        for item in row.get("uncertain", []):
            add("OCR_UNCERTAIN", "warn", row,
                "看不清或无法确定：%s" % (item.get("field") or item.get("note", "")),
                item.get("candidates"), item.get("field"))

        # 2) 必填字段缺失（raw.json 声明的 + schema 里标了 required 的，取并集）
        required = [to_display(f) for f in (row.get("required_fields") or []) if to_display(f)]
        for col in (sch.get("columns") or []):
            if isinstance(col, dict) and col.get("required") and col.get("name"):
                cname = to_display(col["name"])
                if cname not in required:
                    required.append(cname)
        for field in required:
            if not to_display(fields.get(field)):
                add("MISSING_REQUIRED_FIELD", "warn", row,
                    "必填字段为空：%s" % field, None, field)

        # 2.5) 列类型校验：schema 声明了类型的列，值必须真的符合那个类型。
        #      这是通用能力——换任何表型，只要 schema 写清楚，"日期列写成乱码"
        #      这种事就会被抓住，而不是悄悄进 Excel。
        for col in (sch.get("columns") or []):
            if not isinstance(col, dict) or not col.get("name"):
                continue
            cname = to_display(col["name"])
            ctype = to_display(col.get("type")) or "text"
            cval = to_display(fields.get(cname))
            if not cval or ctype in ("text", "identifier"):
                continue
            if not value_matches_type(cval, ctype):
                add("TYPE_MISMATCH", "warn", row,
                    "「%s」列声明为 %s，但值是 %r，不符合该类型" % (cname, ctype, cval),
                    None, cname)

        # 3) 计量项合计校验（整数运算）
        meas = sch["measurements"]
        comps = row.get("components") or []
        if comps and meas.get("enabled", True):
            cats = meas.get("categories") or {}
            default_behavior = to_display(meas.get("default_category")) or "always"
            total = 0
            extra_value = None       # "if_present" 类别的值（如"杂"）
            for comp in comps:
                name = to_display(comp.get("name"))
                kind, registered = classify_component(name, aliases)

                # 0) raw_value（图上原文）与 value（换算值）必须自洽。
                #    这两个字段是模型分别给出的两份表述；一旦对不上，
                #    脚本无法判断哪个才对——而校验用 raw_value、最终 Excel 落 value，
                #    不查就会"校验一个数、交付另一个数"。故一律进问题清单，交人判断。
                #
                #    注意"换算"的含义随表型而变，由 schema 的 measurements 决定：
                #      百分比表型：value 是小数（89.6% <-> 0.896）
                #      其它单位  ：value 就是同一个数（14 小时 <-> 14）
                #    不区分就会把"14 小时"误判成 1400%，凭空产生严重问题。
                _raw_text = to_display(comp.get("raw_value"))
                _dec_text = to_display(comp.get("value"))
                _frac = meas.get("_fraction", True)
                _unit = to_display(meas.get("unit", "%"))
                if _raw_text and _dec_text:
                    _a = parse_percent(_raw_text)
                    _b = decimal_to_units(_dec_text) if _frac else parse_percent(_dec_text)
                    if _a is not None and _b is not None and _a != _b:
                        if _frac:
                            _sa, _sb = fmt_units(_a), fmt_units(_b)
                        else:
                            _sa = "%.2f%s" % (_a / float(SCALE), _unit)
                            _sb = "%.2f%s" % (_b / float(SCALE), _unit)
                        add("VALUE_MISMATCH", "severe", row,
                            "原文与换算值不一致：%s 原文「%s」=%s，换算值「%s」=%s"
                            % (name or "未命名计量项", _raw_text, _sa, _dec_text, _sb),
                            [_raw_text, _dec_text], name)

                # 取数：**原文优先**；原文缺失时按 value 正确换算。
                # 这里曾经写成 comp.get("raw_value", comp.get("value")) 再统一走 parse_percent，
                # 一旦原文缺失，百分比表型下 value="0.896"（本意 89.6%）会被当成 0.90%
                # （差约 100 倍），合计静默少算 → 真实超限的行拿不到告警。
                # 原文与 value 的刻度关系随表型而变（见上），绝不能共用一个解析函数。
                if _raw_text:
                    num = parse_percent(_raw_text)
                elif _dec_text:
                    num = decimal_to_units(_dec_text) if _frac else parse_percent(_dec_text)
                else:
                    num = None

                if name and kind == "comp" and is_suspicious_unregistered(name, aliases, known_terms):
                    unregistered_seen.setdefault(name, set()).add(obj)

                # 这个类别怎么参与合计，由 schema 决定（默认 water=never / misc=if_present / 其余=always）
                behavior = to_display(cats.get(kind)) or default_behavior
                if behavior == "never":
                    continue
                if behavior == "if_present":
                    if not _raw_text and not _dec_text:
                        continue                   # 空白 = 本来就没有，照录留白、不打扰
                    extra_value = num
                    if num is None:
                        add("TYPE_AMBIGUITY", "warn", row,
                            "无法解析的数值：%s = %r（该计量项已从本行合计中排除，本行合计不可信）"
                            % (name, _raw_text or _dec_text), None, name)
                    continue
                if num is None:
                    add("TYPE_AMBIGUITY", "warn", row,
                        "无法解析的数值：%s = %r（该计量项已从本行合计中排除，本行合计不可信）"
                        % (name, _raw_text or _dec_text), None, name)
                    continue
                total += num
            if extra_value is not None:
                total += extra_value
            sum_max = meas.get("_sum_max_units", FULL)
            if total > sum_max:
                if _frac:
                    pretty = "%.2f%%" % (total / float(SCALE))
                    limit = "%.2f%%" % (sum_max / float(SCALE))
                else:
                    pretty = "%.2f%s" % (total / float(SCALE), _unit)
                    limit = "%.2f%s" % (sum_max / float(SCALE), _unit)
                # 类型标识沿用 COMPONENT_SUM_OVER_100：人工决定是按 issue_id 存的
                # （形如 ISS-SUM-R002-），改名会让已填的决定全部对不上号、静默失效。
                # 所以名字保留，只有描述文案变成通用的。
                add("COMPONENT_SUM_OVER_100", "severe", row,
                    "计量项合计 %s，超过上限 %s（%s）"
                    % (pretty, limit, "含按条件计入的类别" if extra_value is not None else "不含按条件计入的类别"),
                    [pretty])

    # 4) 表名漂移汇总：每个没注册的表名只报一条
    for sname in sorted(unknown_sheets):
        issues.append({
            "issue_id": issue_id_for("SHEET_UNKNOWN", sname, seen_ids),
            "run_id": run.get("run_id", ""),
            "type": "SHEET_UNKNOWN",
            "severity": "info",
            "image": "",
            "sheet": sname,
            "target_ids": sorted(unknown_sheets[sname]),
            "object": "",
            "field": "sheet",
            "description": "表名「%s」不在已注册契约里（已注册：%s），本组按通用口径校验；"
                           "若这是新表型，请做样板并注册契约"
                           % (sname, "、".join(sorted(named_sheets))),
            "candidates": [],
            "machine_status": STATUS_PENDING,
            "human_decision": None,
        })

    # 5) 未注册词汇总
    for name, objs in unregistered_seen.items():
        issues.append({
            "issue_id": issue_id_for("UNREGISTERED_TERM", name, seen_ids),
            "run_id": run.get("run_id", ""),
            "type": "UNREGISTERED_TERM",
            "severity": "info",
            "image": "",
            "sheet": "",
            "target_ids": sorted(objs),
            "object": "",
            "field": name,
            "description": "新出现的成分词，尚未注册：%s" % name,
            "candidates": [],
            "machine_status": STATUS_PENDING,
            "human_decision": None,
        })
    return issues


def detect_duplicates(run):
    """同一张图重复提交：按 sha256 分组，一次把整组列清楚。

    过去是"谁重复就报谁，且只说与上一张相同"，三张同 sha 只得 2 条，
    人工看不出到底共几张、哪张才是最早的那张。
    """
    issues = []
    groups = {}
    order = []
    for image in run.get("images", []):
        sha = image.get("sha256")
        if not sha:
            continue
        if sha not in groups:
            groups[sha] = []
            order.append(sha)
        groups[sha].append(to_display(image.get("image_id")))

    for sha in order:
        ids = groups[sha]
        if len(ids) < 2:
            continue
        first = ids[0]
        for dup in ids[1:]:
            issues.append({
                "issue_id": "ISS-DUP-%s" % dup,
                "run_id": run.get("run_id", ""),
                "type": "DUPLICATE",
                "severity": "info",
                "image": dup,
                "sheet": "",
                "target_ids": [dup],
                "object": "",
                "field": "",
                "description": "与 %s 内容完全相同（同一张图重复提交）；本组共 %d 张：%s"
                               % (first, len(ids), "、".join(ids)),
                "candidates": [],
                "machine_status": STATUS_PENDING,
                "human_decision": None,
            })
    return issues


def sanitize_name(name, max_len=80):
    cleaned = re.sub(ILLEGAL, "-", str(name)).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .-")
    return cleaned or "未命名"


def unique_target(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists("%s_%d%s" % (base, n, ext)):
        n += 1
    return "%s_%d%s" % (base, n, ext)


def _is_inside(child, parent):
    """child 是否位于 parent 目录之内（解析 .. 与软链之后再判断）。"""
    if not child or not parent:
        return False
    try:
        c = os.path.realpath(child)
        p = os.path.realpath(parent)
    except Exception:
        return False
    return c == p or c.startswith(p + os.sep)


def sanitize_date_part(text):
    """把模型的 date_text 整成 YYYY-MM-DD；认不出来返回 ""（绝不猜成今天）。

    归档目录必须是 archive/YYYY-MM-DD/：
    过去 date_text 未清洗就拼路径，`2026/09/10` 会变成三层目录、
    `2026-09-10 上午` 会生成带空格的目录名、含 `:` 还会直接建档失败。
    """
    raw = to_display(text)
    m = re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})", raw)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return "%s-%02d-%02d" % (y, mo, d)
    return ""


def build_archive_plan(run, root, archive_root):
    """生成归档计划（只计划、不落盘），并把不安全/不可判定的项标出来。

    两条安全红线在这里落实：
      1) **来源必须是本工作区内的文件**——raw.json 是模型生成的，
         一个幻觉出来的绝对路径就能把工作区外的文件搬走（已实测复现过）；
      2) **目标必须是 archive/ 之内**——人工决定的 target_dir 同样不能越界。
    """
    plan = []
    for image in run.get("images", []):
        iid = to_display(image.get("image_id"))
        src = to_display(image.get("file"))
        if src and not os.path.isabs(src):
            src = os.path.join(root, src)

        date_part = sanitize_date_part(image.get("date_text"))
        label = image.get("sheet_label", "单据")
        suggested = image.get("suggested_name") or ("%s_%s_%s" % (
            date_part or "无日期", label, iid))
        name = sanitize_name(suggested)
        if not os.path.splitext(name)[1]:
            ext = os.path.splitext(src)[1] or ".jpg"
            name += ext

        item = {
            "image_id": iid,
            "source": src,
            "original_name": os.path.basename(src) if src else "",
            "suggested_name": name,
            "date_text_raw": to_display(image.get("date_text")),
            "date_part": date_part,
            "target_dir": os.path.join(archive_root, date_part) if date_part else "",
            "action": "move",
            "confirmed": None,
            "executed": False,
            "result": "未执行",
            "target_path": "",
            "source_ok": True,
            "blocked_reason": "",
        }
        if not src:
            item["source_ok"] = False
            item["blocked_reason"] = "raw.json 未提供 file 字段"
        elif not _is_inside(src, root):
            item["source_ok"] = False
            item["blocked_reason"] = "来源文件不在本工作区内，拒绝搬运工作区外的文件"
        elif not date_part:
            item["blocked_reason"] = "日期无法识别（可在 decisions.json 指定 target_dir 后归档）"
        if item["blocked_reason"]:
            item["result"] = "未执行（%s）" % item["blocked_reason"]
        plan.append(item)
    return plan


def execute_archive(plan, decisions_by_id, archive_root):
    moved, failed = 0, []
    for item in plan:
        item_id = item.get("image_id", "")
        decision = decisions_by_id.get(item_id)
        target_dir = item.get("target_dir") or ""
        if decision:
            if decision.get("confirmed") is False:
                item["result"] = "人工取消"
                continue
            if decision.get("suggested_name"):
                item["suggested_name"] = sanitize_name(decision["suggested_name"])
            if decision.get("target_dir"):
                target_dir = decision["target_dir"]

        if not item.get("source_ok", True):
            item["result"] = "未归档：%s" % (item.get("blocked_reason") or "来源不可用")
            failed.append(item)
            continue
        if not target_dir:
            item["result"] = "未归档：目标目录未确定（请在 decisions.json 里给 target_dir）"
            failed.append(item)
            continue
        if not _is_inside(target_dir, archive_root):
            item["result"] = "未归档：目标目录越出 archive/ 范围，已拒绝"
            failed.append(item)
            continue
        item["target_dir"] = target_dir
        if not os.path.exists(item["source"]):
            item["result"] = "源文件不存在"
            failed.append(item)
            continue
        os.makedirs(target_dir, exist_ok=True)
        target = unique_target(os.path.join(target_dir, item["suggested_name"]))
        if os.path.exists(target):
            # 显式挡住"覆盖"：Windows 的 os.rename 会报错，但 POSIX 下会静默覆盖，
            # 只靠 except 兜不住，必须自己先判一次。
            item["result"] = "目标已存在，未覆盖"
            failed.append(item)
            continue
        try:
            os.rename(item["source"], target)      # 绝不覆盖：同名会直接报错
            item["executed"] = True
            item["result"] = "成功"
            item["target_path"] = target
            moved += 1
        except FileExistsError:
            item["result"] = "目标已存在，未覆盖"
            failed.append(item)
        except PermissionError:
            item["result"] = "文件被占用或无权限"
            failed.append(item)
        except OSError as exc:
            item["result"] = "失败：%s" % exc
            failed.append(item)
    return moved, failed


def load_decisions(run_dir):
    data = read_json(os.path.join(run_dir, "decisions.json"), {})
    by_issue, by_image = {}, {}
    for item in data.get("issue_decisions", []):
        by_issue[item.get("issue_id", "")] = item
    for item in data.get("archive_decisions", []):
        by_image[item.get("image_id", "")] = item
    return by_issue, by_image


def collect_rows(run, blocking_issues, schemas=None):
    """把 raw 行（已落实人工决定）整理成准备落账的数据。

    **未解决（blocked）的行不进事实表**——红线：未解决的数据不得进入最终 Excel。
    过去这里只把 status 写进"明细数据"页，计量项却照样 append 进"成分事实"页，
    而该页没有状态列，等于把超限/看不清/未注册的值当成已确认数据交给了下游。

    事实表的列来自 schema 的 fact_keys，不再写死"化工厂"的那几列；
    schema 里没声明的列留空，通用表型也能用同一张事实表。
    """
    schemas = schemas or {"*": normalize_schema(DEFAULT_SCHEMA)}
    unresolved = set()
    for issue in blocking_issues:
        if issue["type"] in NON_BLOCKING_TYPES:
            continue
        for tid in issue.get("target_ids") or []:
            unresolved.add(tid)

    facts, detail = [], []
    for row in run.get("rows", []):
        role = to_display(row.get("row_role"))
        if role and role != "data":
            continue
        rid = row.get("row_id", "")
        status = STATUS_BLOCKED if rid in unresolved else STATUS_OK
        record = {
            "row_id": rid,
            "status": status,
            "sheet": row.get("sheet", ""),
            "fields": dict(row.get("fields") or {}),
        }
        detail.append(record)
        if status != STATUS_OK:
            continue          # 未解决的行只出现在审核用明细里，绝不进事实表
        fields = row.get("fields") or {}
        sch = schema_for(schemas, row.get("sheet"))
        aliases = sch["vocabulary"]
        cats = sch["measurements"].get("categories") or {}
        for comp in row.get("components") or []:
            name = to_display(comp.get("name"))
            kind, _ = classify_component(name, aliases)
            behavior = to_display(cats.get(kind)) or \
                to_display(sch["measurements"].get("default_category")) or "always"
            if behavior == "never" and not WATER_IN_FACTS:
                continue          # 开关关闭时才丢；默认 False -> 保留在事实表里
            fact = {"source_type": run.get("source_type", ""),
                    "source_run_id": run.get("run_id", ""),
                    "source_row_id": rid}
            for out_col, src_field in (sch.get("fact_keys") or {}).items():
                fact[out_col] = fields.get(to_display(src_field), "")
            fact.update({
                "component_standard_name": name,
                "component_original_name": name,
                "numeric_value": comp.get("value", comp.get("raw_value", "")),
                "unit": comp.get("unit", sch["measurements"].get("unit", "%")),
                "raw_value": comp.get("raw_value", ""),
                "category": kind,
            })
            facts.append(fact)
    return detail, facts, unresolved


def write_final(run, detail, facts, schemas, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "明细数据"
    if detail:
        headers = ["行号", "工作表", "状态"] + order_field_columns(detail, schemas)
        ws.append(headers)
        for cell in ws[1]:
            cell.font = Font(bold=True)
        for rec in detail:
            ws.append([rec["row_id"], rec["sheet"], rec["status"]] +
                      [rec["fields"].get(h, "") for h in headers[3:]])
        for idx, head in enumerate(headers, start=1):
            width = max(10, min(28, int(len(str(head)) * 2.2) + 6))
            ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width
    ws2 = wb.create_sheet("成分事实")
    present = set()
    for fact in facts:
        present.update(fact.keys())
    cols = [c for c in FACT_COLUMN_ORDER if c in present] + \
           sorted(present - set(FACT_COLUMN_ORDER))
    if not cols:
        cols = list(FACT_COLUMN_ORDER)
    ws2.append(cols)
    for cell in ws2[1]:
        cell.font = Font(bold=True)
    for fact in facts:
        ws2.append([fact.get(c, "") for c in cols])
    for idx in range(1, len(cols) + 1):
        ws2.column_dimensions[ws2.cell(row=1, column=idx).column_letter].width = 16
    wb.save(path)


def write_review(run, issues, detail, plan, path):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    import datetime as _dt

    wb = Workbook()
    yellow = PatternFill("solid", fgColor="FFF2CC")
    grey = PatternFill("solid", fgColor="F2F2F2")
    red = PatternFill("solid", fgColor="FCE4E4")

    # 1) 审核总览
    ws = wb.active
    ws.title = "审核总览"
    rows_count = len([r for r in run.get("rows", []) if r.get("row_role", "data") == "data"])
    severe = len([i for i in issues if i["severity"] == "severe"])
    warn = len([i for i in issues if i["severity"] == "warn"])
    info = len([i for i in issues if i["severity"] == "info"])
    new_types = len({i["type"] for i in issues})
    ws.append(["项目", "数量"])
    ws["A1"].font = ws["B1"].font = Font(bold=True)
    for label, value in [
        ("图片数", len(run.get("images", []))),
        ("数据行数", rows_count),
        ("问题总数", len(issues)),
        ("严重问题（必须处理）", severe),
        ("需确认（建议处理）", warn),
        ("提示信息（可不处理）", info),
        ("问题种类", new_types),
        ("待归档文件数", len(plan)),
        ("已确认归档数", len([p for p in plan if p.get("executed")])),
    ]:
        ws.append([label, value])
    ws.append([])
    ws.append(["提交时间", run.get("created", "")])
    ws.append(["任务编号", run.get("run_id", "")])
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 22

    # 2) 问题清单
    ws2 = wb.create_sheet("问题清单")
    ws2.append(["问题编号", "图片", "工作表", "对象", "问题类型", "严重度",
                "问题说明", "候选A", "候选B", "员工决定", "备注"])
    for cell in ws2[1]:
        cell.font = Font(bold=True)
        cell.fill = grey
    for issue in issues:
        cands = issue.get("candidates") or []
        ws2.append([
            issue["issue_id"], issue["image"], issue["sheet"], issue["object"],
            issue["type"], issue["severity"], issue["description"],
            cands[0] if len(cands) > 0 else "",
            cands[1] if len(cands) > 1 else "",
            issue.get("human_decision") or "", "",
        ])
        if issue["severity"] == "severe":
            for col in range(1, 12):
                ws2.cell(row=ws2.max_row, column=col).fill = red
        elif issue["severity"] == "warn":
            for col in range(1, 12):
                ws2.cell(row=ws2.max_row, column=col).fill = yellow
        ws2.cell(row=ws2.max_row, column=10).number_format = "@"
    widths = [12, 12, 12, 16, 22, 9, 40, 12, 12, 14, 16]
    for idx, w in enumerate(widths, start=1):
        ws2.column_dimensions[ws2.cell(row=1, column=idx).column_letter].width = w
    if issues:
        dv = DataValidation(type="list", formula1='"通过,沿用候选A,沿用候选B,见备注"', allow_blank=True)
        ws2.add_data_validation(dv)
        dv.add("J2:J%d" % (len(issues) + 1))

    # 3) 待审核数据
    ws3 = wb.create_sheet("待审核数据")
    if detail:
        headers = ["行号", "工作表", "状态"] + sorted({k for r in detail for k in r["fields"].keys()})
        ws3.append(headers)
        for cell in ws3[1]:
            cell.font = Font(bold=True)
            cell.fill = grey
        for rec in detail:
            ws3.append([rec["row_id"], rec["sheet"], rec["status"]] +
                       [rec["fields"].get(h, "") for h in headers[3:]])
            if rec["status"] != STATUS_OK:
                for col in range(1, len(headers) + 1):
                    ws3.cell(row=ws3.max_row, column=col).fill = yellow

    # 4) 文件归档清单
    ws4 = wb.create_sheet("文件归档清单")
    ws4.append(["图片编号", "原文件名", "建议文件名", "目标目录", "是否归档(是/否)", "实际结果"])
    for cell in ws4[1]:
        cell.font = Font(bold=True)
        cell.fill = grey
    for item in plan:
        ws4.append([item["image_id"], item["original_name"], item["suggested_name"],
                    item["target_dir"], "是", item["result"]])
    for idx, w in enumerate([12, 30, 34, 36, 14, 22], start=1):
        ws4.column_dimensions[ws4.cell(row=1, column=idx).column_letter].width = w
    if plan:
        # 空表时区间会退化成 E2:E1，openpyxl 直接抛 ValueError。
        # "images 为空"的 raw.json 就会走到这里，必须挡一下。
        dv2 = DataValidation(type="list", formula1='"是,否"', allow_blank=True)
        ws4.add_data_validation(dv2)
        dv2.add("E2:E%d" % (len(plan) + 1))

    wb.save(path)


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")


def safe_read(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def detect_phase(run_dir, raw, source_files):
    """判断任务现在停在哪个阶段（样板 → 批量 → 复核）。"""
    sample = safe_read(os.path.join(run_dir, "sample.json"), {}) or {}
    sample_made = os.path.exists(os.path.join(run_dir, "样板.xlsx"))
    recognized = set()
    if raw:
        recognized = {os.path.basename(str(i.get("file", ""))) for i in raw.get("images", [])}
    missing = [f for f in source_files if f not in recognized]

    if not raw or not recognized:
        return "① 未开始", "还没建任务 / 还没识别"
    if not sample.get("confirmed"):
        if sample_made:
            return "② 等样板确认", "样板.xlsx 已生成，等用户看完回话"
        return "② 立样板", "还没做样板：先拿一张有代表性的图做出范例给用户看"
    if missing:
        return "③ 批量转录中", "样板已确认，还剩 %d 张没识别" % len(missing)
    return "④ 待统一复核", "全部识别完，问题一次性交人工"


def show_status(run_dir, root):
    """查看任务进度：断点续跑用。任何时候中断，重新跑这条命令就知道下一步是什么。"""
    print("== 任务进度：%s ==" % os.path.basename(run_dir))
    src_dir = os.path.join(run_dir, "source")
    source_files = []
    if os.path.isdir(src_dir):
        source_files = sorted(f for f in os.listdir(src_dir) if f.lower().endswith(IMAGE_EXT))
    raw = safe_read(os.path.join(run_dir, "raw.json"))
    issues_doc = safe_read(os.path.join(run_dir, "issues.json"))
    decisions = safe_read(os.path.join(run_dir, "decisions.json"))
    plan_doc = safe_read(os.path.join(run_dir, "archive_plan.json"))
    review_ok = os.path.exists(os.path.join(run_dir, "task_review.xlsx"))
    final_ok = os.path.exists(os.path.join(run_dir, "final.xlsx"))

    print("  原图：%d 张" % len(source_files))
    phase, why = detect_phase(run_dir, raw, source_files)
    print("  当前阶段：%s —— %s" % (phase, why))

    # [1] 识别
    if raw:
        got = {os.path.basename(str(i.get("file", ""))) for i in raw.get("images", [])}
        missing = [f for f in source_files if f not in got]
        rows = [r for r in raw.get("rows", []) if r.get("row_role", "data") == "data"]
        print("  [1] 识别：已完成（%d 行数据）" % len(rows))
        if missing:
            print("      尚未识别：%s" % "、".join(missing))
    else:
        missing = source_files
        print("  [1] 识别：未开始")

    # [2] 校验与问题
    undecided = []
    if issues_doc:
        issues = issues_doc.get("issues", [])
        severe = len([i for i in issues if i.get("severity") == "severe"])
        warn = len([i for i in issues if i.get("severity") == "warn"])
        info_n = len([i for i in issues if i.get("severity") == "info"])
        done = len([i for i in issues
                    if i.get("human_decision") or i.get("machine_status") in ("resolved", "accepted")])
        print("  [2] 校验：已完成（问题 %d 条：严重 %d / 需确认 %d / 提示 %d；已解决 %d）"
              % (len(issues), severe, warn, info_n, done))
        undecided = [i for i in issues if not i.get("human_decision")
                     and i.get("machine_status") not in ("resolved", "accepted")]
        if undecided:
            print("      待人工处理 %d 条，例如 %s（%s）"
                  % (len(undecided), undecided[0].get("issue_id", ""),
                     undecided[0].get("description", "")[:28]))
    else:
        print("  [2] 校验：未开始")

    # [3] 审核与决定
    dec_count = len([d for d in (decisions or {}).get("issue_decisions", []) if d.get("decision")])
    if decisions:
        print("  [3] 人工决定：已收到 %d 条" % dec_count)
    elif review_ok:
        print("  [3] 人工决定：审核表已生成，等待用户填写")

    # [4] 落账与归档
    if final_ok:
        print("  [4] 最终数据：已生成 final.xlsx")
    if plan_doc:
        plan = plan_doc.get("plan", [])
        moved = len([p for p in plan if p.get("executed")])
        failed = len([p for p in plan if p.get("result") and p.get("result") != "成功" and p.get("result") != "未执行"])
        print("  [5] 归档：%d/%d 已移动%s" % (moved, len(plan), ("，失败 %d 个" % failed) if failed else ""))
        if failed:
            for p in plan:
                if p.get("result") and p.get("result") not in ("成功", "未执行"):
                    print("      - %s：%s" % (p.get("original_name", ""), p.get("result")))

    # 下一步建议
    print("  ---- 下一步 ----")
    if not raw:
        print("  继续识别：逐图生成 raw.json（未识别：%s）" % ("、".join(missing) if missing else "无"))
    elif missing:
        print("  继续识别剩余 %d 张：%s" % (len(missing), "、".join(missing)))
    elif not issues_doc:
        print("  运行校验：python validate_export.py \"%s\"" % os.path.relpath(run_dir, root))
    elif undecided and not dec_count:
        print("  把 task_review.xlsx 交给用户，请其只填\"问题清单\"的员工决定列")
    elif dec_count and not final_ok:
        print("  收尾重跑：python validate_export.py \"%s\" --archive" % os.path.relpath(run_dir, root))
    elif final_ok and not (plan_doc and any(p.get("executed") for p in plan_doc.get("plan", []))):
        print("  执行归档：python validate_export.py \"%s\" --archive" % os.path.relpath(run_dir, root))
    else:
        print("  任务已完成。如需修改，补充 decisions.json 后重跑即可。")


def warn_unsaved_decisions(review_path, decisions_by_issue):
    """重跑前检查：审核表里是否有人工填写、但还没整理进 decisions.json。

    task_review.xlsx 是**派生文件**，每次重跑整表重写。用户若直接在 Excel 的
    "员工决定"列填写、却没整理成 decisions.json，下次重跑就会被静默清空。
    这里只检测并告警，不改动任何东西。
    """
    if not os.path.exists(review_path):
        return
    pending = []
    try:
        from openpyxl import load_workbook
        wb = load_workbook(review_path, read_only=True)
        try:
            if "问题清单" not in wb.sheetnames:
                return
            ws = wb["问题清单"]
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or len(row) < 10:
                    continue
                iid = to_display(row[0])
                decision = to_display(row[9])
                if not iid or not decision:
                    continue
                if not (decisions_by_issue.get(iid) or {}).get("decision"):
                    pending.append((iid, decision))
        finally:
            wb.close()
    except Exception as exc:
        print("  [提示] 读不了旧的 task_review.xlsx（%s），跳过「未保存决定」检查" % exc)
        return
    if pending:
        print("")
        print("  [注意] 旧的 task_review.xlsx 里有 %d 条人工填写，尚未整理进 decisions.json：" % len(pending))
        for iid, decision in pending[:10]:
            print("    - %s：%s" % (iid, decision))
        if len(pending) > 10:
            print("    …… 另有 %d 条" % (len(pending) - 10))
        print("  这些内容会在本次重跑时被清空——请先写进 decisions.json 再重跑。")
        print("")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--init-issues", action="store_true")
    parser.add_argument("--status", action="store_true", help="查看任务进度（断点续跑用）")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        die("目录不存在：" + run_dir)
    root = os.path.dirname(os.path.dirname(run_dir)) if os.path.basename(os.path.dirname(run_dir)) == "runs" else os.getcwd()

    if args.status:
        show_status(run_dir, root)
        return

    print("== 处理任务：%s ==" % run_dir)
    run = read_json(os.path.join(run_dir, "raw.json"))
    run, sanity = normalize_run(run)
    if sanity:
        print("  raw.json 结构异常 %d 处（已按可处理的方式修正，并会写进问题清单）：" % len(sanity))
        for note in sanity:
            print("    - " + note)
    aliases, registered = load_aliases(root)
    print("  已注册词表条目：%d 条%s" % (len(registered), ("（%s）" % "、".join(registered)) if registered else ""))

    schemas = load_schemas(root)
    declared = [k for k in schemas if k != "*"]
    print("  已加载版式契约：%d 个%s" % (len(declared), ("（%s）" % "、".join(declared)) if declared else "（仅内置通用默认）"))

    known_terms = load_known_terms(root)
    print("  已注册模板词条：%d 个" % len(known_terms))
    decisions_by_issue, decisions_by_image = load_decisions(run_dir)

    # 第一遍：按原样校验（用于把人工决定对应到具体问题）
    unregistered = {}
    pass1 = build_issues(run, schemas, unregistered, known_terms)
    pass1 += detect_duplicates(run)

    # 应用人工决定（改的是工作数据；通过类决定不改数据）
    accepted, applied = apply_decisions(run, decisions_by_issue, pass1)
    if applied:
        print("  已按人工决定修正 %d 处数据" % applied)

    # 第二遍：按已落实决定的数据重新校验（问题编号稳定，不会错位）
    unregistered2 = {}
    issues = build_issues(run, schemas, unregistered2, known_terms)
    issues += detect_duplicates(run)

    # 已解决的问题（第一遍有、第二遍消失）保留在账本里，供审计
    p1_map = {i["issue_id"]: i for i in pass1}
    p2_ids = {i["issue_id"] for i in issues}
    resolved = []
    for iid, item in p1_map.items():
        if iid in p2_ids:
            continue
        d = decisions_by_issue.get(iid) or {}
        if d.get("decision"):
            item = dict(item)
            item["machine_status"] = "resolved"
            item["human_decision"] = d.get("decision")
            resolved.append(item)

    # 回填人工决定（审核表预填 + 账本留痕）
    old = read_json(os.path.join(run_dir, "issues.json"), {"issues": []})
    old_map = {i["issue_id"]: i.get("human_decision") for i in old.get("issues", [])}
    kept = 0
    for item in issues:
        d = decisions_by_issue.get(item["issue_id"])
        if d and d.get("decision"):
            item["human_decision"] = d["decision"]
        elif old_map.get(item["issue_id"]):
            item["human_decision"] = old_map[item["issue_id"]]
            kept += 1
    if kept:
        print("  已回填历史人工决定：%d 条" % kept)

    # 通过类决定 -> 不再阻塞
    accepted_ids = set()
    for item in issues:
        if is_approve(item.get("human_decision")):
            item["machine_status"] = "accepted"
            accepted_ids.add(item["issue_id"])
    blocking_issues = [i for i in issues if i["issue_id"] not in accepted_ids]

    print("  问题 %d 条（严重 %d / 需确认 %d / 提示 %d）；本次按决定解决 %d 条" % (
        len(issues),
        len([i for i in issues if i["severity"] == "severe"]),
        len([i for i in issues if i["severity"] == "warn"]),
        len([i for i in issues if i["severity"] == "info"]),
        len(resolved),
    ))
    write_json(os.path.join(run_dir, "issues.json"),
               {"run_id": run.get("run_id", ""), "issues": issues + resolved,
                "resolved_count": len(resolved)})

    if args.init_issues:
        print("  （--init-issues：仅刷新问题账本，不生成 Excel）")
        return
    detail, facts, unresolved = collect_rows(run, blocking_issues, schemas)
    print("  数据行：%d 行；其中未解决：%d 行（不进最终 Excel 的事实表）" % (len(detail), len(unresolved)))

    review_path = os.path.join(run_dir, "task_review.xlsx")
    warn_unsaved_decisions(review_path, decisions_by_issue)

    archive_root = os.path.join(root, "archive")
    write_final(run, detail, facts, schemas, os.path.join(run_dir, "final.xlsx"))
    plan = build_archive_plan(run, root, archive_root)
    write_review(run, issues, detail, plan, review_path)
    print("  已生成 final.xlsx 与 task_review.xlsx")

    if args.archive:
        moved, failed = execute_archive(plan, decisions_by_image, archive_root)
        print("  归档完成：成功 %d 个，失败/跳过 %d 个" % (moved, len(failed)))
        for item in failed:
            print("    - %s：%s" % (item["original_name"], item["result"]))
    write_json(os.path.join(run_dir, "archive_plan.json"), {"run_id": run.get("run_id", ""), "plan": plan})
    print("== 完成 ==")


if __name__ == "__main__":
    main()
