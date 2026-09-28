# -*- coding: utf-8 -*-
"""提交识别 —— AI 只管看图，交上来一小段"这张图上我看到了什么"，其余全由本脚本干。

    python tools/提交识别.py runs/20260910_003 --next        看下一张待识别的图 + 该填的格式
    python tools/提交识别.py runs/20260910_003 --list        看进度
    python tools/提交识别.py runs/20260910_003 --image IMG_001 --file 片段.json
    python tools/提交识别.py runs/20260910_003 --image IMG_001 --stdin
    python tools/提交识别.py runs/20260910_003 --image IMG_001 --file 片段.json --replace

**为什么要有这个脚本**：以前 AI 要亲手写整个 raw.json —— 里面混着任务编号、图片路径、
指纹、行号、必填字段、单位换算（89.6% → 0.896）、建议文件名。这些全是机器该干的活，
由 AI 手写只会增加出错面，而且错了要等到最后跑校验才发现。

现在 AI 只交"一张图"的内容，脚本负责：
  · 校验格式（缺 row_role、列名不在契约里、数值解析不了 → **当场退回，不落盘**）
  · 编号（row_id 续号）
  · 换算（raw_value → value，用裁判脚本自己的换算函数，绝不另写一套）
  · 必填字段（从契约的 columns[].required 推出来，不让 AI 记）
  · 合并落盘（原子写，一张图一张图地存，断电不丢）
  · 当场把裁判的判定结果回报给 AI（合计超百、同类词未注册、看不清……）

**两种错误分开对待**（这是本脚本的核心设计）：
  · 格式/契约错误 → 拒绝写入，返回错误清单，让 AI 改（因为这是"写错了"，不是"看不清"）
  · 业务疑点     → 照常落盘，交给最后的统一复核（因为这是流程设计：疑点记账，中途不打扰人）

退出码：0 成功（含"有业务疑点但已落盘"）；2 被拒绝/参数错误。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
# stdin 也要钉成 UTF-8：--stdin 交上来的片段就是从这里读的。
# 这台机器默认 GBK，不钉的话中文在**落盘之前**就变成乱码了，
# 而且退出码 0、看不出任何异常 —— 属于最阴的一类错。
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import validate_export as ve  # noqa: E402  （唯一裁判，只用它的函数，不改它）


def head(title):
    print()
    print("=" * 60)
    print("  " + title)
    print("=" * 60)


def load_text_json(path):
    """读 JSON，容忍 BOM。

    裁判脚本的 read_json 用 utf-8 读，带 BOM 的文件会直接崩；
    这里用 utf-8-sig 读（多一个 BOM 也认），写回去时统一不带 BOM，顺手把地雷拆了。
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_run_dir(arg):
    """把 runs/20260910_003、20260910_003、绝对路径 都认出来，统一成绝对路径。"""
    cand = arg if os.path.isabs(arg) else os.path.join(BASE, arg)
    cand = os.path.abspath(cand)
    if os.path.isdir(cand):
        return cand
    alt = os.path.join(BASE, "runs", arg.strip().strip("/\\"))
    if os.path.isdir(alt):
        return os.path.abspath(alt)
    return None


def load_manifest(run_dir):
    path = os.path.join(run_dir, "source", "names.json")
    if not os.path.isfile(path):
        return None
    try:
        return load_text_json(path)
    except Exception as e:
        print("[错误] 图片台账读不了：%s（%s）" % (path, e))
        return None


def load_raw(run_dir):
    path = os.path.join(run_dir, "raw.json")
    if not os.path.isfile(path):
        return None
    try:
        data = load_text_json(path)
    except Exception as e:
        print("[错误] raw.json 读不了：%s（%s）" % (e, path))
        return None
    if not isinstance(data, dict):
        print("[错误] raw.json 顶层不是对象，拒绝继续（怕越改越乱）")
        return None
    data.setdefault("images", [])
    data.setdefault("rows", [])
    if not isinstance(data["images"], list):
        data["images"] = []
    if not isinstance(data["rows"], list):
        data["rows"] = []
    return data


def pending_list(manifest, raw):
    """还没识别（没进 raw.json）的图。"""
    done = set()
    for img in raw.get("images", []):
        if isinstance(img, dict):
            done.add(img.get("image_id"))
    out = []
    for item in manifest.get("images", []):
        if item.get("image_id") not in done:
            out.append(item)
    return out, done


