# -*- coding: utf-8 -*-
"""DSH 专用插件安装器 —— 把 dsh-table-image 挂进 DSH（装 / 查 / 卸）。

    python tools/dsh_plugin_setup.py --status      看装没装、挂没挂上
    python tools/dsh_plugin_setup.py               装（或更新）
    python tools/dsh_plugin_setup.py --dry-run     只显示会改什么，不动任何文件
    python tools/dsh_plugin_setup.py --uninstall   卸掉（配置改回去，备份留着）

（日常双击 tools\\安装DSH插件（双击）.bat 即可。）

它动的东西一共四处，每一处都遵守本机踩过坑总结出来的四条硬规矩：

  1. 改前备份 —— cordis.patch.yml 和 profile 的 package.json 都先存一份带时间戳的备份；
  2. 只动"顶格条目"—— 缩进在 insert 块里的 `- id:` 绝对不碰（碰了 YAML 层级就错乱，
     严重时 DSH 起不来）；DSH 只认**最后一条**匹配的覆盖项，所以也找最后一条；
  3. 原子写 —— 先写 .tmp，写完自检通过才替换；自检不过就放弃，绝不覆盖原配置；
  4. 写后自检 —— 文件非空、没有 tab 缩进、原有的关键挂载行还在、新条目出现且只出现一次。

四个动作：
  · 在 profile 的 node_modules 里做一个指向插件源目录的 junction（不需要管理员权限）；
  · profile 的 package.json：加 `link:` 依赖 + 把包名加进 dsh.profile.bundles；
  · profile 的 cordis.patch.yml：末尾追加（或改最后一条）顶格覆盖项 `- id: table-image`；
  · 把技能装进 DSH 的技能目录（~/.dsh/skills，workspace.txt 用 UTF-8 带 BOM）。

装完必须**重启 DSH**（宿主侧代码换新要重启；只改 patch 才是热加载），界面要按 F5。

退出码：0 成功；2 出错（找不到插件源、自检不过等）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_ID = "table-image"
PLUGIN_PKG = "dsh-table-image"
PLUGIN_SRC = os.path.join(BASE, "插件源文件", PLUGIN_PKG)

DSH_HOME = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
PROFILE = os.environ.get("DSH_PROFILE") or "web"
PROFILE_DIR = os.path.join(DSH_HOME, "profiles", PROFILE)
PATCH_FILE = os.path.join(PROFILE_DIR, "cordis.patch.yml")
PROFILE_PKG = os.path.join(PROFILE_DIR, "package.json")
PROFILE_MODULES = os.path.join(PROFILE_DIR, "node_modules")
JUNCTION = os.path.join(PROFILE_MODULES, PLUGIN_PKG)
PLUGIN_STORE = os.path.join(DSH_HOME, "plugins", PLUGIN_PKG)

# 自检用的"锚点行"：这些行必须在改完之后仍然存在，否则说明我们把配置改坏了
ANCHORS = ("browser-use", "skill-office", "git-buttons")

TS = datetime.now().strftime("%Y%m%d-%H%M%S")


def head(title):
    print()
    print("=" * 62)
    print("  " + title)
    print("=" * 62)


def backup(path, tag):
    """改前备份。文件不存在就跳过（不是错误）。"""
    if not os.path.isfile(path):
        print("  [备份] 跳过（文件不存在）：%s" % path)
        return None
    dst = "%s.bak-before-%s-%s" % (path, tag, TS)
    shutil.copy2(path, dst)
    print("  [备份] %s" % os.path.basename(dst))
    return dst


def read_text(path):
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8-sig") as f:
        return f.read()


def write_atomic(path, text):
    """原子写：先写 .tmp，再替换。写坏了不会留下半个文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def split_lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def join_lines(lines):
    """收尾：去掉尾部空行，最后补且只补一个换行（避免空行滚雪球）。"""
    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    return "\n".join(out) + "\n"


