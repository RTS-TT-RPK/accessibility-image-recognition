# -*- coding: utf-8 -*-
"""环境自检 —— 一次性告诉你"这台电脑能不能干活、缺什么、怎么补"。

用法：
    python tools/环境自检.py            给人看的报告
    python tools/环境自检.py --json     给 AI 读的 JSON（机器可解析）

退出码：0 = 必需项齐全，可以干活；1 = 缺必需项（照报告里的"修复命令"补）。

只做检查，不装任何东西、不改任何文件。
"""
import io
import json
import os
import shutil
import sys
import importlib.util

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_ID = "image-data-recognition"
SKILL_NAME = "图像数据识别转化"

# 工作区必须有的文件夹
NEEDED_DIRS = ["inbox", "pending", "archive", "golden", "runs", "schemas"]

# 离线依赖包所在目录
WHEELS = os.path.join(BASE, "vendor", "wheels")


def load_tool_list():
    """复用 安装技能.py 里的工具清单，避免两处各写一份、日后走样。"""
    path = os.path.join(BASE, "tools", "安装技能.py")
    if not os.path.isfile(path):
        return []
    try:
        spec = importlib.util.spec_from_file_location("_install_skill_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.TOOLS
    except Exception:
        return []


def check_python():
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 10)
    return {
        "name": "Python",
        "required": True,
        "ok": ok,
        "value": "%d.%d.%d" % sys.version_info[:3],
        "path": sys.executable,
        "fix": "装 Python 3.10 或更高版本：https://www.python.org/downloads/windows/ "
               "（安装时务必勾选 Add python.exe to PATH；装完重开命令行）",
    }


def check_openpyxl():
    try:
        import openpyxl
        return {"name": "openpyxl", "required": True, "ok": True,
                "value": getattr(openpyxl, "__version__", "?")}
    except Exception:
        offline = os.path.isdir(WHEELS) and any(
            f.startswith("openpyxl") for f in os.listdir(WHEELS))
        if offline:
            fix = ('python -m pip install --no-index --find-links "%s" openpyxl' % WHEELS)
        else:
            fix = "python -m pip install openpyxl"
        return {"name": "openpyxl", "required": True, "ok": False,
                "value": "未安装", "fix": fix,
                "offline_wheels": offline}


def check_node():
    node = shutil.which("node")
    return {"name": "Node.js", "required": False, "ok": bool(node),
            "value": node or "未安装",
            "note": "只有 tools\\DSH技能自检.mjs 需要它；核心识别/校验/归档不需要"}


def check_dirs():
    out = []
    for d in NEEDED_DIRS:
        out.append({"name": d, "ok": os.path.isdir(os.path.join(BASE, d))})
    return out


def check_skill_installs():
    home = os.path.expanduser("~")
    rows = []
    for name, cfg, sub, _verified, _note in load_tool_list():
        root = os.path.join(home, cfg)
        dst = os.path.join(root, sub, SKILL_ID)
        rows.append({
            "tool": name,
            "tool_dir_exists": os.path.isdir(root),
            "installed": os.path.isdir(dst),
            "path": dst,
        })
    return rows


def check_workspace_txt():
    """检查技能副本里的 workspace.txt 编码是否安全（BOM / 乱码）。"""
    home = os.path.expanduser("~")
    rows = []
    for name, cfg, sub, _v, _n in load_tool_list():
        p = os.path.join(home, cfg, sub, SKILL_ID, "workspace.txt")
        if not os.path.isfile(p):
            continue
        raw = open(p, "rb").read()
        has_bom = raw[:3] == b"\xef\xbb\xbf"
        try:
            text = raw.decode("utf-8-sig").strip()
            readable = os.path.isdir(text)
        except Exception:
            text, readable = "", False
        rows.append({"tool": name, "path": p, "has_bom": has_bom,
                     "workspace": text, "workspace_exists": readable})
    return rows


def gather():
    return {
        "workspace": BASE,
        "python": check_python(),
        "openpyxl": check_openpyxl(),
        "node": check_node(),
        "dirs": check_dirs(),
        "skill_installs": check_skill_installs(),
        "workspace_txt": check_workspace_txt(),
    }


def render(rep):
    L = []
    L.append("=" * 60)
    L.append("  环境自检 —— " + SKILL_NAME)
    L.append("=" * 60)
    L.append("工作区：" + rep["workspace"])
    L.append("")

    L.append("【必需】缺了就不能干活")
    for key in ("python", "openpyxl"):
        it = rep[key]
        flag = "[OK]  " if it["ok"] else "[缺!] "
        L.append("  %s%-10s %s" % (flag, it["name"], it["value"]))
        if not it["ok"]:
            L.append("       路径/说明: " + str(it.get("path", "")))
            L.append("       修复命令 : " + it.get("fix", ""))
    L.append("")

    it = rep["node"]
    L.append("【可选】")
    L.append("  %s%-10s %s" % ("[OK]  " if it["ok"] else "[--]  ", it["name"], it["value"]))
    L.append("       " + it["note"])
    L.append("")

    L.append("【工作区文件夹】")
    missing = [d["name"] for d in rep["dirs"] if not d["ok"]]
    if missing:
        L.append("  缺：" + "、".join(missing))
        L.append("  修复：python tools\\初始化工作区.py")
    else:
        L.append("  齐全：" + "、".join(d["name"] for d in rep["dirs"]))
    L.append("")

    L.append("【技能安装情况】")
    any_installed = False
    for r in rep["skill_installs"]:
        if not r["tool_dir_exists"]:
            continue
        mark = "[已装]" if r["installed"] else "[未装]"
        if r["installed"]:
            any_installed = True
        L.append("  %s %s" % (mark, r["tool"]))
    if not any_installed:
        L.append("  修复：双击 tools\\一键安装（双击）.bat，或 python tools\\安装技能.py")
    L.append("")

    if rep["workspace_txt"]:
        L.append("【workspace.txt 编码检查】")
        for r in rep["workspace_txt"]:
            ok = r["has_bom"] and r["workspace_exists"]
            L.append("  %s %-12s BOM=%s 路径有效=%s"
                     % ("[OK]  " if ok else "[注意]", r["tool"], r["has_bom"], r["workspace_exists"]))
            if not r["workspace_exists"]:
                L.append("       内容: %r" % r["workspace"])
        L.append("       （无 BOM 时，PowerShell 5.1 默认按 GBK 读会把中文路径读成乱码）")
        L.append("")

    ok_all = all(rep[k]["ok"] for k in ("python", "openpyxl")) and not missing
    L.append("-" * 60)
    if ok_all:
        L.append("结论：环境齐全，可以开始干活。")
        L.append('下一步：把单据照片放进 inbox，然后对 AI 说"处理今天的图"。')
    else:
        L.append("结论：还缺必需项，照上面的【修复命令】补，补完再跑一次本脚本。")
    return "\n".join(L), ok_all


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        sys.exit(0)

    report = gather()
    text, ok = render(report)

    if "--json" in argv:
        out = dict(report)
        out["all_required_ok"] = ok
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(text)

    sys.exit(0 if ok else 1)
