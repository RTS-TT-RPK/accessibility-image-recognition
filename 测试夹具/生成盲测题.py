# -*- coding: utf-8 -*-
"""通用性盲测题：6 张结构互不相同的陌生表型 + 标准答案。

出题人（本脚本的数据）知道答案；答题人（子代理）只拿到图片。
判定口径见 测试方案_通用性.md。

只写 测试夹具/盲测题/ 目录。不碰 runs/、inbox/、schemas/。
用法：<带Pillow的python> 生成盲测题.py
"""
import json
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.exit("需要 Pillow，请用捆绑 Python 运行")

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "盲测题", "图片")
KEY = os.path.join(BASE, "盲测题", "标准答案.json")
FONT_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts")
_C = {}


def font(size, bold=False):
    k = (size, bold)
    if k in _C:
        return _C[k]
    names = (["msyhbd.ttc", "simhei.ttf", "msyh.ttc"] if bold
             else ["msyh.ttc", "simsun.ttc", "simhei.ttf"])
    for n in names:
        p = os.path.join(FONT_DIR, n)
        if os.path.exists(p):
            try:
                _C[k] = ImageFont.truetype(p, size)
                return _C[k]
            except Exception:
                pass
    _C[k] = ImageFont.load_default()
    return _C[k]


def ctext(d, box, text, f, fill=(20, 20, 20)):
    if not text:
        return
    x0, y0, x1, y1 = box
    tb = d.textbbox((0, 0), str(text), font=f)
    d.text((x0 + (x1 - x0 - (tb[2] - tb[0])) / 2.0 - tb[0],
            y0 + (y1 - y0 - (tb[3] - tb[1])) / 2.0 - tb[1]), str(text), font=f, fill=fill)


def render(spec):
    """header_rows: [[(text,colspan,rowspan),...], ...]；rows/footer: [[cell,...]]"""
    widths = [w for _, w in spec["cols"]]
    fs = spec.get("fs", 22)
    f, fb = font(fs), font(fs, True)
    margin, row_h = 46, int(fs * 2.1)
    hrows = spec["header_rows"]
    level_h = int(fs * 2.2)
    head_h = level_h * len(hrows)
    n = len(spec["rows"]) + (1 if spec.get("footer") else 0)
    th = int(fs * 2.4) if spec.get("title") else 0
    sh = int(fs * 1.8) if spec.get("subtitle") else 0
    nh = int(fs * 2.2) if spec.get("note") else 0
    table_top = margin + th + sh + 10
    W = sum(widths) + margin * 2
    H = table_top + head_h + n * row_h + nh + margin
    img = Image.new("RGB", (W, H), (253, 253, 251))
    d = ImageDraw.Draw(img)
    if spec.get("title"):
        ctext(d, (margin, margin, W - margin, margin + th), spec["title"], font(int(fs * 1.5), True))
    if spec.get("subtitle"):
        d.text((margin, margin + th + 4), spec["subtitle"], font=font(fs - 3), fill=(70, 70, 70))
    xs = [margin]
    for w in widths:
        xs.append(xs[-1] + w)
    occupied = set()
    for r, row in enumerate(hrows):
        c = 0
        for (text, cs, rs) in row:
            while (r, c) in occupied:
                c += 1
            x0, x1 = xs[c], xs[c + cs]
            y0, y1 = table_top + r * level_h, table_top + (r + rs) * level_h
            d.rectangle([x0, y0, x1, y1], fill=(233, 237, 241), outline=(90, 90, 90))
            ctext(d, (x0, y0, x1, y1), text, fb)
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    occupied.add((rr, cc))
            c += cs
    def rowbox(i):
        y0 = table_top + head_h + i * row_h
        return y0, y0 + row_h
    for i, row in enumerate(spec["rows"]):
        y0, y1 = rowbox(i)
        for c, val in enumerate(row):
            ctext(d, (xs[c], y0, xs[c + 1], y1), val, f)
    if spec.get("footer"):
        i = len(spec["rows"])
        y0, y1 = rowbox(i)
        d.rectangle([xs[0], y0, xs[-1], y1], fill=(250, 246, 224))
        for c, val in enumerate(spec["footer"]):
            ctext(d, (xs[c], y0, xs[c + 1], y1), val, fb)
    for c in range(len(widths) + 1):
        d.line([(xs[c], table_top), (xs[c], table_top + head_h + n * row_h)], fill=(110, 110, 110))
    for r in range(n + 1):
        gy = table_top + head_h + r * row_h
        d.line([(xs[0], gy), (xs[-1], gy)], fill=(110, 110, 110))
    for r in range(len(hrows)):
        gy = table_top + r * level_h
        d.line([(xs[0], gy), (xs[-1], gy)], fill=(110, 110, 110))
    if spec.get("note"):
        d.text((margin, table_top + head_h + n * row_h + 6), spec["note"], font=font(fs - 2),
               fill=(60, 60, 60))
    geo = {"xs": xs, "row_y": [rowbox(i) for i in range(n)], "head_h": head_h,
           "table_top": table_top}
    return img, geo