def top_level_id_indexes(lines, entry_id):
    """找出所有**顶格**（第 1 列开始）的 `- id: <entry_id>` 行号。

    缩进在 insert 块里的同名条目一律不算 —— 那是挂载声明，不是覆盖项。
    """
    pat = re.compile(r"^- id:\s*" + re.escape(entry_id) + r"\s*$")
    return [i for i, ln in enumerate(lines) if pat.match(ln)]


def block_end(lines, start):
    """一个顶格条目的结束行（下一个顶格 `- ` 之前）。"""
    i = start + 1
    while i < len(lines) and not lines[i].startswith("- "):
        i += 1
    return i


def set_override(lines, entry_id, name, disabled):
    """设置（或追加）顶格覆盖项。返回 (新行列表, 说明文字)。

    DSH 只认**最后一条**匹配的覆盖项 —— 所以存在多条时必须改最后一条。
    """
    idxs = top_level_id_indexes(lines, entry_id)
    if idxs:
        last = idxs[-1]
        end = block_end(lines, last)
        block = lines[last:end]
        found = False
        for j, ln in enumerate(block):
            if re.match(r"^\s+disabled:\s*", ln):
                block[j] = "  disabled: %s" % ("true" if disabled else "false")
                found = True
                break
        if not found:
            # 在 name 行后面插 disabled（缩进 2 空格，与同级字段一致）
            insert_at = 1
            for j, ln in enumerate(block):
                if re.match(r"^\s+name:\s*", ln):
                    insert_at = j + 1
                    break
            block.insert(insert_at, "  disabled: %s" % ("true" if disabled else "false"))
        out = lines[:last] + block + lines[end:]
        return out, "更新了最后一条顶格覆盖项（第 %d 行）" % (last + 1)

    out = list(lines)
    while out and out[-1].strip() == "":
        out.pop()
    out.append("")
    out.append("- id: %s" % entry_id)
    out.append("  name: %s" % name)
    out.append("  disabled: %s" % ("true" if disabled else "false"))
    return out, "末尾追加了一条顶格覆盖项"


def self_check_patch(text, before):
    """写后自检。返回问题列表（空 = 通过）。"""
    problems = []
    if len(text) < 200:
        problems.append("文件太短（%d 字节），像是被写坏了" % len(text))
    if re.search(r"(?m)^\t", text):
        problems.append("出现了 tab 缩进（YAML 不允许）")
    for anchor in ANCHORS:
        if anchor in before and anchor not in text:
            problems.append("原有挂载行不见了：%s" % anchor)
    if text.count("- id: %s" % PLUGIN_ID) < 1:
        problems.append("没找到新写的覆盖项 - id: %s" % PLUGIN_ID)
    if len(top_level_id_indexes(split_lines(text), PLUGIN_ID)) != 1:
        problems.append("顶格覆盖项 - id: %s 出现了不止一次（DSH 只认最后一条，会让人改错地方）"
                        % PLUGIN_ID)
    return problems


