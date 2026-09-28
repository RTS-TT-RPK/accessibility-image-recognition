# -*- coding: utf-8 -*-
"""收录 —— 把"人已经拍板过的东西"收进三份长期记忆文件。

为什么需要它：
    aliases.json（同类词词典）、golden/（标准答案库）、FEEDBACK_LOG.md（错题本）
    这三份文件目前全靠 AI 手工维护。问题是它们都是**长期资产**：
    错一条，往后每张图都跟着错；漏一条，同一句话下次还得再问一遍人。
    手工维护时最容易发生的两件事是"写错桶"和"凭空多出一个词"，
    所以本脚本只做一件事：**把人工决定里已经明说的内容抄进去，一个字的自己的
    判断都不加**。凡是没有人工决定明文支持的词/事实，一律不收录（宁可不收）。

三条子命令：
    python tools\\收录.py aliases  <run_dir>
        读 decisions.json 里明确声明了同类词映射的决定（如 `含水量=水`），
        把新词追加进 aliases.json 对应的桶。**只认人明说的映射**。

    python tools\\收录.py golden   <run_dir>
        把本次任务里**已确认**的事实行追加进 golden/golden.jsonl（一行一条 JSON）。
        只追加，永不修改或删除已有条目；同一 run_id+row_id 重跑会跳过（幂等）。

    python tools\\收录.py feedback <run_dir> --field <列名> --wrong <模型输出> \\
                                  --right <正确答案> --type <错误类型>
        往 FEEDBACK_LOG.md 的表格里追加一行，自动取下一个空闲的 FB-NNN 编号。

公共约定：
    * 每个子命令都支持 --dry-run（只报告、不写）；
    * 写之前**一律先备份**成 <文件名>.bak-<时间戳>，备份与目标同目录；
    * 一律"先写 .tmp 再 os.replace"，绝不产生半截文件；
    * 一律 **UTF-8 无 BOM**；.md 保持原有的 LF 换行（不擅自改成 CRLF）；
    * 跑完打印中文报告：新增了什么、跳过了什么、为什么跳过。
    * 加 --root <工作区> 可以指定工作区（默认从 <run_dir> 推断，测临时副本时用）。

安全：
    本脚本**只读** raw.json / issues.json / decisions.json / schemas/，
    只写 aliases.json、golden/golden.jsonl、FEEDBACK_LOG.md（各自先备份）。
    绝不碰 runs/、archive/、inbox/、pending/ 里的任何东西。
"""
import argparse
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import validate_export as ve          # noqa: E402  —— 唯一裁判，口径一律复用它的

GOLDEN_FILE = os.path.join("golden", "golden.jsonl")
FEEDBACK_FILE = "FEEDBACK_LOG.md"
ALIASES_FILE = "aliases.json"
NOW = lambda: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")          # noqa: E731
TODAY = lambda: datetime.datetime.now().strftime("%Y-%m-%d")                 # noqa: E731


# ---------------------------------------------------------------- 通用小工具
def log(msg):
    print("  " + msg)


def die(msg, code=2):
    print("[错误] " + msg)
    sys.exit(code)


def to_display(value):
    return ve.to_display(value)


def read_text(path, encoding="utf-8-sig"):
    """读文本。默认 utf-8-sig：顺手吃掉 BOM，读出来的是干净内容。"""
    if not os.path.exists(path):
        return None
    with io.open(path, "r", encoding=encoding) as f:
        return f.read()


def write_text_atomic(path, text, encoding="utf-8"):
    """先写 .tmp 再 os.replace：绝不产生半截文件。"""
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding=encoding, newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def backup(path):
    """写之前先备份（同目录、带微秒时间戳，不会因连跑两次而互相覆盖）。"""
    if not os.path.exists(path):
        return ""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = "%s.bak-%s" % (path, stamp)
    shutil.copy2(path, target)
    return target


def resolve_root(run_dir, override=None):
    """推断工作区根目录。validate_export 是拿 run_dir 的上级是不是 runs 来判断的。"""
    if override:
        root = os.path.abspath(override)
        if not os.path.isdir(root):
            die("--root 指定的目录不存在：%s" % root)
        return root
    run_dir = os.path.abspath(run_dir)
    parent = os.path.dirname(run_dir)
    if os.path.basename(parent) == "runs":
        return os.path.dirname(parent)
    return os.getcwd()


