# -*- coding: utf-8 -*-
"""拆表原型：把 raw.json 拆成「快照表 + 度量长表 + 成分表 + 问题表」，并生成可执行 SQL。

这是**提案原型**，不是插件现有能力 —— 现有插件只出 facts.jsonl，而且
「没有 components 的表」出不来长表（本次实测暴露的缺口）。这里演示修好之后长什么样。

只读 raw.json / issues.json / schemas/；只写 测试夹具/sql演示/。
用法：python 测试夹具/拆表原型.py [run_id]
"""
import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = sys.argv[1] if len(sys.argv) > 1 else "20261011_001"
RUNDIR = os.path.join(BASE, "runs", RUN)
OUT = os.path.join(BASE, "测试夹具", "sql演示")

# ---- 建议的契约（正式做法是走 tools/注册表型.py 落成 schemas/<表型>_v1.json）----
PROPOSED = {
    "扬州贝尔物料总表": {
        "table": "tank_snapshot",
        "cn": "储位盘点快照",
        "keys": ["储存区域", "物料名称", "来源", "状态", "性质"],
        "measures": {"液位": "吨", "密度": "", "使用率": "%", "最大容积": "吨"},
        "component_col": "成分明细",
    },
    "库存明细": {
        "table": "stock_snapshot",
        "cn": "库存明细快照",
        "keys": ["库位", "储存区域", "编号", "物料名称", "来源", "状态", "规格", "性质"],
        "measures": {"数量": ""},
        "component_col": "成分明细",
    },
    "高栏来料作业单": {
        "table": "incoming_batch",
        "cn": "来料批次行",
        "keys": ["序号", "物料名称", "入库圈号", "化验结果", "入罐号或并箱去处"],
        "measures": {},
        "component_col": "化验结果",
        "box_col": "小桶货方箱编号",
    },
    "进料记录": {
        "table": "feed_log",
        "cn": "进料记录",
        "keys": ["日期", "塔号", "原料名称", "数量", "进料时间", "原料贮存位置", "备注"],
        "measures": {},
        "component_col": None,
    },
}
WATER = ["水", "水分", "含水量", "水份", "含水"]
MISC = ["杂", "杂质", "杂分", "其他", "残余", "不纯物"]


def q(v):
    if v is None:
        return "NULL"
    return "'" + str(v).replace("'", "''") + "'"


def qn(v):
    if v is None or str(v).strip() == "":
        return "NULL"
    try:
        return repr(float(str(v).replace(",", "").replace("%", "").strip()))
    except ValueError:
        return "NULL"


def parse_components(text):
    """把"成分明细"自由文本拆成组分。只认 % 数和 PH，其余原样留成 note（不猜）。"""
    out = []
    if not text:
        return out
    for tok in re.split(r"[\s\u3000、,，;；]+", str(text)):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"^PH\s*[=:：]?\s*([0-9]+(?:\.[0-9]+)?)$", tok, re.I)
        if m:
            out.append(("PH", float(m.group(1)), "", tok, "ph"))
            continue
        m = re.match(r"^(.+?)([0-9]+(?:\.[0-9]+)?)\s*%$", tok)
        if m:
            name = m.group(1).strip()
            cat = "water" if name in WATER else ("misc" if name in MISC else "main")
            out.append((name, float(m.group(2)), "%", tok, cat))
            continue
        if tok in MISC or tok in WATER:
            out.append((tok, None, "", tok, "misc" if tok in MISC else "water"))
            continue
        out.append(("", None, "", tok, "note"))
    return out


