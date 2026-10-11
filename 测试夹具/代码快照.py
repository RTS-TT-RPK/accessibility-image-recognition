# -*- coding: utf-8 -*-
"""通用性测试的硬证据 —— 对「不许改」的代码层打 sha256 快照，测试前后比对。

为什么需要它：
    「通用」的硬判据是 —— 面对从没见过的表型，只许新增数据（schemas/、runs/），
    validate_export.py 与 tools/ 下的脚本一行都不许改。
    口头说「我没改代码」不算数，指纹比对才算数。

用法（只用标准库，工作区自带的 python 就能跑）：

    python 测试夹具/代码快照.py --save      # 测试开始前：写下基线
    python 测试夹具/代码快照.py --check     # 测试结束后：比对
    python 测试夹具/代码快照.py             # 只看当前指纹

退出码：--check 时，0 = 与基线完全一致；1 = 有差异（通用性一票否决）。
"""
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "代码基线.json")

# 「改了就算通用性失败」的受保护层
PROTECTED = ["validate_export.py"]
PROTECTED_DIRS = [("tools", ".py")]

# 「允许增长、但要报出来」的记录层：新增正常，改了要说清
RECORD = ["aliases.json"]
RECORD_DIRS = [("schemas", ".json"), ("golden", "")]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def collect():
    """返回 (受保护文件 {相对路径: sha256}, 记录层文件 {相对路径: sha256})"""
    protected, record = {}, {}

    def add(store, rel):
        p = os.path.join(BASE, rel.replace("/", os.sep))
        if os.path.isfile(p):
            store[rel] = sha256_of(p)

    for rel in PROTECTED:
        add(protected, rel)
    for d, suffix in PROTECTED_DIRS:
        for dirpath, _dirnames, filenames in os.walk(os.path.join(BASE, d)):
            for fn in sorted(filenames):
                if suffix and not fn.endswith(suffix):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), BASE).replace(os.sep, "/")
                add(protected, rel)
    for rel in RECORD:
        add(record, rel)
    for d, suffix in RECORD_DIRS:
        for dirpath, _dirnames, filenames in os.walk(os.path.join(BASE, d)):
            for fn in sorted(filenames):
                if suffix and not fn.endswith(suffix):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), BASE).replace(os.sep, "/")
                add(record, rel)
    return protected, record


def digest_of(files):
    """把一组文件折成一个总指纹：一眼看变化，且不受文件顺序影响。"""
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(files[rel].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def show(protected, record, title):
    print("== %s ==" % title)
    print("工作区：%s" % BASE)
    print("")
    print("【受保护层】改动任意一个 = 通用性一票否决")
    for rel in sorted(protected):
        print("   %s  %s" % (protected[rel][:12], rel))
    print("   共 %d 个文件，总指纹 %s" % (len(protected), digest_of(protected)[:16]))
    print("")
    print("【记录层】新增属正常；改动要在汇报里说清")
    for rel in sorted(record):
        print("   %s  %s" % (record[rel][:12], rel))
    print("   共 %d 个文件，总指纹 %s" % (len(record), digest_of(record)[:16]))


def cmd_save():
    protected, record = collect()
    payload = {
        "_说明": "通用性测试代码基线。受保护层变了 = 通用性不成立。",
        "protected": protected,
        "record": record,
    }
    with open(BASELINE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    show(protected, record, "代码基线已写下")
    print("")
    print("基线文件：%s" % BASELINE)
    print("测试结束后跑：python 测试夹具/代码快照.py --check")
    return 0


def cmd_check():
    if not os.path.isfile(BASELINE):
        print("[错误] 没有基线文件：%s" % BASELINE)
        print("       先跑：python 测试夹具/代码快照.py --save")
        return 1
    with open(BASELINE, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    old_p, old_r = payload.get("protected") or {}, payload.get("record") or {}
    new_p, new_r = collect()

    failed = False
    print("== 通用性比对：受保护层（validate_export.py + tools/*.py）==")
    changed = sorted(k for k in set(old_p) & set(new_p) if old_p[k] != new_p[k])
    added = sorted(set(new_p) - set(old_p))
    removed = sorted(set(old_p) - set(new_p))
    if not (changed or added or removed):
        print("   一致：%d 个文件，指纹 %s（与基线相同）" % (len(new_p), digest_of(new_p)[:16]))
    else:
        failed = True
        for k in changed:
            print("   [已修改] %s" % k)
        for k in added:
            print("   [新增代码] %s" % k)
        for k in removed:
            print("   [删除] %s" % k)
        print("   基线指纹 %s -> 现在 %s" % (digest_of(old_p)[:16], digest_of(new_p)[:16]))

    print("")
    print("== 记录层（新增契约属正常；改动要说明）==")
    any_change = False
    for k in sorted(set(old_r) & set(new_r)):
        if old_r[k] != new_r[k]:
            print("   [已修改] %s" % k)
            any_change = True
    for k in sorted(set(new_r) - set(old_r)):
        print("   [新增] %s" % k)
        any_change = True
    for k in sorted(set(old_r) - set(new_r)):
        print("   [删除] %s" % k)
        any_change = True
    if not any_change:
        print("   一致：%d 个文件" % len(new_r))

    print("")
    if failed:
        print("结论：受保护层有改动 —— 通用性【不成立】（除非事先约定了允许改代码）")
        return 1
    print("结论：受保护层零改动 —— 通用性硬证据【通过】")
    return 0


def main():
    if "--save" in sys.argv:
        return cmd_save()
    if "--check" in sys.argv:
        return cmd_check()
    protected, record = collect()
    show(protected, record, "当前代码快照")
    print("")
    print("--save 写基线 / --check 与基线比对")
    return 0


if __name__ == "__main__":
    sys.exit(main())