def load_run_json(run_dir, name):
    """读任务目录里的 JSON。刻意不用 ve.safe_read：那个用 utf-8 读，
    带 BOM 的历史文件会整份读不出来、还静默返回默认值。"""
    text = read_text(os.path.join(run_dir, name))
    if text is None:
        return {}
    try:
        doc = json.loads(text)
    except Exception:
        return {}
    return doc if isinstance(doc, dict) else {}


def issue_decisions_of(run_dir):
    """读 decisions.json 里的问题决定（用 utf-8-sig，兼容带 BOM 的历史文件）。"""
    path = os.path.join(run_dir, "decisions.json")
    text = read_text(path)
    if text is None:
        return []
    try:
        doc = json.loads(text)
    except Exception as exc:
        die("decisions.json 读不了（%s：%s）。请先修好它再收录。" % (type(exc).__name__, exc))
    if not isinstance(doc, dict):
        die("decisions.json 顶层不是对象，本脚本不接受这种结构。")
    out = []
    for item in (doc.get("issue_decisions") or []):
        if isinstance(item, dict) and to_display(item.get("decision")):
            out.append(item)
    return out


def issues_of(run_dir):
    doc = load_run_json(run_dir, "issues.json")
    return [i for i in (doc.get("issues") or []) if isinstance(i, dict)]


def apply_human_decisions(run, run_dir, schemas, issues):
    """把人工决定落到 raw 数据上，返回 (落实后的 run, 未解决的行号集合)。

    为什么这里要重跑一遍裁判的逻辑：golden 里要收的是**落实决定之后**的值
    （人工确认过的正确答案），而不是模型当初识别出来的原值。
    实现上直接复刻 validate_export.main() 的两遍校验顺序：
      第一遍按原样校验 -> apply_decisions -> 第二遍重新校验 -> 回填 human_decision
      -> 通过类决定标 accepted -> 剩下的就是"未解决"。
    这样"已确认"的口径与裁判百分之百一致，不会出现"脚本说确认了、裁判说没有"。
    """
    decisions_by_issue, _by_image = ve.load_decisions(run_dir)
    run, _sanity = ve.normalize_run(run)

    unreg1 = {}
    pass1 = ve.build_issues(run, schemas, unreg1, ve.load_known_terms(os.path.dirname(run_dir)))
    pass1 += ve.detect_duplicates(run)

    ve.apply_decisions(run, decisions_by_issue, pass1)

    unreg2 = {}
    issues2 = ve.build_issues(run, schemas, unreg2, ve.load_known_terms(os.path.dirname(run_dir)))
    issues2 += ve.detect_duplicates(run)

    for item in issues2:
        d = decisions_by_issue.get(item["issue_id"])
        if d and d.get("decision"):
            item["human_decision"] = d["decision"]

    accepted_ids = set()
    for item in issues2:
        if ve.is_approve(item.get("human_decision")):
            item["machine_status"] = "accepted"
            accepted_ids.add(item["issue_id"])
    blocking = [i for i in issues2 if i["issue_id"] not in accepted_ids]

    detail, facts, unresolved_ids = ve.collect_rows(run, blocking, schemas)
    return run, detail, facts, unresolved_ids


def workspace_schemas(root):
    if os.path.isdir(os.path.join(root, "schemas")):
        return ve.load_schemas(root)
    return {"*": ve.normalize_schema(ve.DEFAULT_SCHEMA)}


# ================================================================ aliases
# 一条"同类词映射"决定长这样：含水量=水（左=图上看到的新词，右=已确认的标准词）。
# 只有当**右边**是 aliases.json 里已经确认过的词时才收：
# 这样"桶"是现成的，脚本不需要（也不允许）自己判断新词属于水类还是杂类。
MAP_SPLIT = re.compile(r"[;；,，\n]+")
MAP_PAIR = re.compile(r"^\s*(.+?)\s*[=:：]\s*(.+?)\s*$")


