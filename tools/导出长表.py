# -*- coding: utf-8 -*-
"""导出长表 —— 把已确认的数据落成一份"机器可读的长表"（facts.jsonl）。

    python tools/导出长表.py runs/20260910_003

产出 `runs/<任务号>/facts.jsonl`：**一行一条事实**（哪个对象、哪个计量项、多少、单位、
原图原文、来源行号），是 Excel 之外的另一份落账形式。

为什么要多这一份：
  · Excel 是给人看的（宽表、排版、公式），机器读起来别扭；
  · 长表列永远不变，只是行数增加 —— 这正是数据库最喜欢的形状，
    将来数据量大了要搬进数据库，**原样导入即可，不用重新识别一遍**；
  · 纯文本 + 一行一条，出问题能直接 diff，不像二进制 Excel 那样没法比对。

红线（和 Excel 完全一致，用的是裁判脚本自己的 collect_rows）：
  **未解决（blocked）的行绝不进长表** —— 没确认的数据不能混进正式台账。
  脚本会把被挡掉的行数如实报出来，不静默。

退出码：0 成功；2 出错（任务目录不存在、raw.json 读不了）。
"""
import argparse
import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import validate_export as ve  # noqa: E402


def load_text_json(path, default=None):
    """读 JSON，容忍 BOM（裁判脚本的 read_json 不容忍，这里绕开那个雷）。"""
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def resolve_run_dir(arg):
    cand = arg if os.path.isabs(arg) else os.path.join(BASE, arg)
    cand = os.path.abspath(cand)
    if os.path.isdir(cand):
        return cand
    alt = os.path.join(BASE, "runs", arg.strip().strip("/\\"))
    return os.path.abspath(alt) if os.path.isdir(alt) else None


def main():
    ap = argparse.ArgumentParser(description="把已确认的数据导出成机器可读的长表 facts.jsonl")
    ap.add_argument("run_dir", help="任务目录，如 runs/20260910_003")
    ap.add_argument("--out", help="输出文件名（默认 facts.jsonl，写在任务目录里）")
    ap.add_argument("--quiet", action="store_true", help="只打印结果，不打印明细")
    args = ap.parse_args()

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir:
        print("[错误] 找不到任务目录：%s" % args.run_dir)
        return 2

    raw_path = os.path.join(run_dir, "raw.json")
    if not os.path.isfile(raw_path):
        print("[错误] 没有 raw.json：%s" % raw_path)
        return 2
    raw = load_text_json(raw_path)
    if not isinstance(raw, dict):
        print("[错误] raw.json 顶层不是对象，拒绝导出")
        return 2

    run, notes = ve.normalize_run(dict(raw))
    schemas = ve.load_schemas(BASE)
    known = ve.load_known_terms(BASE)
    by_issue, _by_image = ve.load_decisions(run_dir)

    # 和 validate_export.py 主流程一模一样的三步，绝不另写一套判据：
    #   第一遍校验 -> 落实人工决定 -> 第二遍校验（问题编号稳定，不会错位）
    pass1 = ve.build_issues(run, schemas, {}, known)
    pass1 += ve.detect_duplicates(run)
    ve.apply_decisions(run, by_issue, pass1)
    issues = ve.build_issues(run, schemas, {}, known)
    issues += ve.detect_duplicates(run)

    for item in issues:
        d = by_issue.get(item["issue_id"])
        if d and d.get("decision"):
            item["human_decision"] = d["decision"]

    blocking = [i for i in issues if not ve.is_approve(i.get("human_decision"))]
    detail, facts, unresolved = ve.collect_rows(run, blocking, schemas)

    out_path = os.path.join(run_dir, args.out or "facts.jsonl")
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        for fact in facts:
            f.write(json.dumps(fact, ensure_ascii=False) + "\n")
    os.replace(tmp_path, out_path)

    data_rows = [r for r in run.get("rows", []) if ve.to_display(r.get("row_role")) in ("", "data")]
    blocked_rows = [r for r in detail if r.get("status") == ve.STATUS_BLOCKED]

    print()
    print("== 导出长表：%s ==" % os.path.basename(run_dir))
    if notes:
        print("  ⚠ raw.json 结构异常 %d 处（已尽量救回，问题清单里也有记录）" % len(notes))
    print("  数据行：%d 行；其中已确认 %d 行、未解决 %d 行" %
          (len(data_rows), len(data_rows) - len(blocked_rows), len(blocked_rows)))
    print("  长表事实：%d 条 -> %s" % (len(facts), os.path.relpath(out_path, BASE)))

    if blocked_rows:
        print()
        print("  ⚠ 有 %d 行没进长表（红线：未确认的数据不进正式台账）：" % len(blocked_rows))
        for r in blocked_rows[:10]:
            print("      · %s（%s）" % (r.get("row_id"), r.get("sheet")))
        if len(blocked_rows) > 10:
            print("      …… 还有 %d 行" % (len(blocked_rows) - 10))
        print("    处理办法：把人工决定写进 decisions.json（用 tools/导入审核.py），然后重跑。")

    if not args.quiet and facts:
        print()
        print("  前几条长这样：")
        for fact in facts[:3]:
            print("    " + json.dumps(fact, ensure_ascii=False))

    if not facts:
        print()
        print("  ⚠ 长表是空的 —— 要么还没数据，要么所有行都被挡着。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