def schema_brief(schemas):
    """给 AI 看的契约摘要：已注册哪些表型、各有哪些列、哪些必填、单位是什么。"""
    lines = []
    for label in sorted(schemas.keys()):
        sch = schemas[label]
        if label == "*":
            continue
        cols = []
        for col in sch.get("columns", []):
            name = col.get("name", "")
            mark = "*" if col.get("required") else ""
            cols.append("%s%s" % (name, mark))
        unit = (sch.get("measurements") or {}).get("unit", "%")
        lines.append("  · %s" % label)
        lines.append("      %s" % (sch.get("row_unit") or "一行 = 一个业务对象"))
        lines.append("      列（带 * 为必填）：%s" % ("、".join(cols) if cols else "（契约里没写列）"))
        if (sch.get("measurements") or {}).get("enabled", True):
            lines.append("      计量项单位：%s；合计上限 %s%%（水不计入合计）"
                         % (unit, (sch.get("measurements") or {}).get("sum_max", 100)))
    if not lines:
        lines.append("  （还没有注册任何表型，只能用 schemas/_default.json 的通用口径）")
    return lines


FRAGMENT_TEMPLATE = """{
  "image_id": "<图片编号，必须和 --image 一致>",
  "sheet_label": "<这张图是什么表，取自上面已注册的表型名>",
  "date_text": "<图上写的日期，原样抄，认不出就留空>",
  "rows": [
    {
      "row_role": "data | summary | header | note    ← 必填，身份不明的行会被拦下",
      "object_label": "<这行是谁，例如 A001/桶01>",
      "fields": {"<列名>": "<图上原文>"},
      "components": [{"name": "<计量项名>", "raw_value": "<图上原文，如 89.6%>"}],
      "uncertain": [{"field": "<哪一格>", "candidates": ["候选1", "候选2"], "note": "<为什么拿不准>"}]
    }
  ]
}

注意：
  · 不要写 row_id、value、unit、required_fields、suggested_name、sha256、file —— 这些由脚本算。
  · components 里的 raw_value 写**图上原文**，不要自己换算；换算由脚本用裁判的函数做。
  · 看不清的格子写进 uncertain，**不要猜**。图上有几行就写几行，合计行/表头/备注行也要写，
    但 row_role 要标对。"""


def cmd_next(run_dir, manifest, raw, schemas):
    pending, done = pending_list(manifest, raw)
    total = len(manifest.get("images", []))
    head("下一张待识别")
    print("  任务：%s" % os.path.basename(run_dir))
    print("  进度：%d / %d 张已识别" % (len(done), total))
    if not pending:
        print()
        print("  全部识别完了。下一步：")
        print("    python validate_export.py \"%s\" --init-issues" % os.path.relpath(run_dir, BASE))
        return 0
    item = pending[0]
    abs_path = os.path.join(BASE, item.get("file", "").replace("/", os.sep))
    print()
    print("  图片编号：%s" % item.get("image_id"))
    print("  原文件名：%s" % item.get("original_name"))
    print("  绝对路径：%s" % abs_path)
    if not os.path.isfile(abs_path):
        print("  ⚠ 这个文件不在！请检查 runs/.../source/ 目录，或用 --list 核对台账。")
    print()
    print("  用 read_image 看这张图，然后按下面的格式交回来：")
    print()
    print("    提交方式（二选一）：")
    print("      python tools/提交识别.py \"%s\" --image %s --file 片段.json"
          % (os.path.relpath(run_dir, BASE), item.get("image_id")))
    print("      python tools/提交识别.py \"%s\" --image %s --stdin"
          % (os.path.relpath(run_dir, BASE), item.get("image_id")))
    print()
    print("  已注册的表型（用来填 sheet_label，不要自己发明新列）：")
    for ln in schema_brief(schemas):
        print(ln)
    print()
    print("  片段格式：")
    print(FRAGMENT_TEMPLATE)
    return 0


def cmd_list(run_dir, manifest, raw):
    pending, done = pending_list(manifest, raw)
    head("任务进度（识别阶段）")
    print("  任务：%s" % os.path.basename(run_dir))
    print("  图片：%d 张；已识别 %d 张；待识别 %d 张" % (len(manifest.get("images", [])), len(done), len(pending)))
    if pending:
        print("  待识别：%s" % "、".join(p.get("image_id", "?") for p in pending))
    rows = [r for r in raw.get("rows", []) if isinstance(r, dict)]
    print("  已落盘数据行：%d 行" % len(rows))
    print()
    print("  查完整进度（含校验/审核/归档）：python validate_export.py \"%s\" --status"
          % os.path.relpath(run_dir, BASE))
    return 0


