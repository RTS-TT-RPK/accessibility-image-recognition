# -*- coding: utf-8 -*-
"""通用技能安装器 —— 把「图像数据识别转化」技能装进本机所有 AI 工具。

用法（一般直接双击 tools\安装技能（双击）.bat 就行）：

    python tools/安装技能.py            安装到所有检测到的工具
    python tools/安装技能.py --list     只列出检测到哪些工具，不安装
    python tools/安装技能.py --force    连没检测到的工具也装（目录照样建）

它会做四件事：
  1. 找到本技能源文件（工作区里的 技能源文件/image-data-recognition/）；
  2. 检测本机装了哪些 AI 工具（看它们的配置目录在不在）；
  3. 把技能整个文件夹复制到每个工具的"技能目录"里；
  4. 往每份副本写一个 workspace.txt，写明工作区在哪。

不联网、不删数据、不需要管理员权限。装完想卸载，删掉对应的技能文件夹即可。
"""
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SKILL_ID = "image-data-recognition"
SKILL_NAME = "图像数据识别转化"

HOME = os.path.expanduser("~")
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "技能源文件", SKILL_ID)

# 工具清单：(显示名, 配置目录名, 技能子目录, 是否已验证, 说明)
TOOLS = [
    ("Codex",     ".codex",     "skills", True,  "本机已实测"),
    ("DSH",       ".dsh",       "skills", True,  "本机已实测"),
    ("Cursor",    ".cursor",    "skills", True,  "按官方文档"),
    ("OpenCode",  ".opencode",  "skills", True,  "本机已实测"),
    ("Claude",    ".claude",    "skills", True,  "通用约定"),
    ("ZCode",     ".zcode",     "skills", False, "同族约定，装入后请在工具里确认一次"),
    ("通用 agents", ".agents",  "skills", True,  "跨工具新标准（DSH/Cursor 等会读）"),
]

# 工作区里也放一份（工具打开这个文件夹时能直接读到）
PROJECT_LOCAL = [(".dsh", "DSH 项目级"), (".agents", "通用项目级")]


def line(text=""):
    print(text)


def head(title):
    line()
    line("=" * 58)
    line("  " + title)
    line("=" * 58)


def check_source():
    if not os.path.isdir(SRC):
        head("找不到技能源文件")
        line("应该在这里，但不存在：")
        line("  " + SRC)
        line()
        line("请确认：本脚本必须放在工作区的 tools\\ 目录下运行。")
        sys.exit(1)
    if not os.path.isfile(os.path.join(SRC, "SKILL.md")):
        head("技能源文件不完整")
        line("缺少 SKILL.md：" + SRC)
        sys.exit(1)


def detect(force):
    found = []
    for name, cfg, sub, verified, note in TOOLS:
        root = os.path.join(HOME, cfg)
        exists = os.path.isdir(root)
        if exists or force:
            found.append((name, os.path.join(root, sub), verified, note, exists))
    return found


def show_list(force):
    head("检测本机装了哪些 AI 工具")
    for name, cfg, sub, verified, note in TOOLS:
        root = os.path.join(HOME, cfg)
        mark = "[发现了]" if os.path.isdir(root) else "[没找到]"
        flag = "" if verified else "（该工具的技能目录未在官方文档中确认，属于同族推测）"
        line("%-12s %s  %s%s" % (mark, name, note, flag))
    line()
    line("工作区：" + BASE)
    line("技能源： " + SRC)


def copy_skill(dst_dir, workdir):
    """把技能复制到 dst_dir（含 workspace.txt）。只覆盖技能自己的文件，不动别的东西。"""
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(SRC):
        if entry in (".git", "__pycache__") or entry.endswith(".pyc"):
            continue
        s = os.path.join(SRC, entry)
        d = os.path.join(dst_dir, entry)
        if os.path.isdir(s):
            os.makedirs(d, exist_ok=True)
            for dp, dns, fns in os.walk(s):
                dns[:] = [x for x in dns if x not in (".git", "__pycache__")]
                rel = os.path.relpath(dp, s)
                target = os.path.join(d, rel) if rel != "." else d
                os.makedirs(target, exist_ok=True)
                for fn in fns:
                    if fn.endswith(".pyc"):
                        continue
                    shutil.copy2(os.path.join(dp, fn), os.path.join(target, fn))
        else:
            shutil.copy2(s, d)
    # 用 utf-8-sig（= UTF-8 带 BOM）写，不能只用 utf-8：
    # Windows PowerShell 5.1 的 Get-Content 默认按系统 ANSI（中文机器是 GBK/936）解码，
    # 没有 BOM 的 UTF-8 中文路径会被读成乱码（如 图像识别长期工程 -> 鍥惧儚璇嗗埆闀挎湡宸ョ▼）。
    # 反过来，Python 读带 BOM 的文件必须用 encoding="utf-8-sig"，用 "utf-8" 会多出一个 \ufeff。
    with open(os.path.join(dst_dir, "workspace.txt"), "w",
              encoding="utf-8-sig", newline="\n") as f:
        f.write(workdir + "\n")


def install(force):
    head("安装「%s」技能" % SKILL_NAME)
    line("工作区：  " + BASE)
    line("技能源：  " + SRC)
    line()

    targets = detect(force)
    if not targets:
        line("没有检测到任何 AI 工具的配置目录。")
        line("用 --force 可以照样安装（以后装了工具就能立刻用）。")
        return 0

    ok, fail = [], []
    for name, skills_root, verified, note, existed in targets:
        dst = os.path.join(skills_root, SKILL_ID)
        already = os.path.isdir(dst)
        try:
            copy_skill(dst, BASE)
            ok.append((name, dst, already))
            line("  [完成] %-12s -> %s" % (name, dst))
        except Exception as e:
            fail.append((name, str(e)))
            line("  [失败] %-12s %s" % (name, e))

    head("工作区里也放一份（打开这个文件夹就能用）")
    for cfg, label in PROJECT_LOCAL:
        dst = os.path.join(BASE, cfg, "skills", SKILL_ID)
        try:
            copy_skill(dst, BASE)
            line("  [完成] %-12s -> %s" % (label, dst))
        except Exception as e:
            line("  [跳过] %-12s %s" % (label, e))

    head("结果")
    line("装好的工具：%d 个" % len(ok))
    for name, dst, already in ok:
        tag = "（更新了旧版本）" if already else "（新装）"
        line("  · %s%s" % (name, tag))
    if fail:
        line()
        line("失败的：")
        for name, err in fail:
            line("  · %s：%s" % (name, err))

    line()
    line("接下来怎么用：")
    line("  1. 在你常用的 AI 工具里打开工作区文件夹：")
    line("     " + BASE)
    line("  2. 对它说：处理今天的单据照片")
    line("  3. 想确认技能有没有被识别到，问它：你有哪些技能？")
    line()
    line("提示：技能源文件改动后，重新双击运行本脚本即可同步到所有工具。")
    return 0 if not fail else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        line(__doc__)
        sys.exit(0)
    check_source()
    if "--list" in argv:
        show_list("--force" in argv)
        sys.exit(0)
    sys.exit(install("--force" in argv))
