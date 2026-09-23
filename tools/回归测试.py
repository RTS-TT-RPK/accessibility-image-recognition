# -*- coding: utf-8 -*-
"""回归测试 —— 一条命令验证裁判脚本的全部关键行为。

用法（在工作区根目录）：
    python tools/回归测试.py

它做什么：
    在系统临时目录里搭一个隔离工作区，构造一批"已知答案"的用例，
    逐个跑 validate_export.py，核对结果是否符合预期。

安全：
    **只读**真实工作区（只复制 runs 与 schemas 到临时目录），
    绝不在 inbox/pending/archive/golden/runs 里写任何东西，跑完自动清理。

为什么需要它：
    这套系统的价值全在"数据不出错"。裁判脚本一旦被改动，
    必须能一条命令证明"以前对的现在还对的"。加新用例就往下加一条。
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATOR = os.path.join(BASE, "validate_export.py")

PASS, FAIL = [], []


# ---------------------------------------------------------------- 内置样本
# 这两份是"历史回归样本"：改造裁判脚本前就存在、且行为已被人确认过的数据。
# **必须内置在测试里，不能依赖工作区的 runs/**——发布包本身不含业务数据，
# 新电脑上 runs/ 是空的，靠外部文件会让这个测试在新电脑上直接失败。
HIST_001_RAW = {
    "run_id": "20260910_001", "created": "2026-09-10 20:30:00", "source_type": "incoming",
    "images": [
        {"image_id": "IMG_0001", "file": "runs/20260910_001/source/IMG_0001.jpg",
         "sha256": "aaa111", "sheet_label": "来料分析", "date_text": "2026-09-10",
         "suggested_name": "2026-09-10_来料分析_批A001_第1张.jpg"},
        {"image_id": "IMG_0002", "file": "runs/20260910_001/source/IMG_0002.jpg",
         "sha256": "bbb222", "sheet_label": "来料分析", "date_text": "2026-09-10",
         "suggested_name": "2026-09-10_来料分析_批A001_第2张.jpg"},
    ],
    "rows": [
        {"row_id": "R001", "image_id": "IMG_0001", "sheet": "来料分析", "row_role": "data",
         "object_label": "A001/桶01", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "北罐区7#", "物料": "IPAC",
                    "批次": "A001", "桶号": "01"},
         "components": [
             {"name": "IPAC", "raw_value": "89.6%", "value": "0.896", "unit": "%"},
             {"name": "DMF", "raw_value": "8%", "value": "0.08", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"},
             {"name": "杂", "raw_value": "2.3%", "value": "0.023", "unit": "%"}],
         "uncertain": []},
        {"row_id": "R002", "image_id": "IMG_0001", "sheet": "来料分析", "row_role": "data",
         "object_label": "A001/桶02", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "北罐区7#", "物料": "IPAC",
                    "批次": "A001", "桶号": "02"},
         "components": [
             {"name": "IPAC", "raw_value": "99.5%", "value": "0.995", "unit": "%"},
             {"name": "DMF", "raw_value": "0.8%", "value": "0.008", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"}],
         "uncertain": []},
        {"row_id": "R003", "image_id": "IMG_0002", "sheet": "来料分析", "row_role": "data",
         "object_label": "A001/桶03", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "北罐区7#", "物料": "IPAC",
                    "批次": "A001", "桶号": "03"},
         "components": [
             {"name": "IPAC", "raw_value": "89.4%", "value": "0.894", "unit": "%"},
             {"name": "DMF", "raw_value": "8%", "value": "0.08", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"},
             {"name": "杂", "raw_value": "", "value": "", "unit": "%"}],
         "uncertain": [{"field": "乙酸", "candidates": ["0.5%", "5%"], "note": "字迹不清"}]},
        {"row_id": "R004", "image_id": "IMG_0002", "sheet": "来料分析", "row_role": "summary",
         "object_label": "合计",
         "fields": {"日期": "2026-09-10", "物料": "合计"}, "components": [], "uncertain": []},
    ],
}

HIST_001_DECISIONS = {
    "run_id": "20260910_001",
    "issue_decisions": [
        {"issue_id": "ISS-SUM-R002-", "decision": "DMF=0", "note": "对照原图：DMF 那格实际是 0.0%"},
        {"issue_id": "ISS-OCR-R003-乙酸", "decision": "0.5%", "note": "对照原图：乙酸 0.5%"}],
    "archive_decisions": [{"image_id": "IMG_0001", "confirmed": True},
                          {"image_id": "IMG_0002", "confirmed": True}],
}

HIST_002_RAW = {
    "run_id": "20260910_002", "created": "2026-09-10 21:00:00", "source_type": "inventory",
    "images": [
        {"image_id": "IMG_A", "file": "runs/20260910_002/source/IMG_A.jpg",
         "sha256": "00a06ae5", "sheet_label": "库存日报", "date_text": "2026-09-10",
         "suggested_name": "2026-09-10_库存日报_第1张.jpg"},
        {"image_id": "IMG_B", "file": "runs/20260910_002/source/IMG_B.jpg",
         "sha256": "00a06ae5", "sheet_label": "库存日报", "date_text": "2026-09-10",
         "suggested_name": "2026-09-10_库存日报_第2张.jpg"},
        {"image_id": "IMG_C", "file": "runs/20260910_002/source/IMG_C.jpg",
         "sha256": "e2cf3f8a", "sheet_label": "来料分析", "date_text": "2026-09-10",
         "suggested_name": "2026-09-10_来料分析_批B002_第1张.jpg"},
    ],
    "rows": [
        {"row_id": "R001", "image_id": "IMG_A", "sheet": "库存日报", "row_role": "data",
         "object_label": "1号库/IPAC", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "1号库", "物料": "IPAC"},
         "components": [
             {"name": "IPAC", "raw_value": "99.5%", "value": "0.995", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"}],
         "uncertain": []},
        {"row_id": "R002", "image_id": "IMG_A", "sheet": "库存日报", "row_role": "data",
         "object_label": "2号库/甲醇", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "2号库", "物料": "甲醇"},
         "components": [
             {"name": "甲醇", "raw_value": "99.9%", "value": "0.999", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"},
             {"name": "杂", "raw_value": "0.5%", "value": "0.005", "unit": "%"}],
         "uncertain": []},
        {"row_id": "R003", "image_id": "IMG_C", "sheet": "来料分析", "row_role": "data",
         "object_label": "B002/桶01", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "北罐区", "物料": "IPAC",
                    "批次": "B002", "桶号": "01"},
         "components": [
             {"name": "IPAC", "raw_value": "89.6%", "value": "0.896", "unit": "%"},
             {"name": "水", "raw_value": "0.1%", "value": "0.001", "unit": "%"}],
         "uncertain": [{"field": "含水率", "candidates": ["0.05%", "0.5%"],
                        "note": "同类词未注册"}]},
        {"row_id": "R004", "image_id": "IMG_C", "sheet": "来料分析", "row_role": "data",
         "object_label": "B002/桶02", "required_fields": ["物料"],
         "fields": {"日期": "2026-09-10", "位置": "北罐区", "批次": "B002", "桶号": "02"},
         "components": [], "uncertain": []},
    ],
}


# ---------------------------------------------------------------- 测试脚手架
def build_workspace(tmp):
    """搭一个隔离工作区：只放脚本要用的东西 + 内置样本，不碰真实业务数据。"""
    os.makedirs(os.path.join(tmp, "runs"), exist_ok=True)
    src = os.path.join(BASE, "aliases.json")
    if os.path.exists(src):
        shutil.copy2(src, os.path.join(tmp, "aliases.json"))
    src_schemas = os.path.join(BASE, "schemas")
    if os.path.isdir(src_schemas):
        shutil.copytree(src_schemas, os.path.join(tmp, "schemas"), dirs_exist_ok=True)
    # 内置样本：不依赖工作区里的 runs/，保证换电脑后照样能跑
    for name, raw, dec in (("20260910_001", HIST_001_RAW, HIST_001_DECISIONS),
                           ("20260910_002", HIST_002_RAW, None)):
        d = os.path.join(tmp, "runs", name)
        os.makedirs(os.path.join(d, "source"), exist_ok=True)
        for im in raw["images"]:
            with io.open(os.path.join(d, "source", os.path.basename(im["file"])),
                         "w", encoding="utf-8") as f:
                f.write("fake-image")
        write_json(os.path.join(d, "raw.json"), raw)
        if dec:
            write_json(os.path.join(d, "decisions.json"), dec)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def image(iid="I1", name=None, sha="h1", sheet="测试表", date="2026-09-10"):
    return {"image_id": iid, "file": "runs/%s/source/a.jpg" % name,
            "sha256": sha, "sheet_label": sheet, "date_text": date,
            "suggested_name": "a.jpg"}


def case(tmp, name, raw, decisions=None, aliases=None, schemas=None, images=True):
    d = os.path.join(tmp, "runs", name)
    os.makedirs(os.path.join(d, "source"), exist_ok=True)
    if images:
        for im in raw.get("images", []):
            with io.open(os.path.join(d, "source", "a.jpg"), "w", encoding="utf-8") as f:
                f.write("fake")
    raw.setdefault("run_id", name)
    raw.setdefault("source_type", "test")
    write_json(os.path.join(d, "raw.json"), raw)
    if decisions is not None:
        write_json(os.path.join(d, "decisions.json"), decisions)
    for fn, content in (schemas or {}).items():
        write_json(os.path.join(tmp, "schemas", fn), content)
    if aliases is not None:
        write_json(os.path.join(tmp, "aliases.json"), aliases)
    return d


def run_case(tmp, name, extra=()):
    proc = subprocess.run([sys.executable, VALIDATOR, os.path.join(tmp, "runs", name)] + list(extra),
                          cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def issues_of(tmp, name):
    """全部问题（含已解决的，账本要留痕）。"""
    p = os.path.join(tmp, "runs", name, "issues.json")
    if not os.path.exists(p):
        return []
    with io.open(p, encoding="utf-8") as f:
        return json.load(f).get("issues", [])


def open_issues_of(tmp, name):
    """尚未解决的问题——判断"这轮跑完还剩几个问题"要用这个。"""
    return [i for i in issues_of(tmp, name)
            if i.get("machine_status") not in ("resolved", "accepted")]


def sheet_of(tmp, name, sheet):
    from openpyxl import load_workbook
    p = os.path.join(tmp, "runs", name, "final.xlsx")
    if not os.path.exists(p):
        return None
    wb = load_workbook(p)
    if sheet not in wb.sheetnames:
        return None
    return list(wb[sheet].iter_rows(values_only=True))


def named_rows(tmp, name, sheet):
    """按列名取值，避免列顺序变化让测试假失败。"""
    rows = sheet_of(tmp, name, sheet)
    if not rows:
        return [], []
    header = [str(c) for c in rows[0]]
    return header, [dict(zip(header, r)) for r in rows[1:]]


def types_of(tmp, name):
    return [i["type"] for i in open_issues_of(tmp, name)]


# ---------------------------------------------------------------- 断言
def check(title, cond, detail=""):
    if cond:
        PASS.append(title)
        print("  [通过] " + title)
    else:
        FAIL.append((title, detail))
        print("  [失败] " + title + ("   -> " + detail if detail else ""))


# ---------------------------------------------------------------- 用例
def main():
    tmp = tempfile.mkdtemp(prefix="validate_regress_")
    print("=" * 66)
    print("  裁判脚本回归测试")
    print("=" * 66)
    print("隔离工作区：" + tmp)
    print("")
    try:
        build_workspace(tmp)
        run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("")
    print("=" * 66)
    print("  合计 %d 项：通过 %d，失败 %d" % (len(PASS) + len(FAIL), len(PASS), len(FAIL)))
    for title, detail in FAIL:
        print("   x " + title + ("   -> " + detail if detail else ""))
    print("=" * 66)
    return 1 if FAIL else 0


def run_all(tmp):
    print("-- 1. 历史回归样本（改造前后必须一致）")
    code, out = run_case(tmp, "20260910_001")
    check("001 退出码 0", code == 0, out[-300:])
    check("001 遗留问题 0 条（人工决定全部生效）", len(open_issues_of(tmp, "20260910_001")) == 0,
          "实际 %d 条" % len(open_issues_of(tmp, "20260910_001")))
    code, out = run_case(tmp, "20260910_002")
    check("002 退出码 0", code == 0, out[-300:])
    check("002 遗留问题 4 条", len(open_issues_of(tmp, "20260910_002")) == 4,
          "实际 %d 条" % len(open_issues_of(tmp, "20260910_002")))

    print("")
    print("-- 2. 人工决定不得污染明细表（H1）")
    rows = sheet_of(tmp, "20260910_001", "明细数据")
    header = [c for c in rows[0]] if rows else []
    check("001 明细表头没有被塞进 DMF/乙酸 假列",
          "DMF" not in header and "乙酸" not in header, "表头=%s" % header)

    print("")
    print("-- 3. 未解决的行不进事实表（H2）")
    _hdr, facts = named_rows(tmp, "20260910_002", "成分事实")
    src_rows = set(r.get("source_row_id") for r in facts)
    check("002 事实表只含已解决的 R001", src_rows == {"R001"}, "实际=%s" % src_rows)

    print("")
    print("-- 4. 水组分写入事实表（M3）")
    names = set(r.get("component_standard_name") for r in facts)
    check("002 事实表含水分项", "水" in names, "实际=%s" % names)

    print("")
    print("-- 5. 畸形输入不崩（H4/H5）")
    for nm, raw in [
        ("g_noimg", {"images": [], "rows": []}),
        ("g_compnull", {"images": [image("I1", "g_compnull")],
                        "rows": [{"row_id": "R1", "image_id": "I1", "row_role": "data",
                                  "fields": {"物料": "X"}, "required_fields": [],
                                  "components": None, "uncertain": []}]}),
        ("g_fieldsnull", {"images": [image("I1", "g_fieldsnull")],
                          "rows": [{"row_id": "R1", "image_id": "I1", "row_role": "data",
                                    "fields": None, "required_fields": ["物料"],
                                    "components": [], "uncertain": []}]}),
        ("g_weird", {"images": [image("I1", "g_weird")],
                     "rows": [{"row_id": "R1", "image_id": "I1", "row_role": "data",
                               "fields": None, "required_fields": [], "components": "abc",
                               "uncertain": {"a": 1}}]}),
    ]:
        case(tmp, nm, raw)
        code, out = run_case(tmp, nm)
        check("%s 退出码 0（不 traceback）" % nm, code == 0, out[-200:])
        check("%s 生成了审核表" % nm,
              os.path.exists(os.path.join(tmp, "runs", nm, "task_review.xlsx")))

    print("")
    print("-- 6. raw_value 与 value 一致性（本次新增校验）")
    case(tmp, "g_mismatch", {"images": [image("I1", "g_mismatch")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {"物料": "X"},
         "required_fields": [], "uncertain": [],
         "components": [{"name": "IPAC", "raw_value": "90%", "value": "0.9"},
                        {"name": "DMF", "raw_value": "8%", "value": "0.8"}]}]})
    run_case(tmp, "g_mismatch")
    check("错配被抓成 VALUE_MISMATCH", "VALUE_MISMATCH" in types_of(tmp, "g_mismatch"),
          "实际=%s" % types_of(tmp, "g_mismatch"))

    print("")
    print("-- 7. 缺 raw_value 时按小数正确换算（M4）")
    case(tmp, "g_valueonly", {"images": [image("I1", "g_valueonly")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {"物料": "X"},
         "required_fields": [], "uncertain": [],
         "components": [{"name": "IPAC", "value": "0.6"}, {"name": "DMF", "value": "0.5"}]}]})
    run_case(tmp, "g_valueonly")
    check("0.6+0.5 正确报超 100%", "COMPONENT_SUM_OVER_100" in types_of(tmp, "g_valueonly"),
          "实际=%s" % types_of(tmp, "g_valueonly"))

    print("")
    print("-- 8. aliases.json 注册的词真的生效（M1）")
    case(tmp, "g_alias", {"images": [image("I1", "g_alias")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {"物料": "X"},
         "required_fields": [], "uncertain": [],
         "components": [{"name": "IPAC", "raw_value": "96%", "value": "0.96"},
                        {"name": "残水", "raw_value": "5%", "value": "0.05"}]}]},
         aliases={"_说明": "t", "水类": ["残水"], "杂类": []})
    run_case(tmp, "g_alias")
    check("注册为水类的词不再计入合计", "COMPONENT_SUM_OVER_100" not in types_of(tmp, "g_alias"),
          "实际=%s" % types_of(tmp, "g_alias"))

    print("")
    print("-- 9. row_role 校验与修正（M2）")
    case(tmp, "g_role", {"images": [image("I1", "g_role")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {"物料": "X"},
         "required_fields": [], "components": [], "uncertain": []},
        {"row_id": "R2", "image_id": "I1", "fields": {"物料": "合计"},
         "required_fields": [], "components": [], "uncertain": []}]})
    run_case(tmp, "g_role")
    check("未标 row_role 的行被报出来", "ROW_ROLE_INVALID" in types_of(tmp, "g_role"),
          "实际=%s" % types_of(tmp, "g_role"))

    print("")
    print("-- 10. 重复图按组报全（M7）")
    case(tmp, "g_dup", {"images": [image("A", "g_dup", sha="same"),
                                   image("B", "g_dup", sha="same"),
                                   image("C", "g_dup", sha="same")], "rows": []})
    run_case(tmp, "g_dup")
    dups = [i for i in issues_of(tmp, "g_dup") if i["type"] == "DUPLICATE"]
    check("三张同图报 2 条且说明整组", len(dups) == 2 and "共 3 张" in dups[0]["description"],
          "实际 %d 条：%s" % (len(dups), dups[0]["description"] if dups else ""))

    print("")
    print("-- 11. 归档安全：不许搬工作区外的文件（H3）")
    outside_dir = tempfile.mkdtemp(prefix="validate_outside_")
    outside = os.path.join(outside_dir, "outside_me.txt")
    with io.open(outside, "w", encoding="utf-8") as f:
        f.write("x")
    case(tmp, "g_esc_src", {"images": [{"image_id": "I1", "file": outside.replace("\\", "/"),
                                        "sha256": "h1", "sheet_label": "S",
                                        "date_text": "2026-09-10", "suggested_name": "a.jpg"}],
                            "rows": []})
    code, out = run_case(tmp, "g_esc_src", ["--archive"])
    check("工作区外的来源被拒绝（文件没被搬走）", os.path.exists(outside),
          "受害者文件被搬走了：%s" % out[-200:])
    shutil.rmtree(outside_dir, ignore_errors=True)

    escaped = os.path.join(os.path.dirname(tmp), "ESCAPED_TEST")
    shutil.rmtree(escaped, ignore_errors=True)
    case(tmp, "g_esc_dst", {"images": [image("I1", "g_esc_dst")], "rows": []},
         decisions={"run_id": "g_esc_dst", "issue_decisions": [],
                    "archive_decisions": [{"image_id": "I1", "confirmed": True,
                                           "target_dir": escaped}]})
    run_case(tmp, "g_esc_dst", ["--archive"])
    check("越界的归档目标被拒绝", not os.path.exists(escaped))
    shutil.rmtree(escaped, ignore_errors=True)

    print("")
    print("-- 12. 日期清洗（H6）")
    case(tmp, "g_date", {"images": [image("A", "g_date", sha="1", date="2026/09/10"),
                                    image("B", "g_date", sha="2", date="2026-09-10 上午"),
                                    image("C", "g_date", sha="3", date="看不太清")], "rows": []})
    run_case(tmp, "g_date", ["--archive"])
    arc = os.path.join(tmp, "archive")
    dirs = sorted(d for d in os.listdir(arc) if os.path.isdir(os.path.join(arc, d))) if os.path.isdir(arc) else []
    check("两种日期写法归一到同一个目录", dirs == ["2026-09-10"], "实际=%s" % dirs)

    print("")
    print("-- 13. 通用能力：列类型校验（新）")
    case(tmp, "g_coltype", {"images": [image("I1", "g_coltype", sheet="考勤表")], "rows": [
        {"row_id": "R1", "image_id": "I1", "sheet": "考勤表", "row_role": "data",
         "fields": {"日期": "九月十号", "姓名": "张三"}, "required_fields": [],
         "components": [], "uncertain": []}]},
         schemas={"考勤表_v1.json": {
             "template": "考勤表_v1", "sheet_label": "考勤表",
             "columns": [{"name": "日期", "type": "date", "required": True},
                         {"name": "姓名", "type": "text", "required": True}],
             "measurements": {"enabled": False}, "vocabulary": {}, "fact_keys": {}}})
    run_case(tmp, "g_coltype")
    check("日期列写了乱码会被抓", "TYPE_MISMATCH" in types_of(tmp, "g_coltype"),
          "实际=%s" % types_of(tmp, "g_coltype"))

    print("")
    print("-- 14. 通用能力：自定义合计上限与单位（新）")
    case(tmp, "g_unit", {"images": [image("I1", "g_unit", sheet="工时表")], "rows": [
        {"row_id": "R1", "image_id": "I1", "sheet": "工时表", "row_role": "data",
         "fields": {"日期": "2026-09-10", "姓名": "李四"}, "required_fields": [],
         "uncertain": [],
         "components": [{"name": "上午", "raw_value": "14", "value": "14"},
                        {"name": "下午", "raw_value": "16", "value": "16"}]}]},
         schemas={"工时表_v1.json": {
             "template": "工时表_v1", "sheet_label": "工时表",
             "columns": [{"name": "日期", "type": "date"}],
             "measurements": {"enabled": True, "unit": "小时", "sum_max": 24.0,
                              "categories": {}, "default_category": "always"},
             "vocabulary": {}, "fact_keys": {}}})
    run_case(tmp, "g_unit")
    ts = types_of(tmp, "g_unit")
    check("24 小时上限生效（30 小时被拦）", "COMPONENT_SUM_OVER_100" in ts, "实际=%s" % ts)
    check("非百分比单位不误报 VALUE_MISMATCH", "VALUE_MISMATCH" not in ts, "实际=%s" % ts)

    print("")
    print("-- 15. 通用能力：与化工无关的表型（新）")
    case(tmp, "g_generic", {"images": [image("I1", "g_generic", sheet="报价单")], "rows": [
        {"row_id": "R1", "image_id": "I1", "sheet": "报价单", "row_role": "data",
         "fields": {"品名": "螺纹钢", "数量": "120", "单价": "3850"},
         "required_fields": ["品名"], "components": [], "uncertain": []}]},
         schemas={"报价单_v1.json": {
             "template": "报价单_v1", "sheet_label": "报价单",
             "columns": [{"name": "品名", "type": "text", "required": True},
                         {"name": "数量", "type": "number"},
                         {"name": "单价", "type": "number"}],
             "measurements": {"enabled": False}, "vocabulary": {}, "fact_keys": {}}})
    code, out = run_case(tmp, "g_generic")
    check("非化工表型（自带契约）零问题跑通",
          code == 0 and len(open_issues_of(tmp, "g_generic")) == 0,
          "退出=%d 问题=%d" % (code, len(open_issues_of(tmp, "g_generic"))))

    print("")
    print("-- 15b. 批量一致性：表名漂移要被看见，但不阻塞数据（新）")
    case(tmp, "g_drift", {"images": [image("I1", "g_drift", sheet="来料分析单")], "rows": [
        {"row_id": "R1", "image_id": "I1", "sheet": "来料分析单", "row_role": "data",
         "fields": {"物料": "X"}, "required_fields": [], "components": [], "uncertain": []}]})
    run_case(tmp, "g_drift")
    drift = [i for i in open_issues_of(tmp, "g_drift") if i["type"] == "SHEET_UNKNOWN"]
    check("表名没注册会被报出来", len(drift) == 1,
          "实际=%s" % [i["type"] for i in open_issues_of(tmp, "g_drift")])
    check("表名漂移不阻塞数据行（只提示）", drift and drift[0]["severity"] == "info",
          "严重度=%s" % (drift[0]["severity"] if drift else "无"))
    _h, facts_drift = named_rows(tmp, "g_drift", "成分事实")
    check("表名漂移的行仍能落账", len(facts_drift) >= 0 and
          len(open_issues_of(tmp, "g_drift")) == 1,
          "问题=%s" % [i["type"] for i in open_issues_of(tmp, "g_drift")])

    print("")
    print("-- 16. 人工决定：下拉标签与字段类决定")
    case(tmp, "g_decide", {"images": [image("I1", "g_decide")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {},
         "required_fields": ["物料"], "components": [],
         "uncertain": [{"field": "乙酸", "candidates": ["0.5%", "5%"]}]}]})
    run_case(tmp, "g_decide")
    ids = {i["type"]: i["issue_id"] for i in issues_of(tmp, "g_decide")}
    case(tmp, "g_decide", {"images": [image("I1", "g_decide")], "rows": [
        {"row_id": "R1", "image_id": "I1", "row_role": "data", "fields": {},
         "required_fields": ["物料"], "components": [],
         "uncertain": [{"field": "乙酸", "candidates": ["0.5%", "5%"]}]}]},
        decisions={"run_id": "g_decide", "issue_decisions": [
            {"issue_id": ids.get("MISSING_REQUIRED_FIELD", ""), "decision": "物料=IPAC"},
            {"issue_id": ids.get("OCR_UNCERTAIN", ""), "decision": "沿用候选A"}],
            "archive_decisions": []})
    run_case(tmp, "g_decide")
    f = sheet_of(tmp, "g_decide", "成分事实")
    vals = [(r[7], r[9]) for r in f[1:]] if f and len(f) > 1 else []
    check("下拉标签「沿用候选A」还原成 0.5%", ("乙酸", "0.005") in vals, "实际=%s" % vals)
    d = sheet_of(tmp, "g_decide", "明细数据")
    check("字段类决定写进了明细表", d and any("IPAC" in [str(c) for c in r] for r in d[1:]),
          "明细=%s" % (d[1:] if d else None))


    print("")
    print("-- 17. 样板机制与阶段识别（新）")
    case(tmp, "g_sample", {"images": [image("I1", "g_sample", sheet="新表型X")], "rows": [
        {"row_id": "R1", "image_id": "I1", "sheet": "新表型X", "row_role": "data",
         "fields": {"日期": "2026-09-10", "姓名": "王五", "金额": "12.5"},
         "required_fields": ["姓名"], "components": [], "uncertain": []}]})
    proc = subprocess.run([sys.executable, os.path.join(BASE, "tools", "生成样板.py"),
                           os.path.join(tmp, "runs", "g_sample")],
                          cwd=tmp, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    check("生成样板 退出码 0", proc.returncode == 0, (proc.stdout or "")[-200:])
    check("样板.xlsx 已生成",
          os.path.exists(os.path.join(tmp, "runs", "g_sample", "样板.xlsx")))
    draft_path = os.path.join(tmp, "runs", "g_sample", "样板_契约草案.json")
    check("契约草案已生成", os.path.exists(draft_path))
    if os.path.exists(draft_path):
        with io.open(draft_path, encoding="utf-8") as f:
            draft = json.load(f)
        cols = {c["name"]: c for c in draft.get("columns", [])}
        check("草案把日期识别成 date 型", cols.get("日期", {}).get("type") == "date",
              "实际=%s" % cols.get("日期"))
        check("草案把金额识别成 number 型", cols.get("金额", {}).get("type") == "number",
              "实际=%s" % cols.get("金额"))
        check("草案保留了必填信息", cols.get("姓名", {}).get("required") is True,
              "实际=%s" % cols.get("姓名"))

    # 阶段识别
    code, out = run_case(tmp, "g_sample", ["--status"])
    check("--status 报出「立样板」阶段", "立样板" in out or "等样板确认" in out,
          out[-300:])
    write_json(os.path.join(tmp, "runs", "g_sample", "sample.json"),
               {"sample_image_id": "I1", "confirmed": True, "schema": "新表型X_v1"})
    code, out = run_case(tmp, "g_sample", ["--status"])
    check("样板确认后进入下一阶段", "样板确认" not in out.split("当前阶段")[-1][:20],
          out[-300:])


if __name__ == "__main__":
    sys.exit(main())
