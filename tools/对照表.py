# -*- coding: utf-8 -*-
"""读图对照表：查看 / 补含义 / 加易错对。

    python tools/对照表.py                  看全部
    python tools/对照表.py 简写             只看简写
    python tools/对照表.py 易错             只看易错对
    python tools/对照表.py 加简写 甲=甲醇    补一个含义（也接受：加简写 甲 = 甲醇）
    python tools/对照表.py 加易错 合金~合全  加一对易错词

红线：本表只用于【提示】和【审核】，脚本永不据此自动改写数据。
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P = os.path.join(BASE, "对照表.json")


def load():
    if not os.path.exists(P):
        return {"_说明": "", "_红线": "", "_用法": [], "简写": [], "易错对": []}
    with open(P, encoding="utf-8") as fh:
        return json.load(fh)


def save(d):
    with open(P, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=2)


def 看简写(d):
    rows = d.get("简写") or []
    if not rows:
        print("（简写表是空的）")
        return
    待填 = [r for r in rows if not (r.get("含义") or "").strip()]
    print("=" * 66)
    print("简写对照（%d 条，其中 %d 条含义待填）" % (len(rows), len(待填)))
    print("=" * 66)
    print("  %-12s %-22s %s" % ("简写", "含义", "见过"))
    for r in rows:
        含义 = (r.get("含义") or "").strip() or "（待填）"
        print("  %-12s %-22s %s" % (r["简写"], 含义, "、".join(r.get("见过") or [])))


def 看易错(d):
    rows = d.get("易错对") or []
    if not rows:
        print("（易错对表是空的）")
        return
    print("=" * 66)
    print("易错对（%d 对）—— 读到任何一个都要标记、请人确认" % len(rows))
    print("=" * 66)
    for r in rows:
        print("  %s  ↔  %s" % (r["甲"], r["乙"]))
        print("      %s  [%s]" % (r.get("说明", ""), r.get("状态", "")))


def main(argv):
    d = load()
    if len(argv) <= 1 or argv[1] in ("看", "all", "全部"):
        看简写(d)
        print()
        看易错(d)
        return 0
    命令 = argv[1]
    if 命令 == "简写":
        看简写(d)
        return 0
    if 命令 == "易错":
        看易错(d)
        return 0
    if 命令 == "加简写":
        参 = " ".join(argv[2:]).replace(" ", "")
        if "=" not in 参:
            print("用法：python tools/对照表.py 加简写 甲=甲醇")
            return 2
        名, 义 = 参.split("=", 1)
        for r in d.setdefault("简写", []):
            if r["简写"] == 名:
                r["含义"] = 义
                save(d)
                print("已更新：%s = %s" % (名, 义))
                return 0
        d["简写"].append({"简写": 名, "含义": 义, "类别": "用户补充", "见过": []})
        save(d)
        print("已新增：%s = %s" % (名, 义))
        return 0
    if 命令 == "加易错":
        参 = " ".join(argv[2:]).replace(" ", "")
        if "~" not in 参:
            print("用法：python tools/对照表.py 加易错 合金~合全")
            return 2
        甲, 乙 = 参.split("~", 1)
        d.setdefault("易错对", []).append({
            "甲": 甲, "乙": 乙, "说明": "", "来源": "用户补充", "状态": "待确认"})
        save(d)
        print("已新增易错对：%s ↔ %s" % (甲, 乙))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
