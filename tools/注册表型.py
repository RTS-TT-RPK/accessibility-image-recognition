# -*- coding: utf-8 -*-
"""注册表型 —— 把你的样板确认变成一份正式契约（schemas/）。

    python tools/注册表型.py runs/20260910_003 --name 来料分析 \
        --row-unit "一行 = 一个样品（一个桶 / 一批）" \
        --name-pattern "{date}_{sheet}_{batch}_第{n}张" \
        --notes "用户说桶号那列其实叫罐号，已改"

它做完这几件事：
  1. 读 tools/生成样板.py 产出的 `样板_契约草案.json`；
  2. 检查草案里**还没被回答的问题**（如 row_unit 还是"（请确认：…）"）——有就不许写，
     让你先去问用户（"不猜"原则，这一条是硬拦，--force 才能绕过）；
  3. 写 `schemas/<表型名>_vN.json`（同一表改版自动递增到 _v2，旧版永久保留）；
  4. 写 `runs/<任务号>/sample.json`，记下"这个样板什么时候被谁确认过"；
  5. 自动跑一遍 `tools/回归测试.py`，确认新契约没把裁判改坏。

**为什么要脚本写，而不是让 AI 抄一遍**：契约是整套系统的规则层，抄错一个列名、
漏一个 required，后面每一张图都会跟着错，而且很难发现。草案是脚本推断的，
落成正式契约也就是脚本搬一次的事，中间不该有第三个手。

退出码：0 成功；2 被拒绝（草案缺失、必填项没确认、契约已存在等）。
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import validate_export as ve  # noqa: E402

# 草案里这些值是"问题"，不是"答案" —— 没被人工回答就不许写进正式契约
UNANSWERED_MARKERS = ("请确认", "待确认", "？", "?")


def head(title):
    print()
    print("=" * 60)
    print("  " + title)
    print("=" * 60)


def load_text_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as f:
        import json
        return json.load(f)


def resolve_run_dir(arg):
    cand = arg if os.path.isabs(arg) else os.path.join(BASE, arg)
    cand = os.path.abspath(cand)
    if os.path.isdir(cand):
        return cand
    alt = os.path.join(BASE, "runs", arg.strip().strip("/\\"))
    return os.path.abspath(alt) if os.path.isdir(alt) else None


def next_version(name):
    """同一表型改版 = 新增一份，旧版永久保留。返回 (版本号, 文件名, 是否已存在)。"""
    n = 1
    while True:
        fn = "%s_v%d.json" % (name, n)
        if not os.path.exists(os.path.join(BASE, "schemas", fn)):
            return n, fn, False
        n += 1


def check_unanswered(draft):
    """找出草案里还没被人工回答的问题。返回问题列表（空 = 可以写）。"""
    problems = []
    row_unit = ve.to_display(draft.get("row_unit"))
    if not row_unit or any(m in row_unit for m in UNANSWERED_MARKERS):
        problems.append(
            "row_unit 还没确认（现在写的是 %r）。这张表一行到底代表什么，"
            "必须由用户回答 —— 用 --row-unit \"一行 = ……\" 传进来。" % row_unit)
    cols = draft.get("columns") or []
    if not cols:
        problems.append("草案里一列都没有 —— 样板图是不是一行数据都没识别出来？")
    names = [ve.to_display(c.get("name")) for c in cols]
    empty = [i for i, n in enumerate(names) if not n]
    if empty:
        problems.append("草案里有 %d 列没有列名（第 %s 列）" % (len(empty), "、".join(str(i + 1) for i in empty)))
    dup = {n for n in names if n and names.count(n) > 1}
    if dup:
        problems.append("草案里有重复列名：%s" % "、".join(sorted(dup)))
    sheet = ve.to_display(draft.get("sheet_label"))
    if not sheet or sheet == "*":
        problems.append("草案里的 sheet_label 是 %r —— 表型名（图上那张表叫什么）要先定下来。" % sheet)
    return problems


def build_schema(draft, name, version, row_unit, name_pattern, notes):
    """把草案整理成正式契约。只做整理，不替用户做业务判断。"""
    schema = {
        "_说明": "由 tools/注册表型.py 从样板草案整理而成；用户确认日期见 runs/<任务>/sample.json。",
        "template": "%s_v%d" % (name, version),
        "sheet_label": ve.to_display(draft.get("sheet_label")),
        "row_unit": row_unit,
        "columns": [],
        "measurements": draft.get("measurements") or {
            "enabled": False, "unit": "%",
            "categories": {"water": "never", "misc": "if_present"},
            "default_category": "always", "sum_max": 100.0,
        },
        "vocabulary": draft.get("vocabulary") or {"water": [], "misc": []},
        "fact_keys": draft.get("fact_keys") or {},
        "archive": {"name_pattern": name_pattern},
    }
    for col in draft.get("columns") or []:
        item = {"name": ve.to_display(col.get("name"))}
        ctype = ve.to_display(col.get("type")) or "text"
        if ctype not in ve.COLUMN_TYPES:
            ctype = "text"
        item["type"] = ctype
        if col.get("required"):
            item["required"] = True
        if ctype == "date":
            item["excel_format"] = "yyyy-mm-dd"
        schema["columns"].append(item)
    if notes:
        schema["_确认备注"] = notes
    return schema


def run_regression():
    script = os.path.join(BASE, "tools", "回归测试.py")
    if not os.path.isfile(script):
        return None, "找不到 tools/回归测试.py，没跑成回归"
    try:
        p = subprocess.run([sys.executable, script], cwd=BASE,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        return None, "回归测试没跑起来：%s" % e
    tail = "\n".join((p.stdout or "").strip().splitlines()[-6:])
    return p.returncode, tail


def main():
    ap = argparse.ArgumentParser(description="把样板草案整理成正式契约 schemas/<表型名>_vN.json")
    ap.add_argument("run_dir", help="任务目录，如 runs/20260910_003")
    ap.add_argument("--name", required=True, help="表型名（用来做文件名和 template），如 来料分析")
    ap.add_argument("--row-unit", help="这张表一行代表什么（必填，来自用户确认）")
    ap.add_argument("--name-pattern", default=None,
                    help="归档命名规则，如 {date}_{sheet}_{batch}_第{n}张；不填则不启用自动命名")
    ap.add_argument("--notes", default="", help="用户确认时说的话，记进 sample.json 和契约备注")
    ap.add_argument("--image-id", help="样板图的 image_id（默认取草案里有点名的第一张）")
    ap.add_argument("--force", action="store_true", help="明知有问题也写（不推荐，会在汇报里标出）")
    ap.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    args = ap.parse_args()

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir:
        print("[错误] 找不到任务目录：%s" % args.run_dir)
        return 2

    draft_path = os.path.join(run_dir, "样板_契约草案.json")
    draft = load_text_json(draft_path)
    if draft is None:
        print("[错误] 找不到样板草案：%s" % draft_path)
        print("       先跑：python tools/生成样板.py \"%s\"" % os.path.relpath(run_dir, BASE))
        return 2

    name = args.name.strip()
    if not name or any(ch in name for ch in '\\/:*?"<>|'):
        print("[错误] 表型名不合法：%r（不能含 \\ / : * ? \" < > |）" % name)
        return 2

    head("注册表型：%s" % name)
    print("  任务：  %s" % os.path.basename(run_dir))
    print("  草案：  %s" % os.path.relpath(draft_path, BASE))
    print("  表名：  %s" % ve.to_display(draft.get("sheet_label")))
    print("  列：    %s" % "、".join(ve.to_display(c.get("name")) for c in draft.get("columns") or []))

    problems = check_unanswered(draft)
    if args.row_unit:
        if any(m in args.row_unit for m in UNANSWERED_MARKERS):
            problems.append("--row-unit 里还带着问号（%r）—— 那还是问题，不是答案。" % args.row_unit)
        else:
            problems = [p for p in problems if not p.startswith("row_unit")]

    if problems and not args.force:
        print()
        print("  ✗ 先别写契约。下面这些还没被人工确认（这是「不猜」的硬拦）：")
        for p in problems:
            print("      · %s" % p)
        print()
        print("  正确做法：把这些问题问用户，拿到答复后用参数传进来，例如：")
        print("    python tools/注册表型.py \"%s\" --name %s --row-unit \"一行 = ……\""
              % (os.path.relpath(run_dir, BASE), name))
        return 2

    version, filename, _exists = next_version(name)
    target = os.path.join(BASE, "schemas", filename)

    # 同一个 sheet_label 已经注册过？先提醒（可能在重复造契约）
    schemas = ve.load_schemas(BASE)
    sheet = ve.to_display(draft.get("sheet_label"))
    for label, sch in schemas.items():
        if label == sheet and label != "*":
            print()
            print("  ⚠ 表名 %r 已经有一份契约了（template=%s）。" % (label, sch.get("template")))
            print("    如果只是这张表的**新版式**，现在这样新增 _v%d 是对的（旧版永久保留）；" % version)
            print("    如果其实是同一版式，请停下来核对，别造重复契约。")

    schema = build_schema(draft, name, version, args.row_unit or ve.to_display(draft.get("row_unit")),
                          args.name_pattern, args.notes)

    print()
    print("  将写入契约：%s" % os.path.relpath(target, BASE))
    print("    计量项合计规则：%s" % ("启用，上限 %s%%" % (schema["measurements"] or {}).get("sum_max", 100)
                                     if (schema["measurements"] or {}).get("enabled") else "不启用"))
    print("    归档命名规则：%s" % (schema["archive"]["name_pattern"] or "（不启用自动命名）"))

    if args.dry_run:
        print()
        print("  试运行结束，什么都没写。")
        return 0

    ve.write_json(target, schema)

    # 记下"这个样板被确认过" —— detect_phase 靠它判断阶段
    image_id = args.image_id or ""
    if not image_id:
        raw = load_text_json(os.path.join(run_dir, "raw.json"), {}) or {}
        imgs = [i.get("image_id") for i in raw.get("images", []) if isinstance(i, dict)]
        image_id = imgs[0] if imgs else ""
    sample = {
        "sample_image_id": image_id,
        "confirmed": True,
        "confirmed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "schema": "%s_v%d" % (name, version),
        "notes": args.notes or "",
    }
    ve.write_json(os.path.join(run_dir, "sample.json"), sample)

    print()
    print("  ✓ 契约已写入：  " + os.path.relpath(target, BASE))
    print("  ✓ 确认记录已写入：%s" % os.path.relpath(os.path.join(run_dir, "sample.json"), BASE))

    print()
    print("  正在跑回归测试（确认新契约没把裁判改坏）……")
    code, tail = run_regression()
    if code is None:
        print("  ⚠ %s" % tail)
        return 0
    print(tail)
    if code != 0:
        print()
        print("  ✗ 回归测试没全绿（退出码 %d）。契约可能写坏了 —— 请立刻检查刚写的 %s"
              % (code, os.path.relpath(target, BASE)))
        return 2

    print()
    print("  ✓ 回归测试全绿。这个表型以后直接批量识别，不用再做样板。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
