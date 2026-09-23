# -*- coding: utf-8 -*-
"""把整个技能打包成一个 zip，用于迁移到别的电脑。

用法：
    python tools/导出技能包.py                # 只打包系统（不含业务数据）
    python tools/导出技能包.py --with-golden  # 连 golden 标准答案一起打包

包里包含：
  表格识别系统/          —— 整个工作区（脚本、规则、文档、离线依赖、技能源文件）
  技能文件_手动安装/     —— 技能源文件的副本，供不想跑脚本的人手动复制到各工具目录
"""
import os
import sys
import zipfile
import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "V1.6"
SKILL_ID = "image-data-recognition"

INCLUDE = [
    "AGENTS.md", "AI自安装说明.md", "validate_export.py", "aliases.json", "FEEDBACK_LOG.md",
    "使用说明.md", "schemas", "tools", "docs", "vendor", "技能源文件",
    # 工作区骨架：这五个业务文件夹本身要在，各带一份说明，
    # 这样新电脑解压出来就是完整工作区（不跑脚本也不缺文件夹）。
    "inbox/README.md", "pending/README.md", "archive/README.md",
    "golden/README.md", "runs/README.md",
]
ALWAYS_SKIP = {".git", "__pycache__", "发布", "runs", "inbox", "pending", "archive", "golden"}

# 这些文件名里带中文全角括号，打包时保持原样即可


def build_zip(with_golden=False):
    today = datetime.date.today().strftime("%Y%m%d")
    out_dir = os.path.join(BASE, "发布")
    os.makedirs(out_dir, exist_ok=True)
    zip_name = "表格识别技能包_%s_%s.zip" % (VERSION, today)
    zip_path = os.path.join(out_dir, zip_name)

    includes = list(INCLUDE)
    if with_golden:
        includes.append("golden")

    count = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:

        # 1) 技能源文件（工作区里那一份，是唯一真源）
        skill_src = os.path.join(BASE, "技能源文件", SKILL_ID)
        if os.path.isdir(skill_src):
            for dirpath, dirnames, filenames in os.walk(skill_src):
                dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP and not d.startswith(".")]
                for fn in filenames:
                    if fn.endswith(".pyc") or fn.startswith("~$"):
                        continue
                    full = os.path.join(dirpath, fn)
                    if fn == "workspace.txt":
                        continue  # 这台电脑的路径，换电脑会重新生成
                    relpath = os.path.relpath(full, skill_src)
                    zf.write(full, arcname=os.path.join("技能文件_手动安装", SKILL_ID, relpath))
                    count += 1
            print("已包含技能源文件（%s）" % SKILL_ID)
        else:
            print("（未找到技能源文件，跳过：%s）" % skill_src)

        # 2) 工作区文件（脚本、规则、文档、离线依赖）
        for rel in includes:
            src = os.path.join(BASE, rel)
            if not os.path.exists(src):
                continue
            if os.path.isfile(src):
                zf.write(src, arcname=os.path.join("表格识别系统", rel))
                count += 1
                continue
            for dirpath, dirnames, filenames in os.walk(src):
                dirnames[:] = [d for d in dirnames if d not in ALWAYS_SKIP and not d.startswith(".")]
                for fn in filenames:
                    if fn.endswith(".pyc") or fn.startswith("~$"):
                        continue
                    if rel == "技能源文件" and fn == "workspace.txt":
                        continue
                    full = os.path.join(dirpath, fn)
                    relpath = os.path.relpath(full, BASE)
                    zf.write(full, arcname=os.path.join("表格识别系统", relpath))
                    count += 1

        version_txt = (
            "表格识别系统 技能包\n"
            "版本：%s\n"
            "打包日期：%s\n"
            "包含文件数：%d\n"
            "\n"
            "=== 新电脑上怎么装（两步）===\n"
            "1. 解压本压缩包，得到「表格识别系统」文件夹（里面已经带齐所有工作文件夹）；\n"
            "2. 双击 表格识别系统\\tools\\一键安装（双击）.bat   —— 一步搞定：\n"
            "     装 Python 依赖（离线） + 补齐工作文件夹 + 把技能装进本机所有 AI 工具。\n"
            "\n"
            "装完就能直接用：用 AI 工具打开「表格识别系统」文件夹，把图丢进 inbox，说“处理今天的图”。\n"
            "\n"
            "装技能的替代办法（不想跑脚本时）：\n"
            "  把「技能文件_手动安装\\%s」整个文件夹复制到对应工具的技能目录：\n"
            "    Codex     →  %%USERPROFILE%%\\.codex\\skills\\\n"
            "    DSH       →  %%USERPROFILE%%\\.dsh\\skills\\\n"
            "    Cursor    →  %%USERPROFILE%%\\.cursor\\skills\\\n"
            "    Claude    →  %%USERPROFILE%%\\.claude\\skills\\\n"
            "    OpenCode  →  %%USERPROFILE%%\\.opencode\\skills\\\n"
            "    ZCode     →  %%USERPROFILE%%\\.zcode\\skills\\\n"
            "   通用 agents →  %%USERPROFILE%%\\.agents\\skills\\\n"
            "\n"
            "在技能文件夹里建一个 workspace.txt，写一行本工作区的完整路径，AI 就能自动找到工作区。\n"
            "\n"
            "详细说明见 表格识别系统\\使用说明.md\n"
            "  以及 技能文件_手动安装\\%s\\references\\安装部署说明.md（当前安装部署说明）\n"
            "\n"
            "=== 也可以让 AI 自己装 ===\n"
            "  打开 表格识别系统 文件夹，让 AI 读根目录的 AI自安装说明.md，它会自己检查环境、\n"
            "  装依赖、把技能放进自己的技能目录，并跑一遍自检。手动步骤在该文档第 7 节。\n"
            "  只想看环境有没有问题：python tools\\环境自检.py\n"
            % (VERSION, datetime.date.today().isoformat(), count, SKILL_ID, SKILL_ID)
        )
        zf.writestr("表格识别系统/VERSION.txt", version_txt)

    size_mb = os.path.getsize(zip_path) / 1024.0 / 1024.0
    print("打包完成：%s" % zip_path)
    print("文件数：%d ；大小：%.1f MB" % (count + 1, size_mb))
    if with_golden:
        print("已包含 golden（标准答案）")
    else:
        print("未包含业务数据（runs/inbox/archive/golden）；如需包含：python tools/导出技能包.py --with-golden")
    return zip_path


if __name__ == "__main__":
    build_zip(with_golden="--with-golden" in sys.argv)