def load_alias_file(path):
    """读 aliases.json，返回 (原文本, 解析后的文档)。

    文件不存在返回 ("", {})。文件坏了返回 (None, None) 让调用方自己决定——
    **绝不能把坏文件当空文档然后覆盖它**。
    """
    if not os.path.exists(path):
        return "", {}
    text = read_text(path)
    if text is None:
        return None, None
    try:
        doc = json.loads(text)
    except Exception as exc:
        log("[错误] aliases.json 读不了（%s：%s）。" % (type(exc).__name__, exc))
        log("       本脚本不会覆盖读不懂的文件，请先手工修好它。")
        return None, None
    if not isinstance(doc, dict):
        log("[错误] aliases.json 顶层不是对象（实际是 %s）。" % type(doc).__name__)
        return None, None
    return text, doc


def confirmed_owner(doc, word):
    """word 是否已被**人工确认过**的桶收录；是则返回那个桶的键名，否则返回 None。

    只认 "确认为真" 的桶：键名能归一到 water/misc，且键名带 _已确认_ 前缀。
    裸桶（"水类": []）是"待确认"的意思，里面的词还不算数——
    AGENTS.md 说 aliases.json 只存用户拍板过的映射。
    """
    word = to_display(word)
    if not word:
        return None
    for key, words in doc.items():
        if not isinstance(words, (list, tuple)):
            continue
        if ve.canonical_alias_key(key) is None:
            continue
        if "_已确认_" not in key and "已确认" not in key:
            continue
        if word in [to_display(w) for w in words]:
            return key
    return None


def resolve_target_bucket(doc, word):
    """确定新词该写进哪个桶，返回 (桶键, 说明)，找不到返回 (None, 原因)。

    优先级：已确认的桶 > 文件里已有的同名桶 > 默认词表 > （按默认词表新建桶）。
    全都不沾边时**不新建桶**——新建等于替用户决定业务口径（红线）。

    "这个标准词属于哪一类"一律用 ve.classify_component 判断
    （它认识默认词表、也认识 aliases.json 里的词），本脚本不自带业务知识。
    """
    word = to_display(word)
    if not word:
        return None, "右边是空的"

    # 1) 已确认的桶里已经有这个标准词 —— 最可靠
    owner = confirmed_owner(doc, word)
    if owner:
        return owner, "「%s」已在 %s 里" % (word, owner)

    # 2) 文件里已有的裸桶（非 _已确认_ 前缀）——沿用文件自己的写法
    for key, words in doc.items():
        if not isinstance(words, (list, tuple)):
            continue
        if "_已确认_" in key or "已确认" in key:
            continue
        if word in [to_display(w) for w in words]:
            return key, "「%s」已列在 %s 里（该桶还没标已确认）" % (word, key)

    # 3) 默认词表 / 全局判断：确定类别之后，优先写进已确认桶
    kind, _registered = ve.classify_component(word, ve.DEFAULT_ALIASES)
    if kind in ("water", "misc"):
        for key in doc:
            if not isinstance(doc[key], (list, tuple)):
                continue
            if ve.canonical_alias_key(key) == kind and "已确认" in key:
                return key, "「%s」按默认词表属于 %s 类" % (word, kind)
        for key in doc:
            if not isinstance(doc[key], (list, tuple)):
                continue
            if ve.canonical_alias_key(key) == kind:
                return key, "「%s」按默认词表属于 %s 类" % (word, kind)
        new_key = "_已确认_%s类" % ("水" if kind == "water" else "杂")
        return new_key, "「%s」按默认词表属于 %s 类（新建 %s 桶）" % (word, kind, new_key)

    return None, ("认不出「%s」属于水类还是杂类，且它不是 aliases.json 里已确认的词 —— "
                  "不替用户决定，跳过（请先在 aliases.json 里手工确认它的归类）" % word)


def existing_words(doc):
    """aliases.json 里出现过的所有词（不论在哪个桶）。"""
    out = set()
    for words in doc.values():
        if isinstance(words, (list, tuple)):
            for w in words:
                w = to_display(w)
                if w:
                    out.add(w)
    return out