# 归档名里常见的逻辑占位符 -> 契约 fact_keys 里的键。
# 契约可以写 {批次}（直接写列名），也可以写 {batch}（写逻辑名），两种都认。
NAME_ALIASES = {
    "date": "record_date", "日期": "record_date",
    "location": "location", "位置": "location",
    "batch": "batch_id", "批次": "batch_id",
    "container": "container_id", "桶号": "container_id", "罐号": "container_id",
    "material": "material_name", "物料": "material_name",
    "supplier": "supplier", "厂商": "supplier",
}


def suggest_name(sch, date_text, sheet_label, fields, seq):
    """按契约里的 `archive.name_pattern` 算归档文件名。

    以前这个名字是 AI 手写的（契约里明明写着规则，却没人读它，等于白写）。
    现在由脚本算，认三种占位符：

      {date} / {sheet} / {n}              -> 日期 / 表型名 / 这一天这个表型的第几张（1 起）
      {列名}（如 {批次}）                   -> 首行数据行的同名列
      {逻辑名}（如 {batch}）                -> 经契约 fact_keys 映射到列名再取值

    认不出的占位符**直接去掉**，绝不把 `{批次}` 这种字面量写进文件名 ——
    宁可名字短一点，也不要一个看不懂的名字。去掉了哪些会一并返回（不静默）。
    """
    pattern = ve.to_display((sch.get("archive") or {}).get("name_pattern"))
    if not pattern:
        return "", []
    fact_keys = sch.get("fact_keys") or {}
    unused = []

    def value_for(token):
        if token == "n":
            return str(seq)
        if token == "sheet":
            return ve.to_display(sheet_label)
        if token == "date":
            return ve.sanitize_date_part(date_text) or ve.to_display(date_text)
        if token in (fields or {}):
            return ve.to_display(fields[token])
        col = fact_keys.get(NAME_ALIASES.get(token, token))
        if col and col in (fields or {}):
            return ve.to_display(fields[col])
        return ""

    out = pattern
    for token in dict.fromkeys(re.findall(r"\{([^}]*)\}", pattern)):
        val = value_for(token)
        if not val:
            unused.append(token)
        out = out.replace("{%s}" % token, val)

    out = re.sub(r"_{2,}", "_", out)          # 占位符被去掉后留下的多余下划线
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" _-."), unused


