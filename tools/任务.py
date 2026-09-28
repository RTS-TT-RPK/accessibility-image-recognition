# -*- coding: utf-8 -*-
"""任务调度器 —— 一个入口，8 个动词，几乎不用带参数。

    python tools/任务.py new                  收 inbox 建任务
    python tools/任务.py next                 看下一张待识别的图 + 该填的格式
    python tools/任务.py submit < 片段.json    交一张图的识别结果
    python tools/任务.py status               看进度（到哪一步了、下一步干什么）
    python tools/任务.py check                全部识别完后刷新问题账本
    python tools/任务.py review               生成审核表交给用户
    python tools/任务.py import               用户填完后，把 Excel 导成 decisions.json
    python tools/任务.py finalize             落账 + 归档 + 出长表 + 出交付摘要

**为什么要有它**：那 7 个脚本各自都对，但叫一个模型去拼
`python tools/提交识别.py runs/20260910_003 --image IMG_001 --file x.json`
—— 里面有任务号、图片编号、中文文件路径三个可拼错的东西。DSH 插件版不用拼，是因为
**确定性代码替它拼了**；通用版要做到同样的事，就得有人替它拼。这个文件就是那个人。

它自己**不含任何业务逻辑**：全部转手交给原来那 7 个脚本，输出原样转达。
所以它坏掉也不会影响数据正确性，最多是入口不好用。

三条省事的规则：
  · **不用给任务号** —— 自动挑当前没干完的那个任务（`--run` 可以指定）；
  · **不用给图片编号** —— 从片段 JSON 自己读 `image_id`；
  · **不用给文件路径** —— 片段从标准输入进来，中文路径的坑直接绕开。

退出码：和它调用的那个脚本一致（0 成功 / 2 被拒或出错 / 1 有断言失败）。
"""
import argparse
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
# stdin 也必须钉死 UTF-8！这台机器的默认编码是 GBK，而识别片段是从标准输入进来的：
# 不钉的话，"水"会被按 GBK 解码成"姘?"，**在写进 raw.json 之前就已经烂了** ——
# 属于最阴的一类错：脚本一切正常、退出码 0、只有数据是错的。
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_ID_RE = re.compile(r"^\d{8}_\d{3}$")
PY = sys.executable

# 子脚本一律用相对路径、cwd=工作区。cwd 必须是工作区：
# 裁判脚本靠"当前目录"判断 schemas/aliases/archive 在哪，跑错地方会静默解析到别处。
STEPS = {
    "建": "tools/建任务.py",
    "交": "tools/提交识别.py",
    "导": "tools/导入审核.py",
    "长": "tools/导出长表.py",
    "报": "tools/收尾报告.py",
    "裁": "validate_export.py",
    "型": "tools/注册表型.py",
    "录": "tools/收录.py",
}


def head(title):
    print()
    print("=" * 62)
    print("  " + title)
    print("=" * 62)


def read_json(path, default=None):
    """读 JSON，容忍 BOM。"""
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return default


def list_runs():
    runs_dir = os.path.join(BASE, "runs")
    if not os.path.isdir(runs_dir):
        return []
    return sorted([d for d in os.listdir(runs_dir)
                   if RUN_ID_RE.match(d) and os.path.isdir(os.path.join(runs_dir, d))],
                  reverse=True)


def run_state(run_dir):
    """这个任务干完了没有。判据全部来自磁盘上的账本，不猜。"""
    raw = read_json(os.path.join(run_dir, "raw.json"), {}) or {}
    plan = (read_json(os.path.join(run_dir, "archive_plan.json"), {}) or {}).get("plan") or []
    manifest = read_json(os.path.join(run_dir, "source", "names.json"), {}) or {}
    recognized = len([i for i in (raw.get("images") or []) if isinstance(i, dict)])
    total = len(manifest.get("images") or [])
    final_ok = os.path.isfile(os.path.join(run_dir, "final.xlsx"))
    archived = bool(plan) and all(p.get("executed") for p in plan)
    return {
        "total": total,
        "recognized": recognized,
        "final": final_ok,
        "archived": archived,
        "done": final_ok and archived,
    }


def pick_run(explicit=None):
    """挑当前该干活的任务：显式指定 > 最新的没干完的 > 最新的。"""
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(BASE, "runs", explicit)
        return os.path.abspath(p) if os.path.isdir(p) else None
    runs = list_runs()
    if not runs:
        return None
    for name in runs:                      # runs 已按名字倒序 = 最新的在前
        d = os.path.join(BASE, "runs", name)
        if not run_state(d)["done"]:
            return d
    return os.path.join(BASE, "runs", runs[0])


def call(args, stdin_text=None, quiet=False):
    """跑一个子脚本，把它的话原样转达。返回退出码。"""
    script = args[0]
    if not os.path.isfile(os.path.join(BASE, script)):
        print()
        print("  [错误] 找不到脚本：%s" % script)
        print("         工作区是不是不完整？跑 python tools\\初始化工作区.py 或重装技能包。")
        return 2
    env = dict(os.environ)
    # 这台机器 Python 的 stdout 默认是 GBK，中文会变乱码；显式钉成 UTF-8。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if not quiet:
        print()
        print("  $ python %s" % " ".join(args))
    p = subprocess.run([PY] + args, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input=stdin_text,
                       timeout=1800, env=env)
    out = (p.stdout or "").rstrip()
    err = (p.stderr or "").rstrip()
    if out:
        print(out)
    if err:
        print(err)
    return p.returncode


