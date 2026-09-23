# -*- coding: utf-8 -*-
"""生成样板 —— 把"AI 打算怎么录"变成一份给人看的、可勾选确认的 Excel。

为什么需要它：
    过去是让 AI 用一张问卷（"这张表一行代表什么？有哪些列？"）去问用户，
    对车间员工来说太抽象。更好的办法是：**先拿一张真实图做出来给他看**，
    他看一眼就知道对不对——"对，就这么录"。

用法（在工作区根目录）：
    python tools/生成样板.py runs/YYYYMMDD_NNN                 # 自动挑第一张已识别的图
    python tools/生成样板.py runs/YYYYMMDD_NNN IMG_0001        # 指定用哪张图做样板

产出两个文件（都放在该任务目录下）：
    样板.xlsx          给人看：录入方案 + 填好的范例 + 确认栏（可以打印出来签字）
    样板_契约草案.json  给 AI 用：从这张样板推断出的版式契约草案

**它只读 raw.json，不改任何数据、不写工作区其它地方。**
"""
import io
import json
import os
import re
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ------------------------------------------------------------------ 读入
def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def to_text(v):
    return "" if v is None else str(v).strip()


# ------------------------------------------------------------------ 推断
def guess_type(values):
    """从一列的实际取值猜类型——只是**草案**，最终由用户确认。"""
    vals = [to_text(v) for v in values if to_text(v)]
    if not vals:
        return "text"
    date_like = re.compile(r"\d{4}\s*[-/年.]\s*\d{1,2}\s*[-/月.]\s*\d{1,2}")
    if all(date_like.search(v) for v in vals):
        return "date"
    if all(("%" in v or "％" in v) for v in vals):
        return "percent"
    if all(re.match(r"^[+-]?\d+(\.\d+)?$", v.replace(",", "")) for v in vals):
        return "number"
    # 编号：纯 ASCII 的字母数字/连字符短串。含中文的一律算文本——
    # 否则「买办公用品」这种会被误判成编号。
    if all(re.match(r"^[A-Za-z0-9\-_/\.]{1,16}$", v) for v in vals):
        return "identifier"
    return "text"


def load_schemas(root):
    out = {}
    d = os.path.join(root, "schemas")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json"):
                data = read_json(os.path.join(d, fn), {})
                if isinstance(data, dict) and data.get("template"):
                    out[to_text(data.get("sheet_label")) or "*"] = data
    return out


def find_schema(schemas, sheet):
    s = to_text(sheet)
    if s and s in schemas:
        return schemas[s], True
    return schemas.get("*", {}), False


def build_rows(run, image_id):
    """取出这张图对应的数据行（保持原顺序）。"""
    rows = []
    for r in run.get("rows", []):
        if image_id and to_text(r.get("image_id")) != image_id:
            continue
        rows.append(r)
    return [r for r in rows if to_text(r.get("row_role")) in ("", "data")]


def draft_schema(rows, run, sheet_label, existing):
    """从样板行推断一份**契约草案**。已有契约时只做补充说明，不覆盖。"""
    if existing:
        return existing
    # 列：所有出现过的字段，按首次出现顺序
    order, values = [], {}
    required = set()
    for r in rows:
        for f in (r.get("required_fields") or []):
            required.add(to_text(f))
        for k, v in (r.get("fields") or {}).items():
            if k not in values:
                order.append(k)
                values[k] = []
            values[k].append(v)
    columns = [{"name": k, "type": guess_type(values[k]),
                "required": k in required} for k in order]

    # 计量项
    comps = []
    for r in rows:
        for c in (r.get("components") or []):
            if isinstance(c, dict) and to_text(c.get("name")):
                comps.append(c)
    unit = to_text(comps[0].get("unit")) if comps else "%"
    meas = {"enabled": bool(comps), "unit": unit or "%",
            "categories": {"water": "never", "misc": "if_present"},
            "default_category": "always", "sum_max": 100.0}

    # 词表：把样板里出现过的计量项名按"像水 / 像杂"粗分，其余交给用户确认
    vocab = {"water": [], "misc": []}
    for c in comps:
        n = to_text(c.get("name"))
        if not n:
            continue
        if any(w in n for w in ("水", "含水")):
            vocab["water"].append(n)
        elif any(w in n for w in ("杂", "渣", "其他", "残余")):
            vocab["misc"].append(n)

    # 事实表列：沿用样板的字段名
    fact_keys = {}
    for out_col, hint in (("record_date", "日期"), ("location", "位置"),
                          ("batch_id", "批次"), ("container_id", "桶号"),
                          ("material_name", "物料"), ("supplier", "厂商")):
        if hint in order:
            fact_keys[out_col] = hint

    return {
        "template": "%s_v1" % (sheet_label or "新表型"),
        "sheet_label": sheet_label or "*",
        "row_unit": "（请确认：这张表一行代表什么？）",
        "columns": columns,
        "measurements": meas,
        "vocabulary": vocab,
        "fact_keys": fact_keys,
        "archive": {"name_pattern": None},
        "_草案说明": "本文件由 tools/生成样板.py 从样板图自动推断，**仅供参考**；"
                    "用户确认样板后，请据此整理成正式契约写入 schemas/。"
    }


