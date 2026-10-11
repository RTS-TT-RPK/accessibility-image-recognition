# -*- coding: utf-8 -*-
"""盲测评分：格级命中率 / 编造数 / 漏行 / 不确定标注。

对齐规则（v2 —— v1 有 bug）：
  v1 把"非数据行"（子代理照规矩把抬头行/页脚写成 row_role=note）也拿去降级配对，
  结果每张图都整体错位一行，把正确读数误判成"编造"。
  v2：**只在 row_role=data 的行之间对齐**；先按 object_label 精确配，配不上的按顺序配。

用法：python 测试夹具/评分.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
KEY = os.path.join(BASE, "盲测题", "标准答案.json")
ANS = os.path.join(BASE, "盲测题", "答卷")


def norm(v):
    s = str("" if v is None else v).strip()
    out = []
    for ch in s:
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            out.append(chr(o - 0xFEE0))
        elif o == 0x3000:
            out.append(" ")
        elif ch in "，、；：":
            out.append({"、": " ", "，": ",", "；": ";", "：": ":"}[ch])
        else:
            out.append(ch)
    s = "".join(out)
    for a, b in ((" ", ""), ("\t", ""), (",", ""), ("\u3000", ""),
                 ("—", "-"), ("－", "-"), ("–", "-"), ("−", "-"), ("~", "-")):
        s = s.replace(a, b)
    return s.lower()


def vals(row):
    return [v for v in (row.get("fields") or {}).values() if str(v or "").strip()]


def is_data(r):
    return (r.get("row_role") or "data") == "data"


def align(krows, drows):
    """先按 object_label 精确配，配不上的按顺序配。只在数据行之间配。"""
    matched, used = {}, set()
    for ki, k in enumerate(krows):
        lab = norm(k.get("object_label"))
        if not lab:
            continue
        for di, d in enumerate(drows):
            if di not in used and norm(d.get("object_label")) == lab:
                matched[ki] = di
                used.add(di)
                break
    pool = [di for di in range(len(drows)) if di not in used]
    pi = 0
    for ki in range(len(krows)):
        if ki in matched:
            continue
        if pi < len(pool):
            matched[ki] = pool[pi]
            pi += 1
    return matched


def grade_one(iid, key):
    path = os.path.join(ANS, iid + ".json")
    if not os.path.isfile(path):
        return None, "答卷缺失：%s" % path
    with open(path, "r", encoding="utf-8") as fh:
        try:
            got = json.load(fh)
        except Exception as exc:
            return None, "答卷 JSON 解析不了：%s" % exc
    grows = [r for r in (got.get("rows") or []) if isinstance(r, dict)]
    drows = [r for r in grows if is_data(r)]
    krows = [r for r in key["rows"] if r["row_role"] == "data"]
    m = align(krows, drows)
    rep = {"id": iid, "hit": 0, "miss": 0, "fab": 0, "miss_detail": [], "fab_detail": [],
           "matched": len(m), "expected": len(krows), "dr": len(drows),
           "unc": [], "unc_expected": key.get("期望不确定") or []}
    for ki, k in enumerate(krows):
        di = m.get(ki)
        if di is None:
            rep["miss"] += len(vals(k))
            continue
        d = drows[di]
        have = [norm(v) for v in vals(d)]
        exp = [norm(v) for v in vals(k)]
        uncf = {norm(u.get("field")) for u in (d.get("uncertain") or [])}
        for u in (d.get("uncertain") or []):
            rep["unc"].append((k.get("object_label"), u.get("field"), u.get("note", "")[:40]))
        for v in vals(k):
            if norm(v) in have:
                rep["hit"] += 1
            else:
                rep["miss"] += 1
                rep["miss_detail"].append((k.get("object_label"), v))
        for f, v in (d.get("fields") or {}).items():
            if not str(v or "").strip() or norm(v) in exp or norm(f) in uncf:
                continue
            rep["fab"] += 1
            rep["fab_detail"].append((k.get("object_label"), f, v))
    rep["_d"] = (drows, m)
    return rep, None


# ---------------------------------------------------------------- 第二道：列级校验
# 第一道只查"这个值在不在这行里"，**列串位查不出来**。这一道把列名对齐后逐列比。

def sim(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0
    if a == b:
        return 1000
    if a in b or b in a:
        return 800
    best = 0
    for i in range(len(a)):
        for j in range(i + 1, len(a) + 1):
            if a[i:j] in b:
                best = max(best, j - i)
    return int(best * 100.0 / max(len(a), len(b))) if best else 0


def col_map(kcols, acols):
    """键列名 -> 答卷列名，贪心一对一。"""
    cand = []
    for kc in kcols:
        for ac in acols:
            s = sim(kc, ac)
            if s > 0:
                cand.append((s, kc, ac))
    cand.sort(key=lambda x: -x[0])
    m, uk, ua = {}, set(), set()
    for s, kc, ac in cand:
        if kc in uk or ac in ua:
            continue
        m[kc] = ac
        uk.add(kc)
        ua.add(ac)
    return m


def grade_cols(key, drows, m):
    krows = [r for r in key["rows"] if r["row_role"] == "data"]
    kcols = key["columns"]
    acols = []
    for d in drows:
        for f in (d.get("fields") or {}):
            if f not in acols:
                acols.append(f)
    cm = col_map(kcols, acols)
    hit = miss = 0
    bad = []
    for ki, k in enumerate(krows):
        di = m.get(ki)
        if di is None:
            continue
        d = drows[di]
        for kc in kcols:
            kv = (k.get("fields") or {}).get(kc)
            if not str(kv or "").strip():
                continue
            ac = cm.get(kc)
            av = (d.get("fields") or {}).get(ac) if ac else None
            if norm(kv) == norm(av):
                hit += 1
            else:
                miss += 1
                bad.append((k.get("object_label"), kc, kv, ac, av))
    return cm, hit, miss, bad


def main():
    with open(KEY, "r", encoding="utf-8") as fh:
        key = json.load(fh)
    tot = dict(hit=0, miss=0, fab=0, matched=0, expected=0, dr=0, chit=0, cmiss=0)
    nun = 0
    for iid, k in key.items():
        rep, err = grade_one(iid, k)
        if rep is None:
            print("== %s ==\n  %s\n" % (iid, err))
            continue
        total = rep["hit"] + rep["miss"]
        rate = (rep["hit"] / float(total) * 100) if total else 0.0
        print("== %s ==  [%s]" % (iid, k["考点"]))
        print("  数据行对齐：%d/%d（答卷给了 %d 个数据行）"
              % (rep["matched"], rep["expected"], rep["dr"]))
        print("  格子命中：%d/%d = %.1f%%   漏格：%d   **编造：%d**"
              % (rep["hit"], total, rate, rep["miss"], rep["fab"]))
        print("  不确定标注：%d 条（图上故意做糊 %d 格）" % (len(rep["unc"]), len(rep["unc_expected"])))
        d2, m2 = rep["_d"]
        cm, chit, cmiss, cbad = grade_cols(k, d2, m2)
        ct = chit + cmiss
        rep["chit"], rep["cmiss"] = chit, cmiss
        print("  列级命中：%d/%d = %.1f%%" % (chit, ct, (chit / float(ct) * 100) if ct else 0))
        print("  列映射：%s" % "  ".join("%s→%s" % (a, b) for a, b in cm.items()))
        for b in cbad[:8]:
            print("    [串位/错值] %s / %s = %r  →答卷该列取到 %r（列名 %s）" % b)
        for d in rep["miss_detail"][:8]:
            print("    [漏] %s -> %r" % d)
        for d in rep["fab_detail"][:8]:
            print("    [编造] %s / %s = %r" % d)
        for u in rep["unc"]:
            print("    [不确定] %s / %s —— %s" % u)
        print("")
        for kk in ("hit", "miss", "fab", "matched", "expected", "dr", "chit", "cmiss"):
            tot[kk] += rep.get(kk, 0)
        nun += len(rep["unc"])
    t = tot["hit"] + tot["miss"]
    print("=" * 60)
    print("  合计：格级命中 %d/%d = %.1f%%"
          % (tot["hit"], t, (tot["hit"] / float(t) * 100) if t else 0))
    print("  漏格 %d   编造 %d   数据行对齐 %d/%d"
          % (tot["miss"], tot["fab"], tot["matched"], tot["expected"]))
    print("  不确定标注 %d 条" % nun)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