def blur_cell(img, geo, r, c, radius=9.0, feather=8):
    x0, y0 = geo["xs"][c], geo["row_y"][r][0] + geo["head_h"] - geo["head_h"]
    x0, y0 = geo["xs"][c], geo["row_y"][r][0]
    x1, y1 = geo["xs"][c + 1], geo["row_y"][r][1]
    ex = (max(0, x0 - feather), max(0, y0 - feather), min(img.width, x1 + feather),
          min(img.height, y1 + feather))
    reg = img.crop(ex).filter(ImageFilter.GaussianBlur(radius))
    m = Image.new("L", (ex[2] - ex[0], ex[3] - ex[1]), 0)
    ImageDraw.Draw(m).rectangle([feather, feather, m.width - feather, m.height - feather], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(feather * 0.7))
    img.paste(reg, (ex[0], ex[1]), m)


def H(names):
    return [[(n, 1, 1) for n in names]]


SPECS = []
SPECS.append({
    "id": "G1_报价单", "sheet": "报价单", "date": "2026-10-11", "key_col": 0,
    "title": "报 价 单", "subtitle": "致：华东化工股份有限公司    报价日期：2026-10-11",
    "cols": [("序号", 80), ("品名", 140), ("规格", 170), ("数量", 90), ("单价(元)", 130),
             ("金额(元)", 150)],
    "header_rows": H(["序号", "品名", "规格", "数量", "单价(元)", "金额(元)"]),
    "rows": [["1", "IPAC", "工业级 200L", "12", "3,850.00", "46,200.00"],
             ["2", "DMF", "工业级 200L", "8", "4,120.00", "32,960.00"],
             ["3", "甲醇", "工业级 槽车", "30", "1,640.00", "49,200.00"]],
    "footer": ["合计", "—", "—", "50", "—", "128,360.00"],
    "note": "报价有效期 30 天，含税不含运费。",
    "blur": [(1, 5)],
    "考点": "纯字段表：没有成分/计量项，不该产生任何合计校验",
})
SPECS.append({
    "id": "G2_考勤表", "sheet": "考勤表", "date": "2026-10-11", "key_col": 1,
    "title": "车间考勤表", "subtitle": "班组：二班    2026-10-11 至 2026-10-12",
    "cols": [("日期", 150), ("姓名", 120), ("班次", 100), ("工时(小时)", 130), ("加班(小时)", 130)],
    "header_rows": H(["日期", "姓名", "班次", "工时(小时)", "加班(小时)"]),
    "rows": [["2026-10-11", "王建国", "白班", "8", "2"],
             ["2026-10-11", "李秀兰", "白班", "8", "0"],
             ["2026-10-11", "赵大勇", "夜班", "6", "1.5"],
             ["2026-10-12", "王建国", "白班", "8", "0"]],
    "note": "制表：人事科",
    "考点": "计量单位是「小时」不是 %：套用 100 上限就是假告警",
})
SPECS.append({
    "id": "G3_温度记录_英文", "sheet": "Temperature Log", "date": "2026-10-11", "key_col": 1,
    "title": "TEMPERATURE & HUMIDITY LOG", "subtitle": "Area: Warehouse B     Sheet 1 of 1",
    "cols": [("Date", 150), ("Time", 110), ("Temp(C)", 130), ("Humidity(%)", 150),
             ("Operator", 160)],
    "header_rows": H(["Date", "Time", "Temp(C)", "Humidity(%)", "Operator"]),
    "rows": [["2026-10-11", "08:00", "22.4", "55", "WANG JG"],
             ["2026-10-11", "12:00", "24.1", "51", "LI XL"],
             ["2026-10-11", "16:00", "23.7", "53", "LI XL"],
             ["2026-10-11", "20:00", "21.9", "58", "ZHAO DY"]],
    "note": "Recorder: QC Lab        Checked by: ZHANG",
    "考点": "全英文 + 摄氏温度：合计概念不适用",
})
SPECS.append({
    "id": "G4_点检表_竖排", "sheet": "设备点检表", "date": "2026-10-11", "key_col": 0,
    "title": "设备日常点检表", "subtitle": "设备：P-207A 离心泵    日期：2026-10-11",
    "cols": [("点检项目", 200), ("8:00", 130), ("12:00", 130), ("16:00", 130), ("20:00", 130)],
    "header_rows": H(["点检项目", "8:00", "12:00", "16:00", "20:00"]),
    "rows": [["压力 (MPa)", "0.62", "0.61", "0.63", "0.60"],
             ["温度 (C)", "68", "71", "73", "69"],
             ["振动 (mm/s)", "1.2", "1.3", "1.1", "1.4"],
             ["电流 (A)", "42.5", "43.1", "43.8", "42.0"],
             ["点检人", "王建国", "李秀兰", "李秀兰", "赵大勇"]],
    "note": "点检结论：全部正常。",
    "考点": "转置版式：字段在左、记录在右；「一行代表什么」不按常规",
})
SPECS.append({
    "id": "G5_化验单_两级表头", "sheet": "化验报告单", "date": "2026-10-11", "key_col": 1,
    "title": "化验报告单", "subtitle": "样品编号：LA-20261011-03    收样日期：2026-10-11",
    "cols": [("序号", 80), ("检测项目", 170), ("数值", 150), ("单位", 110), ("结论", 110)],
    "header_rows": [[("序号", 1, 2), ("检测项目", 1, 2), ("检测结果", 2, 1), ("结论", 1, 2)],
                    [("数值", 1, 1), ("单位", 1, 1)]],
    "rows": [["1", "外观", "无色透明", "—", "合格"],
             ["2", "水分", "0.27", "%", "合格"],
             ["3", "纯度", "99.84", "%", "合格"],
             ["4", "PH", "6.9", "—", "合格"]],
    "note": "检测：化验室    审核：张工",
    "考点": "两级合并表头：检测结果下面分「数值/单位」",
})
SPECS.append({
    "id": "G6_销售日报_钱表", "sheet": "销售日报", "date": "2026-10-11", "key_col": 0,
    "title": "销售日报", "subtitle": "日期：2026-10-11    单位：元",
    "cols": [("品类", 140), ("数量(件)", 130), ("单价(元)", 140), ("金额(元)", 180)],
    "header_rows": H(["品类", "数量(件)", "单价(元)", "金额(元)"]),
    "rows": [["IPAC", "120", "3,850.00", "462,000.00"],
             ["DMF", "80", "4,120.00", "329,600.00"],
             ["甲醇", "300", "1,640.00", "492,000.00"]],
    "footer": ["合计", "500", "—", "1,283,600.00"],
    "note": "制表：销售部",
    "考点": "钱表：合计 128 万，套 100% 上限就是荒谬的假告警",
})