def ensure_junction():
    """在 profile 的 node_modules 里做一个指向插件源目录的 junction（不需要管理员）。"""
    target = PLUGIN_SRC
    if os.path.isdir(JUNCTION):
        try:
            same = os.path.samefile(JUNCTION, target)
        except OSError:
            same = False
        if same:
            print("  [链接] 已经在了：%s -> %s" % (JUNCTION, target))
            return True
        print("  [链接] 已存在但指向别处，先删掉重建")
        shutil.rmtree(JUNCTION, ignore_errors=True)
    if not os.path.isdir(PROFILE_MODULES):
        os.makedirs(PROFILE_MODULES, exist_ok=True)
    p = subprocess.run(["cmd", "/c", "mklink", "/J", JUNCTION, target],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not os.path.isdir(JUNCTION):
        print("  [链接] 失败：%s %s" % (p.stdout, p.stderr))
        return False
    print("  [链接] 建好 junction：%s -> %s" % (JUNCTION, target))
    return True


def update_profile_package(dry_run):
    """把包名加进 profile 的依赖和 bundles。"""
    if not os.path.isfile(PROFILE_PKG):
        print("  [清单] 找不到 profile 的 package.json：%s" % PROFILE_PKG)
        return False, None
    data = json.loads(read_text(PROFILE_PKG))
    link_spec = "link:" + PLUGIN_SRC.replace("\\", "/")

    deps = data.setdefault("dependencies", {})
    changed = []
    if deps.get(PLUGIN_PKG) != link_spec:
        deps[PLUGIN_PKG] = link_spec
        changed.append("dependencies 里加了 %s: %s" % (PLUGIN_PKG, link_spec))

    profile = data.setdefault("dsh", {}).setdefault("profile", {})
    bundles = profile.setdefault("bundles", [])
    if PLUGIN_PKG not in bundles:
        bundles.append(PLUGIN_PKG)
        changed.append("dsh.profile.bundles 里加了 %s" % PLUGIN_PKG)

    if not changed:
        print("  [清单] 已经配好了，不用改")
        return True, None

    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    # 自检：必须是合法 JSON，且原有 bundles 一个不少
    try:
        reparsed = json.loads(new_text)
        old_bundles = json.loads(read_text(PROFILE_PKG)).get("dsh", {}).get("profile", {}).get("bundles", [])
        for b in old_bundles:
            if b not in reparsed["dsh"]["profile"]["bundles"]:
                raise ValueError("原有 bundles 丢了：%s" % b)
    except Exception as e:
        print("  [清单] 自检不过，放弃写入：%s" % e)
        return False, None

    for c in changed:
        print("  [清单] " + c)
    if dry_run:
        return True, new_text
    backup(PROFILE_PKG, PLUGIN_PKG)
    write_atomic(PROFILE_PKG, new_text)
    print("  [清单] 已写入（重启 DSH 后生效）")
    return True, None


def link_dependencies():
    """在插件目录里链接它 import 的宿主包（@deepseek-ai/dsh-tools）。

    为什么需要这一步：插件的源码放在工作区里（`插件源文件/`），而 `@deepseek-ai/dsh-tools`
    装在 profile 的 node_modules 里。Node 解析裸包名是从**文件真实位置**往上找的，
    工作区那边当然找不到。DSH 自己的加载器能解析（本机在跑的 tavernweave 就是这么挂的），
    但这条链接让解析**不依赖加载器的实现细节** —— 顺带也让 dev/tools/host-harness.mjs
    能在重启 DSH 之前就把宿主半区跑一遍。

    链接指向的是同一个真实文件，所以模块实例还是同一个，不会出现"两份 defineTool"。
    """
    targets = ["@deepseek-ai/dsh-tools"]
    parent_modules = os.path.join(DSH_HOME, "profiles", "node_modules")
    made = []
    for pkg in targets:
        scope, short = pkg.split("/", 1)
        src = os.path.join(parent_modules, scope, short)
        if not os.path.isdir(src):
            print("  [依赖] 跳过 %s（在 %s 里没找到）" % (pkg, parent_modules))
            continue
        dest = os.path.join(PLUGIN_SRC, "node_modules", scope, short)
        if os.path.isdir(dest):
            try:
                if os.path.samefile(dest, src):
                    print("  [依赖] 已经在了：%s" % pkg)
                    continue
            except OSError:
                pass
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        p = subprocess.run(["cmd", "/c", "mklink", "/J", dest, src],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0 or not os.path.isdir(dest):
            print("  [依赖] 链接 %s 失败：%s %s" % (pkg, p.stdout, p.stderr))
            continue
        print("  [依赖] 链好 %s（指向 profile 里那一份，模块实例是同一个）" % pkg)
        made.append(pkg)
    return made


def install_skill():
    """把技能装进 DSH 的技能目录。技能目录是"一层深"读取，所以必须在 ~/.dsh/skills/<名>/SKILL.md。"""
    src = os.path.join(BASE, "技能源文件", "image-data-recognition")
    dst = os.path.join(DSH_HOME, "skills", "image-data-recognition")
    if not os.path.isfile(os.path.join(src, "SKILL.md")):
        print("  [技能] 找不到技能源：%s" % src)
        return False
    os.makedirs(dst, exist_ok=True)
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        rel = os.path.relpath(root, src)
        target_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target_dir, exist_ok=True)
        for fn in files:
            if fn.endswith(".pyc"):
                continue
            shutil.copy2(os.path.join(root, fn), os.path.join(target_dir, fn))
    # workspace.txt 必须 UTF-8 **带 BOM**：PowerShell 5.1 默认按 GBK 读，没 BOM 中文路径会乱码
    with open(os.path.join(dst, "workspace.txt"), "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(BASE + "\n")
    print("  [技能] 已装到 %s" % dst)
    return True


def show_status():
    head("DSH 插件状态：%s" % PLUGIN_PKG)
    print("  DSH 目录：  %s" % DSH_HOME)
    print("  profile：   %s（%s）" % (PROFILE, PROFILE_DIR))
    print("  插件源：    %s %s" % (PLUGIN_SRC, "[有]" if os.path.isdir(PLUGIN_SRC) else "[缺]"))

    ok = True
    if os.path.isdir(JUNCTION):
        try:
            tgt = os.path.realpath(JUNCTION)
        except OSError:
            tgt = "?"
        print("  node_modules 链接： [有] -> %s" % tgt)
    else:
        print("  node_modules 链接： [缺]")
        ok = False

    if os.path.isfile(PROFILE_PKG):
        data = json.loads(read_text(PROFILE_PKG))
        dep = (data.get("dependencies") or {}).get(PLUGIN_PKG)
        bundles = (data.get("dsh", {}).get("profile", {}) or {}).get("bundles", [])
        print("  package.json 依赖： %s" % (dep or "[缺]"))
        print("  bundles 里：        %s" % ("[有]" if PLUGIN_PKG in bundles else "[缺]"))
        ok = ok and bool(dep) and PLUGIN_PKG in bundles
    else:
        print("  package.json：      [缺] %s" % PROFILE_PKG)
        ok = False

    if os.path.isfile(PATCH_FILE):
        lines = split_lines(read_text(PATCH_FILE))
        idxs = top_level_id_indexes(lines, PLUGIN_ID)
        if idxs:
            last = idxs[-1]
            end = block_end(lines, last)
            block = "\n".join(lines[last:end])
            disabled = re.search(r"disabled:\s*(true|false)", block)
            print("  覆盖项：            [有] 第 %d 行，disabled=%s（共 %d 条匹配）"
                  % (last + 1, disabled.group(1) if disabled else "未写", len(idxs)))
            print("                      %s" % "  ".join(l.strip() for l in lines[last:end] if l.strip()))
        else:
            print("  覆盖项：            [缺]")
            ok = False
    else:
        print("  cordis.patch.yml：  [缺] %s" % PATCH_FILE)
        ok = False

    skill = os.path.join(DSH_HOME, "skills", "image-data-recognition", "SKILL.md")
    print("  技能：              %s" % ("[有] " + skill if os.path.isfile(skill) else "[缺]"))

    print()
    print("  结论：%s" % ("已装好。改完要重启 DSH；界面改动按 F5。" if ok
                         else "还没装好（或缺东西）。跑 python tools/dsh_plugin_setup.py 装一次。"))
    return 0 if ok else 2


def do_install(dry_run, verify):
    if not os.path.isfile(os.path.join(PLUGIN_SRC, "package.json")):
        print("[错误] 找不到插件源：%s" % PLUGIN_SRC)
        print("       插件包应该在工作区的 插件源文件\\%s\\ 里。" % PLUGIN_PKG)
        return 2
    if not os.path.isfile(PATCH_FILE):
        print("[错误] 找不到 profile 配置：%s" % PATCH_FILE)
        print("       DSH 装在别的地方？用环境变量 DSH_HOME / DSH_PROFILE 指一下。")
        return 2

    head("安装 DSH 专用插件：%s%s" % (PLUGIN_PKG, "（试运行）" if dry_run else ""))

    before = read_text(PATCH_FILE)

    print()
    print("[1/5] 链接插件目录")
    if dry_run:
        print("  （试运行：会建 junction %s -> %s）" % (JUNCTION, PLUGIN_SRC))
    elif not ensure_junction():
        return 2

    print()
    print("[1b/5] 链接插件 import 的宿主包")
    if dry_run:
        print("  （试运行：会在插件目录里链接 @deepseek-ai/dsh-tools）")
    else:
        link_dependencies()

    print()
    print("[2/5] 更新 profile 的 package.json")
    ok, _pending = update_profile_package(dry_run)
    if not ok:
        return 2

    print()
    print("[3/5] 更新 cordis.patch.yml（只动顶格条目）")
    lines = split_lines(before)
    new_lines, how = set_override(lines, PLUGIN_ID, PLUGIN_PKG, disabled=False)
    new_text = join_lines(new_lines)
    problems = self_check_patch(new_text, before)
    print("  %s" % how)
    if problems:
        print("  ✗ 自检不过，**放弃写入**（原配置文件一个字没动）：")
        for p in problems:
            print("      · %s" % p)
        return 2
    print("  自检通过：文件非空、无 tab 缩进、原有挂载行都在、新条目只出现一次")
    if dry_run:
        print("  （试运行：会写入 %s）" % PATCH_FILE)
    else:
        backup(PATCH_FILE, PLUGIN_PKG)
        write_atomic(PATCH_FILE, new_text)
        print("  已写入（patch 层是热加载的，但宿主侧代码换新要重启）")

    print()
    print("[4/5] 装技能到 DSH 技能目录")
    if dry_run:
        print("  （试运行：会把技能复制到 %s）" % os.path.join(DSH_HOME, "skills"))
    elif not install_skill():
        return 2

    print()
    print("[5/5] 校验配置")
    if dry_run or not verify:
        print("  跳过（试运行或 --no-verify）")
    else:
        check_dump_config()

    head("装完了")
    print("  接下来必须做两件事：")
    print("    1. **重启 DSH**（宿主侧的插件代码要重启才会加载）")
    print("    2. 重启后按 **F5** 刷新界面（客户端那一半要刷新页面）")
    print()
    print("  重启后新开一个会话，问一句「有哪些 table_image 开头的工具」验证一下。")
    print("  想回退：python tools/dsh_plugin_setup.py --uninstall")
    return 0


def check_dump_config():
    """用官方 --dump-config 验证配置能不能加载（本机已验证的验收方式）。"""
    cli = os.path.join(BASE, "..", "..", "deepseek-harness", "apps", "cli")
    checkout = os.environ.get("DSH_CHECKOUT") or r"F:\deepseek-harness"
    if not os.path.isdir(checkout):
        print("  跳过 --dump-config（找不到 DSH 源码目录 %s；可用 DSH_CHECKOUT 指定）" % checkout)
        return None
    pnpm = shutil.which("pnpm") or "pnpm"
    try:
        p = subprocess.run([pnpm, "dsh", "--profile", PROFILE, "--dump-config"],
                           cwd=checkout, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
    except Exception as e:
        print("  跳过 --dump-config（跑不起来：%s）" % e)
        return None
    out = (p.stdout or "") + (p.stderr or "")
    lines = out.splitlines()
    hit = [l for l in lines if PLUGIN_ID in l or PLUGIN_PKG in l]
    print("  --dump-config 退出码 %d，%d 行输出" % (p.returncode, len(lines)))
    if p.returncode != 0:
        print("  ⚠ 配置加载报错了，先别重启 —— 看一下上面的输出：")
        for l in lines[-15:]:
            print("      " + l)
        return False
    if hit:
        print("  ✓ 配置里能找到插件行：")
        for l in hit[:5]:
            print("      " + l.strip())
    else:
        print("  ⚠ 退出码是 0，但没找到插件行 —— 可能没挂上（或输出格式变了）")
    return True


def do_uninstall(dry_run):
    head("卸载 DSH 专用插件：%s%s" % (PLUGIN_PKG, "（试运行）" if dry_run else ""))
    if dry_run:
        print("  （试运行）会：删 junction、从 package.json 去掉依赖和 bundles、删掉顶格覆盖项")
        return 0

    if os.path.isdir(JUNCTION):
        shutil.rmtree(JUNCTION, ignore_errors=True)
        print("  [链接] 已删除 %s" % JUNCTION)

    if os.path.isfile(PROFILE_PKG):
        data = json.loads(read_text(PROFILE_PKG))
        changed = False
        if (data.get("dependencies") or {}).pop(PLUGIN_PKG, None) is not None:
            changed = True
        bundles = data.get("dsh", {}).get("profile", {}).get("bundles", [])
        if PLUGIN_PKG in bundles:
            bundles.remove(PLUGIN_PKG)
            changed = True
        if changed:
            backup(PROFILE_PKG, PLUGIN_PKG + "-uninstall")
            write_atomic(PROFILE_PKG, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            print("  [清单] 已去掉依赖和 bundles")
        else:
            print("  [清单] 本来就没有，跳过")

    if os.path.isfile(PATCH_FILE):
        before = read_text(PATCH_FILE)
        lines = split_lines(before)
        idxs = top_level_id_indexes(lines, PLUGIN_ID)
        if idxs:
            last = idxs[-1]
            end = block_end(lines, last)
            # 连同前面的空行一起去掉，避免反复装卸把空行攒起来
            start = last
            while start > 0 and lines[start - 1].strip() == "":
                start -= 1
            new_text = join_lines(lines[:start] + lines[end:])
            problems = self_check_patch_removal(new_text, before)
            if problems:
                print("  ✗ 自检不过，放弃写入：")
                for p in problems:
                    print("      · %s" % p)
                return 2
            backup(PATCH_FILE, PLUGIN_PKG + "-uninstall")
            write_atomic(PATCH_FILE, new_text)
            print("  [配置] 已删掉顶格覆盖项")
        else:
            print("  [配置] 本来就没有覆盖项，跳过")

    print()
    print("  卸载完成。**重启 DSH** 生效。工具和界面按钮都会消失。")
    print("  数据（runs/、archive/、schemas/）一点没动。")
    return 0


def self_check_patch_removal(text, before):
    problems = []
    if len(text) < 200:
        problems.append("文件太短（%d 字节）" % len(text))
    if re.search(r"(?m)^\t", text):
        problems.append("出现了 tab 缩进")
    for anchor in ANCHORS:
        if anchor in before and anchor not in text:
            problems.append("原有挂载行不见了：%s" % anchor)
    return problems


def main():
    ap = argparse.ArgumentParser(description="安装/查询/卸载 DSH 专用插件 dsh-table-image")
    ap.add_argument("--status", action="store_true", help="只看状态，不改任何东西")
    ap.add_argument("--uninstall", action="store_true", help="卸载（配置改回去，备份保留）")
    ap.add_argument("--dry-run", action="store_true", help="只显示会改什么，不写文件")
    ap.add_argument("--no-verify", action="store_true", help="跳过 pnpm --dump-config 校验")
    args = ap.parse_args()

    if args.status:
        return show_status()
    if args.uninstall:
        return do_uninstall(args.dry_run)
    return do_install(args.dry_run, not args.no_verify)


if __name__ == "__main__":
    sys.exit(main())