def main():
    raw = json.load(open(os.path.join(RUNDIR, "raw.json"), encoding="utf-8"))
    try:
        issues = json.load(open(os.path.join(RUNDIR, "issues.json"), encoding="utf-8"))
    except Exception:
        issues = {}
    ilist = issues.get("issues", issues if isinstance(issues, list) else [])
    os.makedirs(OUT, exist_ok=True)

    rows = [r for r in raw.get("rows", []) if (r.get("row_role") or "data") == "data"]
    by_sheet = {}
    for r in rows:
        by_sheet.setdefault(r.get("sheet", "?"), []).append(r)

    ddl, ins = [], []
    ddl.append("-- 由「拆表原型.py」从 runs/%s/raw.json 生成（提案原型，非插件现有输出）" % RUN)
    ddl.append("PRAGMA foreign_keys = ON;\n")

    # 溯源表：每条事实都能追回原图
    ddl.append("""CREATE TABLE probe (
  probe_id   INTEGER PRIMARY KEY,
  run_id     TEXT NOT NULL,
  image_id   TEXT NOT NULL,
  row_id     TEXT NOT NULL,            -- 溯源到 raw.json 的行号
  sheet      TEXT NOT NULL,
  object_label TEXT,
  has_uncertain INTEGER DEFAULT 0
);""")

    snap_id = 0
    metrics, comps, boxes = [], [], []
    for sheet, srows in sorted(by_sheet.items()):
        spec = PROPOSED.get(sheet)
        if not spec:
            # 容错：片段里的表名只要包含契约名就算命中（手写表名常常写不全）
            for k, v in PROPOSED.items():
                if k in str(sheet) or str(sheet) in k:
                    spec = v
                    print("  [容错匹配] 表名「%s」按契约「%s」处理" % (sheet, k))
                    break
        if not spec:
            print("  [跳过] 表型「%s」还没定契约（%d 行）" % (sheet, len(srows)))
            continue
        cols = spec["keys"]
        ddl.append("\n-- %s（%s）：一行 = 一个%s在一个时点" %
                   (spec["table"], spec["cn"], "储位" if "tank" in spec["table"] else "库位"))
        ddl.append("CREATE TABLE %s (\n  snapshot_id INTEGER PRIMARY KEY,\n" % spec["table"])
        ddl.append("  probe_id INTEGER REFERENCES probe(probe_id),\n")
        for c in cols:
            ddl.append("  \"%s\" TEXT,\n" % c)
        ddl.append("  component_text TEXT          -- 成分明细原文，保真\n);")
        for r in srows:
            snap_id += 1
            f = r.get("fields") or {}
            unc = 1 if (r.get("uncertain") or []) else 0
            ins.append("INSERT INTO probe VALUES (%d,%s,%s,%s,%s,%s,%d);" % (
                snap_id, q(raw.get("run_id")), q(r.get("image_id")), q(r.get("row_id")),
                q(sheet), q(r.get("object_label")), unc))
            vals = [str(snap_id), str(snap_id)] + [q(f.get(c, "")) for c in cols]
            ins.append("INSERT INTO %s VALUES (%s,%s);" % (
                spec["table"], ",".join(vals[:-0] if False else vals),
                )) if False else None
            ins.append("INSERT INTO %s (snapshot_id, probe_id, %s, component_text) VALUES (%d,%d,%s,%s);" % (
                spec["table"], ",".join('"%s"' % c for c in cols), snap_id, snap_id,
                ",".join(q(f.get(c, "")) for c in cols),
                q(f.get(spec["component_col"], ""))))
            for mname, unit in spec["measures"].items():
                rv = f.get(mname, "")
                if str(rv).strip() == "":
                    continue
                num = None
                try:
                    num = float(str(rv).replace(",", "").replace("%", "").strip())
                except ValueError:
                    pass
                metrics.append((snap_id, mname, num, unit, str(rv), sheet))
            for (nm, val, unit, rawt, cat) in (parse_components(f.get(spec["component_col"], ""))
                                             if spec.get("component_col") else []):
                comps.append((snap_id, nm, val, unit, rawt, cat, sheet))
            bc = spec.get("box_col")
            if bc:
                # 一格塞了一百多个箱号 —— 不拆成行就永远查不了"哪些箱装的是二甲苯"
                for tok in re.split(r"[\s\u3000、,，;；.。]+", str(f.get(bc, ""))):
                    tok = tok.strip().strip(".")
                    if tok.isdigit():
                        boxes.append((snap_id, tok, sheet, f.get("物料名称", ""),
                                      f.get("化验结果", ""), f.get("入罐号或并箱去处", "")))

    ddl.append("""
-- 长表（度量）：一行 = 一个对象 × 一个指标 × 一个值
--   正是它让「液位/密度/使用率」这类数值从来料总表里被拆出来，能直接 SQL 查
CREATE TABLE measure (
  snapshot_id INTEGER NOT NULL,
  sheet       TEXT NOT NULL,
  metric      TEXT NOT NULL,
  value_num   REAL,
  unit        TEXT,
  raw_value   TEXT            -- 图上原文，保真
);""")
    ddl.append("""
-- 成分表：一行 = 一个对象 × 一个组分（从"成分明细"自由文本里拆出来）
CREATE TABLE component_reading (
  snapshot_id INTEGER NOT NULL,
  sheet       TEXT NOT NULL,
  component   TEXT,
  value_num   REAL,
  unit        TEXT,
  raw_token   TEXT,           -- 拆之前那个词的原样
  category    TEXT            -- main / water / misc / ph / note
);""")
    ddl.append("""
-- 箱号表：一个批次行 × 一个箱号（把"小桶货方箱编号"那一格长串拆成行）
CREATE TABLE incoming_box (
  snapshot_id INTEGER NOT NULL,
  box_no      TEXT NOT NULL,
  sheet       TEXT NOT NULL,
  material    TEXT,
  assay       TEXT,
  destination TEXT
);

-- 问题表：账本进关系库，一个决定都不丢
CREATE TABLE issue (
  issue_id    TEXT PRIMARY KEY,
  type        TEXT, severity TEXT, sheet TEXT, object TEXT, field TEXT,
  description TEXT, target_ids TEXT, resolved INTEGER DEFAULT 0
);""")

    for t in metrics:
        ins.append("INSERT INTO measure (snapshot_id,metric,value_num,unit,raw_value,sheet) VALUES (%d,%s,%s,%s,%s,%s);"
                   % (t[0], q(t[1]), qn(t[2]), q(t[3]), q(t[4]), q(t[5])))
    for c in comps:
        ins.append("INSERT INTO component_reading (snapshot_id,sheet,component,value_num,unit,raw_token,category) VALUES (%d,%s,%s,%s,%s,%s,%s);"
                   % (c[0], q(c[6]), q(c[1]), qn(c[2]), q(c[3]), q(c[4]), q(c[5])))
    for b in boxes:
        ins.append("INSERT INTO incoming_box VALUES (%d,%s,%s,%s,%s,%s);"
                   % (b[0], q(b[1]), q(b[2]), q(b[3]), q(b[4]), q(b[5])))
    for i in ilist:
        ins.append("INSERT INTO issue VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%d);" % (
            q(i.get("issue_id")), q(i.get("type")), q(i.get("severity")), q(i.get("sheet")),
            q(i.get("object")), q(i.get("field")), q(i.get("description")),
            q(",".join(i.get("target_ids") or [])), 0))

    open(os.path.join(OUT, "01_schema.sql"), "w", encoding="utf-8", newline="\n").write(
        "\n".join(ddl) + "\n")
    open(os.path.join(OUT, "02_load.sql"), "w", encoding="utf-8", newline="\n").write(
        "\n".join(ins) + "\n")

    # 真跑一遍，证明能加载、能查
    db = os.path.join(OUT, "demo.db")
    if os.path.exists(db):
        os.remove(db)
    con = sqlite3.connect(db)
    con.executescript("\n".join(ddl))
    con.executescript("\n".join(ins))
    con.commit()
    print("  文件：%s" % OUT)
    print("  01_schema.sql %d 行 / 02_load.sql %d 条 INSERT" % (len(ddl), len(ins)))
    print("")
    for t in ("probe", "tank_snapshot", "stock_snapshot", "incoming_batch", "incoming_box",
              "feed_log", "measure", "component_reading", "issue"):
        try:
            n = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            print("  %-18s %d 行" % (t, n))
        except Exception as e:
            print("  %-18s (无) %s" % (t, e))
    con.close()


if __name__ == "__main__":
    main()