def validate_fragment(frag, image_id, manifest_item, schemas, known_image_ids):
    """校验 AI 交上来的片段。返回 (错误列表, 提醒列表)。错误非空 → 拒绝写入。"""
    errors, notes = [], []

    if not isinstance(frag, dict):
        return ["片段顶层必须是 JSON 对象（{}），收到的是 %s" % type(frag).__name__], notes

    fid = ve.to_display(frag.get("image_id"))
    if fid and fid != image_id:
        errors.append("片段里的 image_id 是 %r，但命令指定的是 %r —— 两者必须一致" % (fid, image_id))

    sheet_label = ve.to_display(frag.get("sheet_label"))
    for key in frag:
        if key not in ("image_id", "sheet_label", "date_text", "rows", "notes"):
            notes.append("片段里的 %r 不是约定字段，已忽略" % key)

    rows = frag.get("rows")
    if rows is None:
        errors.append("片段缺少 rows（图上有几行就写几行；一行都没有也要写成空数组 []）")
        return errors, notes
    if not isinstance(rows, list):
        errors.append("rows 必须是数组，收到的是 %s" % type(rows).__name__)
        return errors, notes
    if not rows:
        notes.append("这一张图一行都没有。图上真的有内容吗？如果有，说明漏了 —— 请重交。")

    if not sheet_label and not any(ve.to_display(r.get("sheet")) for r in rows if isinstance(r, dict)):
        errors.append("没有 sheet_label，也没有任何一行写 sheet —— 无法判断这是哪种表")
        return errors, notes

    for idx, row in enumerate(rows, 1):
        tag = "第 %d 行" % idx
        if not isinstance(row, dict):
            errors.append("%s 不是对象（{}）" % tag)
            continue

        role = ve.to_display(row.get("row_role"))
        if role not in ve.ROW_ROLES:
            errors.append(
                "%s 的 row_role = %r 不合法。必须是下列之一：%s。"
                "身份不明的行会被裁判拦下，不允许留空或猜 —— 请照着图重新判一次。"
                % (tag, row.get("row_role"), " / ".join(ve.ROW_ROLES)))
            continue

        row_sheet = ve.to_display(row.get("sheet")) or sheet_label
        sch = ve.schema_for(schemas, row_sheet) if row_sheet else schemas.get("*")

        fields = row.get("fields")
        if fields is None:
            fields = {}
        if not isinstance(fields, dict):
            errors.append("%s 的 fields 不是对象" % tag)
            continue
        allowed = [ve.to_display(c.get("name")) for c in sch.get("columns", []) if c.get("name")]
        if allowed:
            unknown = [k for k in fields if k not in allowed]
            if unknown:
                errors.append(
                    "%s 用了契约里没有的列：%s。这张表的契约列是：%s。"
                    "不要自己发明新列 —— 如果图上确实有这一列，先走 tools/注册表型.py 更新契约。"
                    % (tag, "、".join(unknown), "、".join(allowed)))
                continue

        comps = row.get("components") or []
        if not isinstance(comps, list):
            errors.append("%s 的 components 不是数组" % tag)
            continue
        for c in comps:
            if not isinstance(c, dict):
                errors.append("%s 里有一个计量项不是对象" % tag)
                continue
            if not ve.to_display(c.get("name")):
                errors.append("%s 里有一个计量项没写 name" % tag)
                continue
            raw_v = ve.to_display(c.get("raw_value"))
            if raw_v and ve.parse_percent(raw_v) is None:
                errors.append(
                    "%s 的计量项 %r 的 raw_value = %r 解析不出数值。"
                    "数值读不清就写进 uncertain，**不要猜一个数**。"
                    % (tag, ve.to_display(c.get("name")), raw_v))

        unc = row.get("uncertain") or []
        if not isinstance(unc, list):
            errors.append("%s 的 uncertain 不是数组" % tag)
            continue
        for u in unc:
            if not isinstance(u, dict):
                errors.append("%s 里有一条 uncertain 不是对象" % tag)
            elif not ve.to_display(u.get("field")) and not ve.to_display(u.get("note")):
                errors.append("%s 里有一条 uncertain 既没写 field 也没写 note，等于没说清哪里看不清" % tag)

    if image_id not in known_image_ids:
        errors.append("图片编号 %r 不在本任务的台账里（可能不是这个任务的图）" % image_id)

    return errors, notes


def normalize_rows(frag, image_id, manifest_item, schemas, next_row_no):
    """把 AI 的片段补全成 raw.json 要的样子：编号、换算、必填、单位，全部由脚本算。"""
    sheet_label = ve.to_display(frag.get("sheet_label"))
    date_text = ve.to_display(frag.get("date_text"))
    out = []
    for row in frag.get("rows") or []:
        row_sheet = ve.to_display(row.get("sheet")) or sheet_label
        sch = ve.schema_for(schemas, row_sheet) if row_sheet else schemas.get("*")

        fields = {}
        for k, v in (row.get("fields") or {}).items():
            fields[ve.to_display(k)] = ve.to_display(v)

        required = [ve.to_display(c.get("name")) for c in sch.get("columns", [])
                    if c.get("required") and c.get("name")]

        unit = (sch.get("measurements") or {}).get("unit", "%") or "%"
        comps = []
        for c in (row.get("components") or []):
            name = ve.to_display(c.get("name"))
            raw_v = ve.to_display(c.get("raw_value"))
            # value 由脚本换算，绝不采信 AI 写的（裁判的换算函数，与校验同一套）
            value = ve.percent_to_decimal_text(raw_v) if raw_v else ""
            if "value" in c and ve.to_display(c.get("value")):
                pass  # 静默接受但覆盖；下面统一提示
            comps.append({
                "name": name,
                "raw_value": raw_v,
                "value": value,
                "unit": ve.to_display(c.get("unit")) or unit,
            })

        unc = []
        for u in (row.get("uncertain") or []):
            cands = u.get("candidates") or []
            if not isinstance(cands, list):
                cands = [ve.to_display(cands)]
            unc.append({
                "field": ve.to_display(u.get("field")),
                "candidates": [ve.to_display(c) for c in cands],
                "note": ve.to_display(u.get("note")),
            })

        new_row = {
            "row_id": "R%03d" % next_row_no,
            "image_id": image_id,
            "sheet": row_sheet,
            "row_role": ve.to_display(row.get("row_role")),
            "object_label": ve.to_display(row.get("object_label")),
            "required_fields": required,
            "fields": fields,
            "components": comps,
            "uncertain": unc,
        }
        out.append(new_row)
        next_row_no += 1
    return out, date_text