def main():
    os.makedirs(OUT, exist_ok=True)
    key = {}
    for spec in SPECS:
        img, geo = render(spec)
        for (r, c) in spec.get("blur", []):
            blur_cell(img, geo, r, c)
        name = spec["id"] + ".png"
        img.save(os.path.join(OUT, name))
        cols = [n for n, _ in spec["cols"]]
        rows = []
        for i, r in enumerate(spec["rows"]):
            fields = {}
            for c, v in enumerate(r):
                if (i, c) in spec.get("blur", []):
                    continue
                fields[cols[c]] = v
            rows.append({"row_role": "data", "object_label": r[spec["key_col"]], "fields": fields})
        if spec.get("footer"):
            rows.append({"row_role": "summary", "object_label": spec["footer"][spec["key_col"]],
                         "fields": {cols[c]: v for c, v in enumerate(spec["footer"])}})
        if spec.get("note"):
            rows.append({"row_role": "note", "object_label": spec["note"][:20], "fields": {}})
        key[spec["id"]] = {
            "file": "图片/" + name, "sheet_label": spec["sheet"], "date_text": spec["date"],
            "columns": cols, "size": [img.width, img.height], "考点": spec["考点"],
            "行数": len(spec["rows"]),
            "期望不确定": [{"row": r, "col": cols[c]} for (r, c) in spec.get("blur", [])],
            "rows": rows,
        }
        print("  [生成] %-24s %sx%s  %s" % (name, img.width, img.height, spec["考点"][:28]))
    with open(KEY, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(key, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print("\n  标准答案.json 已写出，共 %d 题" % len(SPECS))


if __name__ == "__main__":
    main()