def dispatch(verb, run_dir, extra, stdin_text):
    rel = os.path.relpath(run_dir, BASE) if run_dir else ""

    if verb == "new":
        # 建任务不需要已有任务；--run-id / --date / 文件列表原样传下去
        args = [STEPS["建"]] + extra
        return call(args, stdin_text)

    if verb == "next":
        return call([STEPS["交"], rel, "--next"] + extra)

    if verb == "submit":
        frag_text = stdin_text
        file_arg = None
        if "--file" in extra:
            i = extra.index("--file")
            file_arg = extra[i + 1] if i + 1 < len(extra) else None
            extra = extra[:i] + extra[i + 2:]
        if not frag_text and not file_arg:
            print()
            print("  [错误] 没给识别片段。两种交法：")
            print("     python tools\\任务.py submit < 片段.json      （推荐，不用管路径）")
            print("     python tools\\任务.py submit --file 片段.json")
            return 2

        image_id = None
        for i, a in enumerate(extra):
            if a == "--image" and i + 1 < len(extra):
                image_id = extra[i + 1]
                extra = extra[:i] + extra[i + 2:]
                break
        if not image_id:
            # 编号从片段自己读 —— 模型不用抄编号，抄错的机会就没了
            try:
                data = json.loads(frag_text) if frag_text else read_json(file_arg, {})
            except Exception as e:
                print()
                print("  [错误] 片段不是合法 JSON：%s" % e)
                return 2
            if not isinstance(data, dict) or not data.get("image_id"):
                print()
                print("  [错误] 片段里没有 image_id，也没用 --image 指定。")
                print("         片段顶层必须带 \"image_id\"（照 tools\\任务.py next 打印的格式写）。")
                return 2
            image_id = str(data["image_id"]).strip()

        if frag_text:
            return call([STEPS["交"], rel, "--image", image_id, "--stdin"] + extra,
                        stdin_text=frag_text)
        return call([STEPS["交"], rel, "--image", image_id, "--file", file_arg] + extra)

    if verb == "status":
        # 拿不到任务时也要给一句人话，而不是让裁判脚本报"目录不存在"
        if not run_dir:
            print()
            print("  还没有任何任务。先跑：python tools\\任务.py new")
            return 0
        st = run_state(run_dir)
        print()
        print("  当前任务：%s（原图 %d 张，已识别 %d 张%s）"
              % (os.path.basename(run_dir), st["total"], st["recognized"],
                 "，已完成" if st["done"] else ""))
        return call([STEPS["裁"], rel, "--status"])

    if verb == "check":
        return call([STEPS["裁"], rel, "--init-issues"] + extra)

    if verb == "review":
        return call([STEPS["裁"], rel] + extra)

    if verb == "import":
        return call([STEPS["导"], rel] + extra)

    if verb == "finalize":
        codes = []
        codes.append(("落账 + 归档", call([STEPS["裁"], rel, "--archive"] + extra)))
        if codes[-1][1] != 0:
            print()
            print("  [!] 落账/归档没成功（退出码 %d），后面的长表和摘要**不再执行** ——"
                  " 免得给你一份看着像完成了的摘要。" % codes[-1][1])
            return codes[-1][1]
        codes.append(("导出长表", call([STEPS["长"], rel, "--quiet"])))
        codes.append(("交付摘要", call([STEPS["报"], rel, "--one-line"])))
        head("收尾结果")
        for name, code in codes:
            print("  %s %s（退出码 %d）" % ("[完成]" if code == 0 else "[失败]", name, code))
        return 0 if all(c == 0 for _n, c in codes) else 2

    print("[错误] 不认识的动词：%s" % verb)
    return 2


HELP = """任务调度器 —— 一个入口，8 个动词

  python tools\\任务.py new                收 inbox 建任务（可加 --dry-run 先看）
  python tools\\任务.py next               看下一张待识别的图 + 该填的格式
  python tools\\任务.py submit < 片段.json  交一张图的识别结果（片段从标准输入进）
  python tools\\任务.py status             看进度 + 下一步该干什么
  python tools\\任务.py check              全部识别完后刷新问题账本
  python tools\\任务.py review             生成审核表（task_review.xlsx）交给用户
  python tools\\任务.py import             用户填完后，把 Excel 导成 decisions.json
  python tools\\任务.py finalize           落账 + 归档 + 出长表 + 出交付摘要

公共参数：
  --run <任务号>   指定任务（默认自动挑"最新且还没干完"的那个）
  --file <路径>    只对 submit 有效：片段从文件读（默认从标准输入读）
  -h, --help       看这份说明

识别阶段不要来问用户：看不清就写进 uncertain，疑点脚本会记账，最后统一复核。
"""


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help", "帮助"):
        print(HELP)
        return 0 if argv else 2

    verb = argv[0].strip().lower()
    rest = argv[1:]

    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--run", default=None)
    known, _unknown = ap.parse_known_args(rest)
    extra = []
    skip = False
    for i, a in enumerate(rest):
        if skip:
            skip = False
            continue
        if a == "--run":
            skip = True
            continue
        extra.append(a)

    run_dir = pick_run(known.run)
    stdin_text = None
    if verb == "submit" and "--file" not in extra:
        if sys.stdin is not None and not sys.stdin.isatty():
            stdin_text = sys.stdin.read()
        elif sys.stdin is None:
            stdin_text = None

    head("任务调度器：%s" % verb)
    if run_dir and verb != "new":
        st = run_state(run_dir)
        print("  任务：%s（原图 %d 张 / 已识别 %d 张）"
              % (os.path.basename(run_dir), st["total"], st["recognized"]))
    elif verb != "new" and not run_dir:
        print("  还没有任何任务 —— 先用 new 建一个。")

    return dispatch(verb, run_dir, extra, stdin_text)


if __name__ == "__main__":
    sys.exit(main())