def cmd_aliases(args):
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        die("目录不存在：%s" % run_dir)
    root = resolve_root(run_dir, args.root)
    path = os.path.join(root, ALIASES_FILE)

    print("== 收录同类词（aliases）==")
    print("  任务：%s" % run_dir)
    print("  词表：%s" % path)
    decisions = issue_decisions_of(run_dir)
    issues = issues_of(run_dir)
    issue_by_id = {}
    for issue in issues:
        issue_by_id.setdefault(to_display(issue.get("issue_id")), issue)
    print("  人工决定：%d 条；问题账本：%d 条" % (len(decisions), len(issues)))

    _text, doc = load_alias_file(path)
    if doc is None:
        die("aliases.json 读不了，本次没有写任何内容。", 2)
    have = existing_words(doc)
    additions, skipped = [], []

    for item in decisions:
        iid = to_display(item.get("issue_id"))
        decision = to_display(item.get("decision"))
        issue = issue_by_id.get(iid) or {}
        # 只处理"新出现的成分词"这类问题。别的类型里的等号是字段修正
        # （如 `物料=甲醇`），收进 aliases 会把字段值错当成分词。
        itype = to_display(issue.get("type"))
        if itype != "UNREGISTERED_TERM":
            skipped.append((iid, decision, "问题类型是 %s，不是 UNREGISTERED_TERM"
                            % (itype or "未知（账本里找不到这条）")))
            continue
        for part in MAP_SPLIT.split(decision):
            part = part.strip()
            if not part:
                continue
            m = MAP_PAIR.match(part)
            if not m:
                skipped.append((iid, decision, "「%s」不是 新词=已确认词 的写法" % part))
                continue
            new_word, anchor = to_display(m.group(1)), to_display(m.group(2))
            if not new_word or not anchor:
                skipped.append((iid, decision, "「%s」左右有一边是空的" % part))
                continue
            if new_word in have:
                skipped.append((iid, decision, "「%s」已在 aliases.json 里，不重复收录" % new_word))
                continue
            # 冲突检查：右边若是**另一个**类别的已确认词，说明这句话自相矛盾
            owner_new = confirmed_owner(doc, new_word)
            owner_anchor = confirmed_owner(doc, anchor)
            if owner_new and owner_anchor and owner_new != owner_anchor:
                skipped.append((iid, decision,
                                "「%s」已在 %s，而「%s」在 %s —— 自相矛盾，跳过"
                                % (new_word, owner_new, anchor, owner_anchor)))
                continue
            bucket, why = resolve_target_bucket(doc, anchor)
            if bucket is None:
                skipped.append((iid, decision, why))
                continue
            additions.append((bucket, new_word, anchor, why))
            have.add(new_word)
            doc.setdefault(bucket, [])

    # 同一个词被多条决定提到时只收一次
    seen, unique = set(), []
    for bucket, word, anchor, why in additions:
        if word in seen:
            continue
        seen.add(word)
        unique.append((bucket, word, anchor, why))

    if not unique:
        print("")
        log("没有可收录的同类词。")
        if skipped:
            log("跳过的决定：")
            for iid, decision, why in skipped:
                print("      %s：%s —— %s" % (iid or "（没问题编号）", decision, why))
        log("aliases.json 未改动。")
        return 0

    print("")
    log("将收录 %d 个词：" % len(unique))
    for bucket, word, anchor, why in unique:
        print("      %s <-「%s」（依据：%s；%s）" % (bucket, word, anchor, why))
    if skipped:
        log("跳过 %d 条：" % len(skipped))
        for iid, decision, why in skipped:
            print("      %s：%s —— %s" % (iid or "（没问题编号）", decision, why))

    if args.dry_run:
        print("")
        log("--dry-run：以上是将要追加的内容，**没有写任何文件**。")
        return 0

    for bucket, word, _anchor, _why in unique:
        cur = doc.get(bucket)
        if not isinstance(cur, list):
            cur = []
            doc[bucket] = cur
        if word not in [to_display(w) for w in cur]:
            cur.append(word)
    new_text = json.dumps(doc, ensure_ascii=False, indent=2)
    bak = backup(path)
    write_text_atomic(path, new_text)
    print("")
    log("已写入：%s（UTF-8 无 BOM）" % path)
    if bak:
        log("原文件已备份：%s" % os.path.basename(bak))
    log("变化：")
    for bucket, word, _anchor, _why in unique:
        print("      + %s: %s" % (bucket, word))
    return 0


