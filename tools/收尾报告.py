# -*- coding: utf-8 -*-
"""收尾报告 —— 一句话交付。数字全部来自磁盘上的账本，不靠谁回忆。

    python tools/收尾报告.py runs/20260910_003

为什么要有它：以前"今天录了几张、几个问题、归档没有"是 AI 凭记忆说的。
记忆会错、会美化、会漏。现在这些数字一律由脚本从文件里读出来打印，
AI 只负责把这段输出转述给人听 —— 它编不了，也漏不掉。

数字的来源（都写明出处，不猜）：
  · 几张图、几行数据   <- raw.json
  · 几个问题、几条没处理 <- issues.json（validate_export.py 写的账本）
  · 归档成没成         <- archive_plan.json
  · 长表几条事实       <- facts.jsonl（tools/导出长表.py 写的）

顺带做一件有用的检查：**你填了审核表但还没重跑**时会明确告警 ——
task_review.xlsx 是派生文件，重跑会整表重写，没落进 decisions.json 的填写会丢。

退出码：0 正常；2 任务目录不存在。
"""
import argparse
import json
import os
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import validate_export as ve  # noqa: E402


def load_text_json(path, default=None):
    """读 JSON，容忍 BOM。"""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def resolve_run_dir(arg):
    cand = arg if os.path.isabs(arg) else os.path.join(BASE, arg)
    cand = os.path.abspath(cand)
    if os.path.isdir(cand):
        return cand
    alt = os.path.join(BASE, "runs", arg.strip().strip("/\\"))
    return os.path.abspath(alt) if os.path.isdir(alt) else None


def count_lines(path):
    if not os.path.isfile(path):
        return None
    n = 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def mtime(path):
    return os.path.getmtime(path) if os.path.isfile(path) else 0


