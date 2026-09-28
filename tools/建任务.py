# -*- coding: utf-8 -*-
"""建任务 —— 把 inbox 里的图收进一个新的任务文件夹，并算好指纹。

    python tools/建任务.py                     收 inbox 里全部图片
    python tools/建任务.py a.jpg b.jpg         只收指定的几张
    python tools/建任务.py --date 2026-09-10   指定任务日期
    python tools/建任务.py --dry-run           只看会收哪些，不写任何东西

它做的四件事（全部是"机器该干的活"，以前由 AI 手工做）：

  1. 挑一个不重复的任务号 runs/YYYYMMDD_NNN/（NNN 从 001 开始，撞了就 +1）；
  2. 把原图**复制**进 runs/<任务号>/source/（不改名、不动 inbox 里的原件）；
  3. 逐张算 sha256 指纹（算的是真实字节，不是谁写上去的字符串）；
  4. 写两样东西：
       runs/<任务号>/source/names.json  —— 图片台账（编号↔原文件名↔指纹↔大小）
       runs/<任务号>/raw.json           —— 骨架，images/rows 都是空的

**为什么 raw.json 一开始是空的**：裁判脚本把"出现在 images[] 里的图"当成"已识别"。
如果建任务时就先塞进去，进度就会谎报"全部识别完了"。所以登记在 names.json，
只有真正识别完的图才由 tools/提交识别.py 写进 raw.json —— 进度因此永远是真的。

指纹还用来查重：同一张图以前收过，会在这里点名报出来（但**不替你删**，按"不猜"原则，
收不收由你决定；确认不要就加 --skip-duplicates）。

退出码：0 成功；2 出错（没有任何图可收、任务号已存在等）。
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import validate_export as ve  # noqa: E402  （唯一裁判，只借用它的工具函数，不改它）

RUN_ID_RE = r"^\d{8}_\d{3}$"


def head(title):
    print()
    print("=" * 60)
    print("  " + title)
    print("=" * 60)


def list_images(folder):
    """列出文件夹里的图片（按文件名排序，保证每次跑的顺序一致）。"""
    if not os.path.isdir(folder):
        return []
    out = []
    for fn in sorted(os.listdir(folder)):
        p = os.path.join(folder, fn)
        if os.path.isfile(p) and fn.lower().endswith(ve.IMAGE_EXT):
            out.append(p)
    return out


def next_run_id(root, date_part):
    """给这一天挑一个没被占用的任务号：YYYYMMDD_001、_002……"""
    runs = os.path.join(root, "runs")
    used = set()
    if os.path.isdir(runs):
        for fn in os.listdir(runs):
            if os.path.isdir(os.path.join(runs, fn)) and fn.startswith(date_part + "_"):
                used.add(fn)
    n = 1
    while True:
        cand = "%s_%03d" % (date_part, n)
        if cand not in used:
            return cand
        n += 1


def known_hashes(root):
    """扫历史任务，收集已经收过的指纹 —— 用来发现"同一张图重复提交"。"""
    seen = {}
    runs = os.path.join(root, "runs")
    if not os.path.isdir(runs):
        return seen
    for run_name in sorted(os.listdir(runs)):
        man = os.path.join(runs, run_name, "source", "names.json")
        if not os.path.isfile(man):
            continue
        try:
            with open(man, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except Exception:
            continue
        for item in data.get("images", []):
            h = item.get("sha256")
            if h:
                seen.setdefault(h, []).append("%s/%s" % (run_name, item.get("original_name", "?")))
    return seen


def make_image_id(original_name, used):
    """图片编号：用原文件名的主干，重名就加 _2、_3……"""
    stem = os.path.splitext(original_name)[0]
    stem = ve.sanitize_name(stem, max_len=60) or "IMG"
    cand, n = stem, 1
    while cand in used:
        n += 1
        cand = "%s_%d" % (stem, n)
    used.add(cand)
    return cand


def unique_dest(source_dir, original_name):
    """同名的图不覆盖：加 _2、_3……（"不覆盖"是红线）"""
    dest = os.path.join(source_dir, original_name)
    stem, ext = os.path.splitext(original_name)
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(source_dir, "%s_%d%s" % (stem, n, ext))
    return dest


def build(root, files, date_part, run_id, source_type, skip_duplicates, dry_run):
    run_dir = os.path.join(root, "runs", run_id)
    source_dir = os.path.join(run_dir, "source")

    if os.path.exists(run_dir):
        print("[错误] 任务号已经存在，不能覆盖：" + run_dir)
        return 2

    dups = known_hashes(root)

    head("收图建任务" + ("（试运行，不写任何东西）" if dry_run else ""))
    print("  工作区：  " + root)
    print("  任务号：  " + run_id)
    print("  待收图片：%d 张" % len(files))
    print()

    if not dry_run:
        os.makedirs(source_dir, exist_ok=True)

    images = []
    duplicates = []
    skipped = []
    used_ids = set()
    batch_hashes = {}

    for src in files:
        original_name = os.path.basename(src)
        digest = ve.sha256_of(src)
        size = os.path.getsize(src)

        if digest in dups:
            where = "、".join(dups[digest][:3])
            duplicates.append((original_name, where))
            if skip_duplicates:
                skipped.append(original_name)
                print("  [跳过] %-28s 与 %s 是同一张图（--skip-duplicates）" % (original_name, where))
                continue
        if digest in batch_hashes:
            # 这一批里自己撞了（同一张图重复提交常见于手机连拍/微信重复保存）
            duplicates.append((original_name, "本批的 " + batch_hashes[digest]))
            if skip_duplicates:
                skipped.append(original_name)
                print("  [跳过] %-28s 与本批的 %s 是同一张图（--skip-duplicates）"
                      % (original_name, batch_hashes[digest]))
                continue
        else:
            batch_hashes[digest] = original_name

        image_id = make_image_id(original_name, used_ids)
        # raw.json 里的 file 字段统一用正斜杠相对路径，换电脑/换盘符也不会失效。
        rel_file = "runs/%s/source/%s" % (run_id, original_name)

        if not dry_run:
            dest = unique_dest(source_dir, original_name)
            shutil.copy2(src, dest)
            if os.path.basename(dest) != original_name:
                # 收进来时撞名了，实际文件名和原文件名不同 —— 如实记下来，并同步 file 字段。
                print("  [改名] %s 已存在，本次收为 %s" % (original_name, os.path.basename(dest)))
                rel_file = "runs/%s/source/%s" % (run_id, os.path.basename(dest))

        images.append({
            "image_id": image_id,
            "original_name": original_name,
            "file": rel_file,
            "sha256": digest,
            "bytes": size,
        })
        print("  [收入] %-28s -> %-10s %s" % (original_name, image_id, digest[:12]))

    head("结果")
    print("  已收入：%d 张" % len(images))
    if skipped:
        print("  已跳过（重复）：%d 张 —— %s" % (len(skipped), "、".join(skipped)))
    if duplicates:
        print()
        print("  ⚠ 有 %d 张图以前收过（同一份指纹）：" % len(duplicates))
        for name, where in duplicates:
            print("      · %s  ←→ %s" % (name, where))
        print("    已按原样收进来（没有替你丢东西）。确认是重复提交，重跑时加 --skip-duplicates。")
        print("    裁判脚本也会对指纹相同的图出一条 DUPLICATE 问题，最后统一复核时一起看。")

    if not images:
        print()
        print("[错误] 一张图都没收到。inbox 里是不是没图？")
        return 2

    if dry_run:
        print()
        print("  试运行结束，什么都没写。去掉 --dry-run 就真的建任务。")
        return 0

    manifest = {
        "_说明": "本任务的图片台账：编号 ↔ 原文件名 ↔ 指纹。由 tools/建任务.py 生成。",
        "run_id": run_id,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "images": images,
    }
    ve.write_json(os.path.join(source_dir, "names.json"), manifest)

    # raw.json 骨架：images/rows 都留空，等 tools/提交识别.py 一张一张填。
    raw = {
        "run_id": run_id,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_type": source_type,
        "images": [],
        "rows": [],
    }
    raw_path = os.path.join(run_dir, "raw.json")
    if os.path.exists(raw_path):
        print()
        print("[错误] raw.json 已存在，绝不覆盖：" + raw_path)
        return 2
    ve.write_json(raw_path, raw)

    print()
    print("  写好了：")
    print("    " + os.path.join(source_dir, "names.json"))
    print("    " + raw_path)
    print()
    print("  下一步（注意：识别阶段不要来问用户，疑点全部记账）：")
    print("    1) python tools/提交识别.py \"runs/%s\" --next   看下一张图和要填的格式" % run_id)
    print("    2) 看图 → 把这一张的识别结果写成一个 json → 提交")
    print("    3) 全部识别完：python validate_export.py \"runs/%s\" --init-issues" % run_id)
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="收图建任务：建 runs/日期_编号/、复制原图、算指纹、写图片台账和 raw.json 骨架")
    ap.add_argument("files", nargs="*", help="要收的图片路径；不写就收 inbox 里全部图片")
    ap.add_argument("--date", help="任务日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--run-id", help="直接指定任务号 YYYYMMDD_NNN（默认自动取下一个空号）")
    ap.add_argument("--source-type", default="incoming",
                    help="来源类型，写进 raw.json 的 source_type（默认 incoming）")
    ap.add_argument("--skip-duplicates", action="store_true",
                    help="指纹和以前收过的图相同的，直接跳过不收")
    ap.add_argument("--dry-run", action="store_true", help="只显示会收哪些，不写任何文件")
    args = ap.parse_args()

    if args.date:
        try:
            date_part = datetime.strptime(args.date.strip(), "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            print("[错误] --date 要写成 YYYY-MM-DD，例如 2026-09-10")
            return 2
    else:
        date_part = datetime.now().strftime("%Y%m%d")

    if args.run_id:
        run_id = args.run_id.strip()
        if not __import__("re").match(RUN_ID_RE, run_id):
            print("[错误] --run-id 要写成 YYYYMMDD_NNN，例如 20260910_001")
            return 2
    else:
        run_id = next_run_id(BASE, date_part)

    if args.files:
        files, missing = [], []
        for f in args.files:
            p = f if os.path.isabs(f) else os.path.join(BASE, f)
            if os.path.isfile(p):
                files.append(p)
            else:
                missing.append(f)
        if missing:
            print("[错误] 这些文件找不到：" + "、".join(missing))
            return 2
    else:
        files = list_images(os.path.join(BASE, "inbox"))

    if not files:
        print("[错误] 没有可收的图。把照片放进 inbox 再跑一次，或直接把图片路径写在命令后面。")
        return 2

    return build(BASE, files, date_part, run_id, args.source_type,
                 args.skip_duplicates, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
