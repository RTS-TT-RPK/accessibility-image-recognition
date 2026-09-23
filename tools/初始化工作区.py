# -*- coding: utf-8 -*-
"""补齐工作区文件夹骨架 —— 换电脑/新解压后跑一次，缺什么补什么。

用法：
    python tools/初始化工作区.py            补齐并打印结果
    python tools/初始化工作区.py --quiet    静默模式（安装脚本里调用时用）

它会做的事：
    确保 inbox / pending / archive / golden / runs / schemas 六个文件夹存在；
    缺 README.md 的，补一份说明（告诉用户这个夹子是干嘛的）。

它绝不做的事：
    不删除任何东西；不覆盖已存在的文件（你自己改过的说明不会被冲掉）；
    不动 runs/ 里的历史任务；不碰 golden/ 里的标准答案。

安全性：重复运行完全无副作用（幂等）。
"""
import io
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 文件夹 -> 该文件夹的说明文字（None 表示只建目录、不写说明）
SKELETON = [
    ("inbox", (
        "# inbox —— 把今天的照片扔这里\n"
        "\n"
        "- 不用分类、不用改名、不用管顺序。\n"
        "- 可以自己建日期子文件夹（如 `2026-09-10`），纯粹为了你好找。\n"
        "- 系统不会删除这里的文件（除非归档时是你确认过的移动）。\n"
    )),
    ("pending", (
        "# pending —— 暂时处理不了的照片\n"
        "\n"
        "读不清、太暗、需要重拍的照片会被放到这里，不会被丢弃。\n"
    )),
    ("archive", (
        "# archive —— 归档区\n"
        "\n"
        "人工确认后才把原图移动进来，按日期建文件夹。**永不覆盖同名文件**（撞名自动加 `_2`）。\n"
    )),
    ("golden", (
        "# golden —— 标准答案库\n"
        "\n"
        "只有你确认过的结果才会进这里，**只增加、永不修改**。\n"
        "越攒越多，系统回归测试和长期台账都从它重建。\n"
    )),
    ("runs", (
        "# runs —— 每次任务的完整存档\n"
        "\n"
        "每次任务一个文件夹：`YYYYMMDD_NNN/`\n"
        "\n"
        "| 文件 | 作用 |\n"
        "|------|------|\n"
        "| `raw.json` | 识别原文（证据，写完不改） |\n"
        "| `issues.json` | 问题账本（今天要你判断什么） |\n"
        "| `decisions.json` | 你的决定（唯一权威） |\n"
        "| `archive_plan.json` | 归档计划与实际结果 |\n"
        "| `final.xlsx` | 最终业务数据 |\n"
        "| `task_review.xlsx` | 给你审核用的表 |\n"
        "| `source/` | 原图副本 |\n"
    )),
    ("schemas", None),
]


def ensure(quiet=False):
    made_dirs, made_files, kept = [], [], []

    for name, text in SKELETON:
        folder = os.path.join(BASE, name)
        if not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
            made_dirs.append(name)

        if text is None:
            continue

        readme = os.path.join(folder, "README.md")
        if os.path.exists(readme):
            kept.append(name)
            continue
        with io.open(readme, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)          # 只在不存在时写，绝不覆盖
        made_files.append(name)

    if quiet:
        return made_dirs, made_files, kept

    print("=" * 58)
    print("  工作区文件夹自检 / 补齐")
    print("=" * 58)
    print("工作区：" + BASE)
    print("")
    for name, _ in SKELETON:
        folder = os.path.join(BASE, name)
        readme = os.path.join(folder, "README.md")
        if name in made_dirs:
            tag = "[新建文件夹]"
        elif name in made_files:
            tag = "[补上说明]"
        elif os.path.exists(readme):
            tag = "[已存在]"
        else:
            tag = "[已存在]"
        print("  %-10s %s" % (name + "\\", tag))

    print("")
    if not made_dirs and not made_files:
        print("结论：工作区结构完整，什么都没改。")
    else:
        print("结论：补齐 %d 个文件夹、%d 份说明。" % (len(made_dirs), len(made_files)))
        if made_dirs:
            print("  新建：" + "、".join(made_dirs))
        if made_files:
            print("  补说明：" + "、".join(made_files))
    print("（已存在的文件和文件夹一律不动，历史数据不会受影响）")
    return made_dirs, made_files, kept


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)
    ensure(quiet="--quiet" in argv)
    sys.exit(0)