# ================================================================ golden
def golden_record(run, row_id, sheet, fields, components, source_row, stamp):
    return {
        "recorded_at": stamp,
        "run_id": to_display(run.get("run_id")),
        "row_id": to_display(row_id),
        "sheet": to_display(sheet),
        "image_id": to_display(source_row.get("image_id")),
        "object_label": to_display(source_row.get("object_label")),
        "source_type": to_display(run.get("source_type")),
        "fields": dict(fields),
        "components": components,
    }


def record_digest(rec):
    """一条事实的"内容指纹"——**不含 recorded_at**，所以同一份内容重复收录指纹相同。

    用它来判断幂等：同一次任务重跑一次，若人工决定没变，内容指纹就不变，
    这时应当**什么都不写**。"只是又跑了一遍"不该在 golden 里堆重复条目。
    """
    body = dict(rec)
    body.pop("recorded_at", None)
    body.pop("supersedes", None)
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()[:16]


def cmd_golden(args):
    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        die("目录不存在：%s" % run_dir)
    root = resolve_root(run_dir, args.root)
    path = os.path.join(root, GOLDEN_FILE)

    print("== 收录标准答案（golden）==")
    print("  任务：%s" % run_dir)
    print("  台账：%s" % path)

    raw = load_run_json(run_dir, "raw.json")
    if not raw:
        die("读不到 %s/raw.json，无法收录。" % run_dir)
    schemas = workspace_schemas(root)
    issues = issues_of(run_dir)
    if not issues:
        log("[注意] 没找到 issues.json —— 本次会按「没有问题」处理，"
            "也就是**所有数据行都算已确认**。若校验还没跑过，请先跑 validate_export.py。")

    run, detail, facts, unresolved = apply_human_decisions(raw, run_dir, schemas, issues)
    blocked_rows = set(unresolved)
    confirmed = [rec for rec in detail if rec["row_id"] not in blocked_rows]
    print("  数据行：%d 行；其中未解决（不进 golden）：%d 行"
          % (len(detail), len(detail) - len(confirmed)))
    if blocked_rows:
        log("未解决的行（跳过，未确认的事实绝不进 golden）：%s"
            % "、".join(sorted(str(r) for r in blocked_rows)))

    rows_by_id = {}
    for row in run.get("rows", []):
        rows_by_id[to_display(row.get("row_id"))] = row

    stamp = NOW()
    records = []
    for rec in confirmed:
        rid = to_display(rec["row_id"])
        src = rows_by_id.get(rid) or {}
        comps = []
        for comp in (src.get("components") or []):
            comps.append({
                "name": to_display(comp.get("name")),
                "raw_value": to_display(comp.get("raw_value")),
                "value": to_display(comp.get("value")),
                "unit": to_display(comp.get("unit")),
                "from_human": bool(comp.get("from_human")),
            })
        records.append(golden_record(run, rid, rec.get("sheet", ""),
                                     rec.get("fields") or {}, comps, src, stamp))

    old_text = read_text(path, encoding="utf-8") or ""
    lines = [ln for ln in old_text.split("\n") if ln.strip()]
    existing_objs, bad_lines = [], []
    for idx, ln in enumerate(lines):
        try:
            obj = json.loads(ln)
        except Exception:
            bad_lines.append(idx + 1)
            continue
        if isinstance(obj, dict):
            existing_objs.append(obj)
    if bad_lines:
        log("[注意] golden.jsonl 里有 %d 行读不出 JSON（第 %s 行）—— "
            "本脚本**照原样保留**它们，只往后追加。" % (len(bad_lines),
                                                        "、".join(str(n) for n in bad_lines)))

    latest = {}                     # (run_id, row_id) -> 最后一条的内容指纹
    for obj in existing_objs:
        key = (to_display(obj.get("run_id")), to_display(obj.get("row_id")))
        latest[key] = record_digest(obj)
    print("  台账已有：%d 条可解析记录（%d 个 任务+行号）"
          % (len(existing_objs), len(latest)))

    fresh, updates, same = [], [], []
    for rec in records:
        key = (rec["run_id"], rec["row_id"])
        digest = record_digest(rec)
        if key not in latest:
            fresh.append(rec)
        elif latest[key] == digest:
            same.append(rec)
        else:
            rec = dict(rec)
            rec["supersedes"] = latest[key]
            updates.append(rec)

    if same:
        log("内容与台账完全一致、跳过（幂等）：%d 条 —— %s"
            % (len(same), "、".join("%s/%s" % (r["run_id"], r["row_id"]) for r in same)))

    if not fresh and not updates:
        print("")
        log("没有需要新增的事实行，golden 未改动。")
        return 0

    print("")
    if fresh:
        log("新增 %d 条：" % len(fresh))
        for rec in fresh:
            print("      %s / %s（%s）字段 %d 个、计量项 %d 个"
                  % (rec["run_id"], rec["row_id"], rec["sheet"],
                     len(rec["fields"]), len(rec["components"])))
    if updates:
        log("人工决定变了、按最新值重录 %d 条（**只追加新版本**，旧条目原样保留）：" % len(updates))
        for rec in updates:
            print("      %s / %s（%s）supersedes=%s"
                  % (rec["run_id"], rec["row_id"], rec["sheet"], rec["supersedes"]))

    if args.dry_run:
        print("")
        log("--dry-run：以上是将会追加的内容，**没有写任何文件**。")
        return 0

    out = old_text
    if out and not out.endswith("\n"):
        out += "\n"
    for rec in fresh + updates:
        out += json.dumps(rec, ensure_ascii=False) + "\n"
    bak = backup(path)
    write_text_atomic(path, out, encoding="utf-8")
    print("")
    log("已追加：%s（UTF-8 无 BOM；原有 %d 行一条都没动）" % (path, len(lines)))
    if bak:
        log("原文件已备份：%s" % os.path.basename(bak))
    else:
        log("（golden 文件原本不存在，本次新建）")
    return 0