def main():
    ap = argparse.ArgumentParser(description="收尾报告：一句话交付（数字全部来自磁盘账本）")
    ap.add_argument("run_dir", help="任务目录，如 runs/20260910_003")
    ap.add_argument("--one-line", action="store_true", help="只打一行，方便直接转述给用户")
    args = ap.parse_args()

    run_dir = resolve_run_dir(args.run_dir)
    if not run_dir:
        print("[错误] 找不到任务目录：%s" % args.run_dir)
        return 2

    run_id = os.path.basename(run_dir)
    raw = load_text_json(os.path.join(run_dir, "raw.json"), {}) or {}
    issues_doc = load_text_json(os.path.join(run_dir, "issues.json"), {}) or {}
    plan_doc = load_text_json(os.path.join(run_dir, "archive_plan.json"), {}) or {}
    manifest = load_text_json(os.path.join(run_dir, "source", "names.json"), {}) or {}
    decisions = load_text_json(os.path.join(run_dir, "decisions.json"), {}) or {}

    imgs = manifest.get("images") or []
    recognized = [i for i in raw.get("images", []) if isinstance(i, dict)]
    rows = [r for r in raw.get("rows", []) if isinstance(r, dict)]
    data_rows = [r for r in rows if ve.to_display(r.get("row_role")) in ("", "data")]
    sheets = sorted({ve.to_display(r.get("sheet")) for r in rows if ve.to_display(r.get("sheet"))})

    issues = issues_doc.get("issues") or []
    severe = len([i for i in issues if i.get("severity") == "severe"])
    warn = len([i for i in issues if i.get("severity") == "warn"])
    info = len([i for i in issues if i.get("severity") == "info"])
    done = len([i for i in issues if i.get("human_decision")
                or i.get("machine_status") in ("resolved", "accepted")])
    undecided = len(issues) - done

    plan = plan_doc.get("plan") or []
    moved = len([p for p in plan if p.get("executed")])
    failed = [p for p in plan if p.get("result") and p.get("result") not in ("成功", "未执行")]

    final_ok = os.path.isfile(os.path.join(run_dir, "final.xlsx"))
    review_ok = os.path.isfile(os.path.join(run_dir, "task_review.xlsx"))
    facts_n = count_lines(os.path.join(run_dir, "facts.jsonl"))

    # 状态判定：和 validate_export.py --status 的口径一致（以账本为准，不猜）
    if not recognized:
        state = "未开始识别"
    elif len(recognized) < len(imgs):
        state = "识别中（还剩 %d 张）" % (len(imgs) - len(recognized))
    elif not issues_doc:
        state = "已识别完，还没跑校验"
    elif undecided and not decisions.get("issue_decisions"):
        state = "待人工复核（%d 条问题）" % undecided
    elif undecided:
        state = "已收到 %d 条决定，还有 %d 条没处理" % (
            len(decisions.get("issue_decisions") or []), undecided)
    elif not final_ok:
        state = "可以重跑落账了"
    elif plan and moved < len(plan):
        state = "数据已落账，归档还差 %d 张" % (len(plan) - moved)
    else:
        state = "已完成"

    line = ("%s：%s；原图 %d 张（已识别 %d）；数据行 %d 行；表型 %s；"
            "问题 %d 条（严重 %d / 需确认 %d / 提示 %d，未处理 %d）；"
            "落账 %s；归档 %d/%d%s"
            % (run_id, state, len(imgs), len(recognized), len(data_rows),
               "、".join(sheets) if sheets else "（无）",
               len(issues), severe, warn, info, undecided,
               ("final.xlsx 已生成" if final_ok else "未生成"),
               moved, len(plan), ("，失败 %d" % len(failed)) if failed else ""))

    if args.one_line:
        print(line)
        return 0

    print()
    print("=" * 60)
    print("  交付摘要：%s" % run_id)
    print("=" * 60)
    print("  当前状态：%s" % state)
    print("  表型：    %s" % ("、".join(sheets) if sheets else "（还没有数据行）"))
    print("  原图：    %d 张（已识别 %d 张）" % (len(imgs), len(recognized)))
    print("  数据行：  %d 行" % len(data_rows))
    print("  问题：    %d 条（严重 %d / 需确认 %d / 提示 %d）；已处理 %d 条，未处理 %d 条"
          % (len(issues), severe, warn, info, done, undecided))
    print("  落账：    final.xlsx %s" % ("已生成" if final_ok else "未生成"))
    if facts_n is not None:
        print("  长表：    facts.jsonl %d 条事实" % facts_n)
    if plan:
        print("  归档：    %d/%d 已移动%s" % (moved, len(plan), ("，失败 %d 个" % len(failed)) if failed else ""))
        for p in failed:
            print("      · %s：%s" % (p.get("original_name", ""), p.get("result", "")))

    print()
    print("  文件：")
    for rel in ("raw.json", "issues.json", "decisions.json", "task_review.xlsx",
                "final.xlsx", "facts.jsonl", "archive_plan.json", "sample.json",
                "样板.xlsx"):
        p = os.path.join(run_dir, rel)
        print("    %s %s" % ("[有]" if os.path.isfile(p) else "[无]", os.path.relpath(p, BASE)))

    # 关键告警：审核表填了、但还没落进 decisions.json —— 重跑会把填写冲掉
    warn_lines = []
    if review_ok and mtime(os.path.join(run_dir, "task_review.xlsx")) > mtime(
            os.path.join(run_dir, "decisions.json")):
        if decisions.get("issue_decisions"):
            warn_lines.append("审核表比 decisions.json 新 —— 你在 Excel 里可能又改了东西，"
                              "但还没导入。重跑前先跑 tools/导入审核.py，否则新填的会被冲掉。")
        else:
            warn_lines.append("审核表已生成，但还没有 decisions.json —— "
                              "如果用户已经在 Excel 里填了，请先跑 tools/导入审核.py。")
    if not os.path.isfile(os.path.join(run_dir, "decisions.json")) and undecided:
        warn_lines.append("还有 %d 条问题没有人工决定，这些行不会进 final.xlsx（红线）。" % undecided)
    if failed:
        warn_lines.append("有 %d 张图归档失败，原图仍在 runs/.../source/ 里，没有丢。" % len(failed))

    if warn_lines:
        print()
        print("  ⚠ 要注意：")
        for w in warn_lines:
            print("    · %s" % w)

    print()
    print("  一句话转述给用户：")
    print("    " + line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