def referee_feedback(raw, ws_root):
    """用裁判脚本自己的判定逻辑，立刻把这张图的问题报出来（同一套规则，不另写一份）。"""
    run, _notes = ve.normalize_run(dict(raw))
    schemas = ve.load_schemas(ws_root)
    aliases, _words = ve.load_aliases(ws_root)
    known = ve.load_known_terms(ws_root)
    issues = list(ve.build_issues(run, schemas, {}, known))
    issues += list(ve.detect_duplicates(run))
    return issues


def cmd_submit(run_dir, manifest, raw, schemas, image_id, frag, replace):
    item = None
    for it in manifest.get("images", []):
        if it.get("image_id") == image_id:
            item = it
            break
    if item is None:
        print("[错误] 图片编号 %r 不在本任务台账里。可用编号：%s"
              % (image_id, "、".join(i.get("image_id", "?") for i in manifest.get("images", []))))
        return 2

    known_image_ids = {i.get("image_id") for i in manifest.get("images", [])}
    errors, notes = validate_fragment(frag, image_id, item, schemas, known_image_ids)

    already = any(isinstance(i, dict) and i.get("image_id") == image_id for i in raw.get("images", []))
    if already and not replace:
        errors.append("这张图已经交过了。确认要重交（会覆盖这张图的旧结果），加 --replace。")

    head("提交识别：%s" % image_id)
    for n in notes:
        print("  [提醒] " + n)
    if errors:
        print()
        print("  ✗ 被拒绝，**没有写入任何东西**。请改好再交一次：")
        for e in errors:
            print("      · %s" % e)
        print()
        print("  提示：看不清的格子写进 uncertain（field + candidates + note），不要猜一个值；")
        print("        身份不明的行 row_role 必须照实标（data/summary/header/note）。")
        return 2

    # 续号要排除"这张图自己的旧行"：重交（--replace）时行号必须复用原来的 R001、R002……
    # 因为问题编号里嵌着 row_id（ISS-SUM-R002-），行号一变，人工已经填过的决定就会错位。
    next_row_no = 1
    for r in raw.get("rows", []):
        if not isinstance(r, dict):
            continue
        if r.get("image_id") == image_id:
            continue
        if str(r.get("row_id", "")).startswith("R"):
            try:
                next_row_no = max(next_row_no, int(str(r["row_id"])[1:]) + 1)
            except ValueError:
                pass

    new_rows, date_text = normalize_rows(frag, image_id, item, schemas, next_row_no)

    # 覆盖式合并：同一张图重交 = 换掉这张图的行和图片条目，别的图一行不动。
    raw["rows"] = [r for r in raw.get("rows", [])
                   if not (isinstance(r, dict) and r.get("image_id") == image_id)]
    raw["rows"].extend(new_rows)
    raw["images"] = [i for i in raw.get("images", [])
                     if not (isinstance(i, dict) and i.get("image_id") == image_id)]

    # 归档文件名按契约算（契约里写着 name_pattern，以前没人读它，等于白写）
    sheet_label = ve.to_display(frag.get("sheet_label"))
    first_fields = {}
    for r in new_rows:
        if ve.to_display(r.get("row_role")) == "data" and r.get("fields"):
            first_fields = r["fields"]
            break
    if not first_fields and new_rows:
        first_fields = new_rows[0].get("fields") or {}
    sch_for_name = ve.schema_for(schemas, sheet_label) if sheet_label else schemas.get("*")
    seq = 1 + len([i for i in raw["images"]
                   if isinstance(i, dict)
                   and ve.to_display(i.get("sheet_label")) == sheet_label
                   and ve.to_display(i.get("date_text")) == date_text])
    name, unused = suggest_name(sch_for_name, date_text, sheet_label, first_fields, seq)

    entry = {
        "image_id": image_id,
        "file": item.get("file", ""),
        "sha256": item.get("sha256", ""),
        "sheet_label": sheet_label,
        "date_text": date_text,
    }
    if name:
        entry["suggested_name"] = name
    raw["images"].append(entry)
    raw["created"] = raw.get("created") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 原子写：先写 .tmp 再替换，写到一半断电也不会留下半个 raw.json
    raw_path = os.path.join(run_dir, "raw.json")
    tmp_path = raw_path + ".tmp"
    ve.write_json(tmp_path, raw)
    os.replace(tmp_path, raw_path)

    print("  ✓ 已落盘：%d 行数据" % len(new_rows))
    print("  ✓ 脚本补的：row_id、value（由 raw_value 换算）、required_fields、unit")
    if name:
        print("  ✓ 归档名（按契约 %s 算）：%s" % (sch_for_name.get("template", "?"), name))
        if unused:
            print("      ⚠ 契约里的这些占位符在这张图上找不到对应的值，已从名字里去掉了：%s"
                  % "、".join(unused))
    comp_conv = [(c["name"], c["raw_value"], c["value"])
                 for r in new_rows for c in r["components"] if c["raw_value"]]
    if comp_conv:
        print("     换算示例：" + "；".join("%s %s -> %s" % t for t in comp_conv[:4]))

    # 当场把裁判的判定结果报出来（同一套规则）
    issues = referee_feedback(raw, BASE)
    mine = [i for i in issues if image_id in json.dumps(i, ensure_ascii=False)]
    print()
    if mine:
        print("  ⚠ 裁判对这张图报了 %d 条疑点（**已记账，最后统一复核，不要停下来问用户**）：" % len(mine))
        for i in mine[:12]:
            print("      · [%s] %s" % (i.get("severity", "?"), i.get("description", "")))
        if len(mine) > 12:
            print("      …… 还有 %d 条，全部记在最后生成的 issues.json 里" % (len(mine) - 12))
    else:
        print("  ✓ 裁判对这张图没报疑点。")

    pending, done = pending_list(manifest, raw)
    print()
    print("  进度：%d / %d 张已识别" % (len(done), len(manifest.get("images", []))))
    if pending:
        print("  还剩：%s" % "、".join(p.get("image_id", "?") for p in pending))
        print("  下一张：python tools/提交识别.py \"%s\" --next" % os.path.relpath(run_dir, BASE))
    else:
        print("  全部识别完。下一步：")
        print("    python validate_export.py \"%s\" --init-issues" % os.path.relpath(run_dir, BASE))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="提交一张图的识别结果（AI 只交片段，编号/换算/合并/当场校验由脚本做）")
    ap.add_argument("run_dir", help="任务目录，如 runs/20260910_003")
    ap.add_argument("--next", action="store_true", help="显示下一张待识别的图 + 该填的格式")
    ap.add_argument("--list", action="store_true", help="显示识别进度")
    ap.add_argument("--image", help="图片编号（image_id）")
    ap.add_argument("--file", help="识别片段 JSON 文件")
    ap.add_argument("--stdin", action="store_true", help="从标准输入读识别片段 JSON")
    ap.add_argument("--replace", action="store_true", help="这张图已交过，确认覆盖重交")
    args = ap.parse_args()

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir:
        print("[错误] 找不到任务目录：%s（先跑 python tools/建任务.py 建任务）" % args.run_dir)
        return 2

    manifest = load_manifest(run_dir)
    if manifest is None:
        print("[错误] 这个任务没有 source/names.json 台账。")
        print("       台账由 tools/建任务.py 生成 —— 请用它建任务，不要手工造任务目录。")
        return 2

    raw = load_raw(run_dir)
    if raw is None:
        return 2

    schemas = ve.load_schemas(BASE)

    if args.next:
        return cmd_next(run_dir, manifest, raw, schemas)
    if args.list:
        return cmd_list(run_dir, manifest, raw)

    if not args.image:
        print("[错误] 要么加 --next 看下一张，要么用 --image 指定图片编号")
        return 2
    if not args.file and not args.stdin:
        print("[错误] 还要给出片段内容：--file 片段.json 或 --stdin")
        return 2

    try:
        if args.stdin:
            frag = json.loads(sys.stdin.read())
        else:
            path = args.file if os.path.isabs(args.file) else os.path.join(os.getcwd(), args.file)
            if not os.path.isfile(path):
                path = os.path.join(BASE, args.file)
            frag = load_text_json(path)
    except Exception as e:
        print("[错误] 片段读不了或不是合法 JSON：%s" % e)
        return 2

    return cmd_submit(run_dir, manifest, raw, schemas, args.image.strip(), frag, args.replace)


if __name__ == "__main__":
    sys.exit(main())