# ------------------------------------------------------------------ 输出
def write_sample_xlsx(run, rows, schema, is_registered, sheet_label, out_path, image_id):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDEBF7")
    warn_fill = PatternFill("solid", fgColor="FFF2CC")
    ok_fill = PatternFill("solid", fgColor="E2EFDA")
    wrap = Alignment(wrap_text=True, vertical="top")

    # --- 1) 录入方案
    ws = wb.active
    ws.title = "1-录入方案"
    ws.append(["这一张图，我打算这样录"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(["项目", "内容"])
    for c in ws[2]:
        if c.value:
            c.font = bold
            c.fill = head_fill
    meas = schema.get("measurements") or {}
    cols = schema.get("columns") or []
    meas_on = meas.get("enabled", True)
    plan = [
        ("表型名", schema.get("template") or "（未注册）"),
        ("图上的表名", sheet_label or "（图上没写）"),
        ("一行代表", schema.get("row_unit") or "（待确认）"),
        ("有几列", "、".join(c.get("name", "") for c in cols) or "（未声明，以实际读到的列为准）"),
        ("哪些列不能空", "、".join(c["name"] for c in cols if c.get("required")) or "（未声明）"),
        ("有没有合计", "有" if meas_on else "没有"),
    ]
    if meas_on:
        limit = meas.get("sum_max", 100.0)
        cats = meas.get("categories") or {}
        desc = []
        for k, v in cats.items():
            desc.append("%s：%s" % (k, {"never": "不计入合计",
                                        "if_present": "有值才计入",
                                        "always": "必须计入"}.get(v, v)))
        plan += [
            ("合计上限", "%s%s" % (limit, meas.get("unit", "%"))),
            ("哪些不计入合计", "；".join(desc) or "（无）"),
        ]
    vocab = schema.get("vocabulary") or {}
    for k, words in vocab.items():
        if words:
            plan.append(("同类词（%s）" % k, "、".join(words)))
    plan.append(("契约状态", "已注册，直接按它录" if is_registered else "⚠ 还没注册——确认样板后才写入 schemas/"))
    for k, v in plan:
        ws.append([k, v])
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 86
    for row in ws.iter_rows(min_row=3):
        row[1].alignment = wrap

    # --- 2) 样本数据
    ws2 = wb.create_sheet("2-样本数据")
    ws2.append(["这张图读出来的内容（空白=图上就是空的）"])
    ws2["A1"].font = Font(bold=True, size=14)
    declared = [c.get("name") for c in cols if c.get("name")]
    extra = []
    for r in rows:
        for k in (r.get("fields") or {}):
            if k not in declared and k not in extra:
                extra.append(k)
    headers = ["行号", "对象"] + declared + extra
    ws2.append([])
    ws2.append(headers)
    for c in ws2[3]:
        c.font = bold
        c.fill = head_fill
    for r in rows:
        fields = r.get("fields") or {}
        ws2.append([to_text(r.get("row_id")), to_text(r.get("object_label"))] +
                   [to_text(fields.get(h)) for h in declared + extra])
    ws2.column_dimensions["A"].width = 8
    ws2.column_dimensions["B"].width = 18
    for i in range(3, 3 + len(declared + extra)):
        ws2.column_dimensions[ws2.cell(row=3, column=i).column_letter].width = 14

    # --- 3) 计量项明细（如果这张表有）
    ws3 = wb.create_sheet("3-计量项")
    if any((r.get("components") or []) for r in rows):
        ws3.append(["行号", "计量项", "图上原文", "换算值", "单位"])
        for c in ws3[1]:
            c.font = bold
            c.fill = head_fill
        for r in rows:
            for comp in (r.get("components") or []):
                ws3.append([to_text(r.get("row_id")), to_text(comp.get("name")),
                            to_text(comp.get("raw_value")), to_text(comp.get("value")),
                            to_text(comp.get("unit"))])
        ws3.column_dimensions["A"].width = 8
        ws3.column_dimensions["B"].width = 20
        ws3.column_dimensions["C"].width = 14
        ws3.column_dimensions["D"].width = 14
        ws3.column_dimensions["E"].width = 8
    else:
        ws3.append(["（这张表没有需要合计的计量项）"])

    # --- 4) 看不清的地方
    ws4 = wb.create_sheet("4-看不清的地方")
    unc = []
    for r in rows:
        for u in (r.get("uncertain") or []):
            unc.append([to_text(r.get("row_id")), to_text(u.get("field")),
                        " / ".join(to_text(c) for c in (u.get("candidates") or [])),
                        to_text(u.get("note"))])
    ws4.append(["行号", "哪个格子", "图上可能是", "说明"])
    for c in ws4[1]:
        c.font = bold
        c.fill = head_fill
    for u in unc:
        ws4.append(u)
    ws4.column_dimensions["A"].width = 8
    ws4.column_dimensions["B"].width = 16
    ws4.column_dimensions["C"].width = 26
    ws4.column_dimensions["D"].width = 30
    if not unc:
        ws4.append(["（这张图上没有看不清的地方）"])

    # --- 5) 确认栏
    ws5 = wb.create_sheet("5-请在这里确认")
    ws5.append(["请看一下前面几页，然后在这里给个话"])
    ws5["A1"].font = Font(bold=True, size=14)
    ws5.append([])
    ws5.append(["要确认的事", "你的答复（填在右边）", "举例"])
    for c in ws5[3]:
        c.font = bold
        c.fill = head_fill
    items = [
        ("这个录法对不对？", "", "对 / 不对，应该……"),
        ("一行代表什么，对吗？", "", "对 / 应该是一张单子一行"),
        ("列的名字和顺序行不行？", "", "行 / 把「桶号」改成「罐号」"),
        ("有没有哪一列是必须有的？", "", "日期和物料必须有"),
        ("有没有合计？上限多少？", "", "有，不能超过 100 / 没有合计"),
        ("哪些词其实是一回事？", "", "含水量=水分=水"),
        ("看不清的那几个格子，值是什么？", "", "乙酸是 0.5%"),
    ]
    for it in items:
        ws5.append(list(it))
    ws5.append([])
    ws5.append(["确认人", "", ""])
    ws5.append(["确认日期", "", ""])
    ws5.column_dimensions["A"].width = 34
    ws5.column_dimensions["B"].width = 40
    ws5.column_dimensions["C"].width = 34
    for row in ws5.iter_rows(min_row=4):
        for cell in row:
            cell.alignment = wrap
    for r in range(4, 4 + len(items)):
        ws5.cell(row=r, column=2).fill = warn_fill
    ws5.cell(row=4 + len(items) + 2, column=2).fill = ok_fill

    wb.save(out_path)


def main():
    argv = sys.argv[1:]
    if not argv or "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0 if argv else 1

    run_dir = os.path.abspath(argv[0])
    root = os.path.dirname(os.path.dirname(run_dir)) \
        if os.path.basename(os.path.dirname(run_dir)) == "runs" else os.getcwd()
    if not os.path.isdir(run_dir):
        print("[错误] 目录不存在：" + run_dir)
        return 1

    run = read_json(os.path.join(run_dir, "raw.json"))
    if not run:
        print("[错误] 读不到 raw.json（或格式坏了）：" + run_dir)
        print("       样板必须是**已经识别过**的一张图。")
        return 1

    images = run.get("images") or []
    want = to_text(argv[1]) if len(argv) > 1 else ""
    if want:
        target = next((im for im in images if to_text(im.get("image_id")) == want), None)
        if target is None:
            print("[错误] raw.json 里没有 image_id = %s 的图" % want)
            return 1
    else:
        target = images[0] if images else None
    if target is None:
        print("[错误] raw.json 里一张图都没有，没法做样板。")
        return 1

    image_id = to_text(target.get("image_id"))
    sheet_label = to_text(target.get("sheet_label")) or \
        to_text(next((r.get("sheet") for r in run.get("rows", [])
                      if to_text(r.get("image_id")) == image_id), ""))
    rows = build_rows(run, image_id)
    if not rows:
        print("[提示] 这张图还没识别出任何数据行（raw.json 里没有它的行），样板会是空的。")

    schemas = load_schemas(root)
    schema, registered = find_schema(schemas, sheet_label)
    draft = draft_schema(rows, run, sheet_label, schema if registered else None)

    xlsx = os.path.join(run_dir, "样板.xlsx")
    write_sample_xlsx(run, rows, draft, registered, sheet_label, xlsx, image_id)

    draft_path = os.path.join(run_dir, "样板_契约草案.json")
    with io.open(draft_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(draft, f, ensure_ascii=False, indent=1)

    print("=" * 60)
    print("  样板已生成")
    print("=" * 60)
    print("任务：      " + os.path.basename(run_dir))
    print("样板图：    %s（%s）" % (image_id, os.path.basename(to_text(target.get("file")))))
    print("图上表名：  " + (sheet_label or "（没写）"))
    print("识别出：    %d 行数据" % len(rows))
    print("契约状态：  " + ("已注册（%s）" % draft.get("template") if registered
                          else "⚠ 未注册，草案已生成，等用户确认样板后写入 schemas/"))
    print("")
    print("给人看：    " + os.path.relpath(xlsx, root))
    print("给 AI 用：  " + os.path.relpath(draft_path, root))
    print("")
    print("下一步：把「样板.xlsx」交给用户，请他在第 5 页写确认意见。")
    print("        确认无误后，把草案整理成 schemas/<表型名>_v1.json，再开始批量。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