# ================================================================ feedback
FB_ID = re.compile(r"^\s*\|?\s*(?:（示例）)?\s*FB-(\d{1,4})\b")
TABLE_ROW = re.compile(r"^\s*\|")


def guess_sheet(run, run_dir, field, explicit=None):
    """猜这一行该写哪个表型。判不出就留空——**不编**。"""
    if explicit:
        return explicit
    raw = load_run_json(run_dir, "raw.json")
    if not raw:
        raw = run or {}
    sheets = []
    for row in raw.get("rows", []):
        f = to_display(field)
        if f and f in (row.get("fields") or {}):
            s = to_display(row.get("sheet"))
            if s and s not in sheets:
                sheets.append(s)
    if len(sheets) == 1:
        return sheets[0]
    if len(sheets) > 1:
        log("[注意] 「%s」同时出现在多个工作表（%s）里，影响表型留空不猜；"
            "需要的话用 --sheet 指定。" % (field, "、".join(sheets)))
        return ""
    return ""


def parse_feedback_table(text):
    """解析错题本。返回 (行列表, 表头行下标, 分隔行下标)。行列表不含换行符。"""
    lines = text.split("\n")
    header_idx, sep_idx = None, None
    for idx, ln in enumerate(lines):
        if not TABLE_ROW.match(ln):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if header_idx is None and "FB-ID" in cells:
            header_idx = idx
            continue
        if header_idx is not None and sep_idx is None and cells and \
                all(set(c) <= set("-: ") and c for c in cells):
            sep_idx = idx
            continue
    return lines, header_idx, sep_idx


def next_fb_id(lines, header_idx):
    used = set()
    for ln in lines[header_idx + 1:] if header_idx is not None else lines:
        m = FB_ID.match(ln)
        if m and "(示例)" not in ln and "（示例）" not in ln:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    return "FB-%03d" % n, sorted(used)


def clean_cell(value):
    """表格单元格里不能出现裸的竖线或换行，否则整张表会散架。"""
    text = to_display(value).replace("|", "／").replace("\n", " ").replace("\r", " ")
    return text


def auto_error_type(wrong, right):
    """--type 没给时的保守默认：只在明显是数值误读时才下结论，其余写"人工指出"。"""
    wn, rn = to_display(wrong), to_display(right)
    num = re.compile(r"^[+\-]?[\d.,]+%?$")
    if num.match(wn) and num.match(rn):
        return "视觉误读"
    return "人工指出（未分类）"


def cmd_feedback(args):
    run_dir = os.path.abspath(args.run_dir) if args.run_dir else ""
    if run_dir and not os.path.isdir(run_dir):
        die("目录不存在：%s" % run_dir)
    root = resolve_root(run_dir or os.getcwd(), args.root)
    path = os.path.join(root, FEEDBACK_FILE)

    print("== 收录错题（feedback）==")
    print("  任务：%s" % (run_dir or "（未指定）"))
    print("  错题本：%s" % path)

    wrong = to_display(args.wrong)
    right = to_display(args.right)
    field = to_display(args.field)
    if not wrong or not right:
        die("--wrong 和 --right 都必须给值（记的是「模型输出错在哪、正确答案是什么」）。")
    if wrong == right:
        die("--wrong 与 --right 相同，这不是一条错题，不收录。")
    etype = to_display(args.type) or auto_error_type(wrong, right)

    text = read_text(path)
    if text is None:
        die("找不到错题本：%s" % path)
    lines, header_idx, sep_idx = parse_feedback_table(text)
    if header_idx is None:
        die("错题本里找不到表头（应含「FB-ID」列）。为避免写坏它，本次不写任何内容。")
    fb_id, used = next_fb_id(lines, header_idx)
    sheet = guess_sheet(None, run_dir, field, args.sheet) if run_dir else to_display(args.sheet)

    row = "| %s | %s | %s | %s | %s | %s | 1 | %s | 仅记录 | 关闭 |" % (
        fb_id, TODAY(), clean_cell(sheet) or "—", clean_cell(field) or "—",
        clean_cell(wrong), clean_cell(right), clean_cell(etype))
    print("  已有编号：%s" % ("、".join("FB-%03d" % n for n in used) or "（无）"))
    print("")
    log("将追加一行：")
    print("      " + row)
    if not to_display(sheet):
        log("[注意] 影响表型留空（没猜出来）—— 后续可用 --sheet 指定。")
    if not to_display(args.type):
        log("[注意] 没给 --type，按保守值填了「%s」。" % etype)

    if args.dry_run:
        print("")
        log("--dry-run：以上是将会追加的行，**没有写任何文件**。")
        return 0

    # 插到最后一行表格行之后（表格后面若还有正文，正文保持在后面，原样不动）
    last_table = header_idx
    for idx in range(header_idx + 1, len(lines)):
        if TABLE_ROW.match(lines[idx]):
            last_table = idx
    if last_table < (sep_idx if sep_idx is not None else header_idx):
        last_table = sep_idx if sep_idx is not None else header_idx
    new_lines = lines[:last_table + 1] + [row] + lines[last_table + 1:]
    new_text = "\n".join(new_lines)
    bak = backup(path)
    write_text_atomic(path, new_text)
    print("")
    log("已追加：%s（插在第 %d 行之后；原有 %d 行一字未动；保留 LF 换行）"
        % (path, last_table + 1, len(lines)))
    if bak:
        log("原文件已备份：%s" % os.path.basename(bak))
    return 0


# ================================================================ CLI
def build_parser():
    parser = argparse.ArgumentParser(
        description="把人工拍板过的内容收进 aliases.json / golden / FEEDBACK_LOG.md（只抄不猜）")
    parser.add_argument("--root", help="工作区根目录（默认从 <run_dir> 推断；测临时副本时用）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("aliases", help="把人工确认过的同类词收进 aliases.json")
    p1.add_argument("run_dir")
    p1.add_argument("--dry-run", action="store_true")
    p1.set_defaults(func=cmd_aliases)

    p2 = sub.add_parser("golden", help="把已确认的事实行追加进 golden/golden.jsonl")
    p2.add_argument("run_dir")
    p2.add_argument("--dry-run", action="store_true")
    p2.set_defaults(func=cmd_golden)

    p3 = sub.add_parser("feedback", help="往 FEEDBACK_LOG.md 追加一行错题")
    p3.add_argument("run_dir", nargs="?", default="")
    p3.add_argument("--field", required=True, help="出错字段（列名 / 计量项名）")
    p3.add_argument("--wrong", required=True, help="模型当时的输出")
    p3.add_argument("--right", required=True, help="人工确认的正确答案")
    p3.add_argument("--type", default="", help="错误类型（不给则按保守值填）")
    p3.add_argument("--sheet", default="", help="影响表型（不给则尝试从 raw.json 推断）")
    p3.add_argument("--dry-run", action="store_true")
    p3.set_defaults(func=cmd_feedback)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
