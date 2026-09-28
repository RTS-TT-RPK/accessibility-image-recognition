# dsh-table-image —— 表格图片转录（DSH 插件）

> 给「表格图片 → 结构化 Excel」这套工作流做的一个 DSH 插件。
> 一半是**宿主侧工具**（模型能调的 `table_image_*`），一半是**界面**（输入框工具行上一个「收图」按钮 + 状态面板 + 拖拽收图）。

---

## 一、这是什么（一句实话）

**它是个薄壳，里面没有任何业务逻辑。**

插件的每一个动作都只是把一条命令交给工作区里的 Python 脚本去执行，然后把
**stdout + stderr + 退出码**原样交回给模型。谁的判断算数？**脚本说了算。**

```
模型 → table_image_* 工具 → 插件（只负责拼命令、跑命令、把原文带回来）→ python 脚本（唯一裁判）
```

这样分的好处：脚本改了、契约改了、流程改了，插件都不用动；
出了问题也是去看脚本的输出，而不是猜插件在想什么。

**它不会替你拍板**：识别结果、问题清单、人工决定，全都走工作区既有的流程；
插件既不猜数、也不改数、也不替你确认。

---

## 二、文件清单

| 文件 | 作用 |
|------|------|
| `package.json` | 包声明。含 `dsh.bundle.patch`（组合包挂载点）与 `dsh.client`（客户端半区声明） |
| `cordis.patch.yml` | 本包自己的组合包 patch：`- insert: [- id: table-image, name: dsh-table-image, config: { workspace: ... }]` |
| `index.js` | **宿主半区**。注册 9 个工具 + 挂一个上传端点。约 850 行，含大量注释说明"为什么这么写" |
| `client.js` | **客户端半区**。输入框工具行的按钮 + 面板 + 拖拽收图 + 设置页分区 |
| `dev/tools/test-harness.mjs` | 客户端测试台（模拟 React，不需要浏览器）。**83 项检查** |
| `dev/tools/test-host.mjs` | 宿主测试台（用假 python 真跑 spawn + 真发 HTTP 请求）。**205 项检查** |
| `dev/tools/host-harness.mjs` | 另一份宿主冒烟测试（21 项），会真的对着真工作区跑一次 `table_image_status` |
| `dev/tools/stub-loader.mjs` / `stub-hooks.mjs` | 把 `@deepseek-ai/dsh-tools` 换成替身，好在没装依赖的机器上跑宿主测试台 |
| `dev/tools/real-check.mjs` | **真机联调**：从 web profile 的 `node_modules` 导入真包，用**真** `@deepseek-ai/dsh-tools`、**真** python、**真**工作区跑一遍。**31 项检查** |
| `dev/tools/check-brackets.mjs` | 括号配平检查器（懂字符串/注释/正则字面量，不会误报） |
| `dev/tools/run-all-tests.bat` | 一次跑完全部测试台 |
| `安装说明.md` | 手工安装的完整步骤与四条硬规则 |
| `README.md` | 本文件 |

---

## 三、九个工具

所有工具都在**工作区目录**下以 `python <脚本> ...` 执行，命令行与工作目录写在返回内容开头。

| 工具 | 干什么 | 实际命令 | 呈现 |
|------|--------|----------|------|
| `table_image_status` | 查任务做到哪一步了 | `python validate_export.py runs/<run_id> --status` | 只读 |
| `table_image_start` | 收图建任务 | `python tools/建任务.py [--run-id X] [文件...]` | 执行 |
| `table_image_next` | 取「下一张还没识别的图」+ 契约摘要 + 片段格式 | `python tools/提交识别.py runs/<run_id> --next` | 只读 |
| `table_image_submit` | 交一张图的识别片段 | `python tools/提交识别.py runs/<run_id> --image ID --file <临时文件>` | 执行 |
| `table_image_check` | 刷新问题账本 | `python validate_export.py runs/<run_id> --init-issues` | 执行 |
| `table_image_review` | 生成人工审核表 | `python validate_export.py runs/<run_id>` | 执行 |
| `table_image_import_review` | 把审核表收回成 `decisions.json` | `python tools/导入审核.py runs/<run_id>` | 执行 |
| `table_image_finalize` | 落账（可归档）+ 导出长表 + 收尾报告，三步连跑 | 见下 | 执行 |
| `table_image_register_schema` | 注册新表型（版式契约） | `python tools/注册表型.py runs/<run_id> --name <表型名>` | 执行 |

`table_image_finalize` 的三步（`archive` 默认 `true`）：

```
python validate_export.py runs/<run_id> [--archive]
python tools/导出长表.py  runs/<run_id>
python tools/收尾报告.py  runs/<run_id>
```

### 几个刻意为之的设计

- **`run_id` 省略时会自动挑 `runs/` 下最新的任务，并告诉模型挑的是哪个。**
  `runs/` 下一个任务都没有时**不硬跑脚本**，直接如实说明。
- **每个工具的输出都原样带回**，前面加一段 `[宿主]` 抬头（工作目录 / 命令 / 退出码），
  后面分 `──── stdout ────` 与 `──── stderr ────` 两段。
- **退出码 0 不等于干净。** 工具描述里明确写了"退出码 0 也要把脚本打印的字读一遍"。
  退出码非 0 时额外加一句提醒：把原文转述给用户，不要替它编一个说法。
- **输出不会无界增长**：单个流超过 16000 字符就截断，并注明丢了多少字符。
- **`presentCall` 的读/写分类**用的是 DSH 真实枚举
  （`read | edit | delete | move | search | execute | fetch | other`）——
  **没有 `write` 这个值**，所以跑脚本改状态的工具统一报 `execute`。
- **工具注册与脚本是否存在解耦**：脚本不在时工具照常注册，调用时才如实说"脚本不存在"，
  并提示模型不要猜结果。

---

## 四、界面部分（`client.js`）

- **按钮挂在 `conversation.input.activity`**（`single` + `session` scope）——
  这个槽位是唯一能同时满足"只放一个控件"和"拿得到 `inputActions`"的位置。
- **只用 `inputActions.setDraft(text)`，从不调用 `submit()`。**
  点按钮的效果是把一段整理好的话**填进输入框**，发不发由用户按回车决定。
  三个按钮：「处理今天的图」「查一下进度」「收尾归档」。
- **拖拽收图**：把图片拖到按钮或面板上 → `POST /table-image/api` → 落进工作区的 `inbox/`。
  收下之后**不会自动开始识别**——面板上会写明还要说一声「处理今天的图」。
- **探活**：组件挂载后 `GET /table-image/api` 探一次。通了才启用拖拽；
  不通就把拖拽**禁用**并把原因写在面板上（**绝不假装成功**）。
- **兜底**：如果 DSH 里没有 `webServer` 服务，插件照常加载，只是拖拽不可用。

### 为什么长这样（照抄自《自建客户端插件》那篇血泪文档）

| 做法 | 防的是哪个坑 |
|------|--------------|
| 显式色值，全文件**零** `var(--*)` 用法 | CSS 变量名写错会静默用兜底值 → 面板全黑，且不报错 |
| 外面套 `ErrorBoundary` 类组件 | 渲染期抛错没有边界时，React 会卸载**整棵树** → 整个插件凭空消失 |
| 组件之间只用 props + 一个模块级 store | 平级组件互不可见；引用别人的局部变量 = `ReferenceError` |
| 拖拽四个事件（`onDragEnter/Over/Leave/Drop`）**一个不漏** | 漏挂事件 = 拖不动 / 点了没反应 |
| `panelStyle()` **两个轴都给**，纵向用 `bottom` 锚定，**从不写 `top`** | 只给一个轴 → 面板按静态位置渲染跑到视口外；猜高度 → 面板压住按钮 |
| `settings.section` 注册**必带 `label`** | 漏写就是一个空白行，且不报错 |

---

## 五、安装 / 卸载

完整步骤见 **`安装说明.md`**（含备份、原子写、写后自检那四条硬规则）。
一句话版本：

```
1. 备份 ~/.dsh/profiles/web/cordis.patch.yml 与 package.json
2. 把本包目录做成 junction 放进 ~/.dsh/profiles/web/node_modules/dsh-table-image
3. profile 的 package.json 里加依赖 dsh-table-image + dsh.profile.bundles 里加 dsh-table-image
4. 在 ~/.dsh/profiles/web/cordis.patch.yml 【末尾】加顶层覆盖项：
      - id: table-image
        name: dsh-table-image
5. 重启 DSH（客户端半区的改动还要刷新页面 F5）
6. 验收：pnpm dsh --profile web --dump-config  退出码 0、能看到 table-image 行
```

卸载：把第 4 步那条覆盖项改成 `disabled: true`（或删掉），再摘掉 junction 与依赖声明，重启。

---

## 六、已知限制（一条都没藏）

1. **在浏览器里亲眼看一遍这件事，没有做。**
   插件写好时本机**已经装好了**（junction、profile 依赖、`bundles`、用户层覆盖项都在位），
   所以能做的联调全做了：真包 + 真 `@deepseek-ai/dsh-tools` + 真 python + 真工作区，
   工具与上传端点都跑通了（`dev/tools/real-check.mjs`，全绿）。
   **唯独**没有重启 DSH 之后在浏览器里确认"按钮真的长在输入框工具行上"——
   重启是有成本的动作，不能为了自测随便动。
   所以：**第一次重开 DSH 之后请按 `安装说明.md` 第二节走一遍界面验收。**

2. **拖拽只能"收进 inbox/"，不能自动开跑。**
   这是故意的：本插件不替用户发指令。收完图用户还得说一声「处理今天的图」。

3. **面板位置在"锚点太靠屏幕上部"时会切顶。**
   面板要钉在锚点上方、又要整个进视口，这两件事在锚点离屏幕顶不足一个面板高时
   数学上不可兼得。本插件选择**保住"不盖住按钮"**（按钮被挡住 = 入口没了），
   代价是面板顶部可能被切掉一点。本插件的按钮长在输入框工具行（屏幕下半部分），
   实际不会碰到这个情形；`dev/tools/test-harness.mjs` 里把这条取舍打印出来当票据。

4. **上传走的是 base64 JSON，不是 multipart。**
   单文件上限 64 MB；超大图请先压一下。选这么实现是因为它只用 `node:http` 原生
   `req/res`，不引任何框架、可验证。图片还会按魔数再验一次，不是图片一律 400 拒收。

5. **端点路径 `/table-image/api` 是写死的**（客户端与宿主两边必须一致）。
   DSH 的路由表按 path 唯一，重复注册会抛错；本插件抄了 TavernWeave 的自愈写法
   （撞车时替换表项而不是让插件挂掉），但**这条自愈分支本身没有被触发验证过**。

6. **`tools/导出长表.py`、`tools/收尾报告.py`、`tools/导入审核.py`、`tools/注册表型.py`
   在本插件写好的时候还不存在**（另一个进程正在写它们）。
   本插件按约定的**路径与参数**调用，脚本一就位就能用。
   脚本不在时工具会如实报"脚本不存在"（实测过），不会假装成功。

7. **`tools/收尾报告.py` 的 `--one-line` 参数本插件没有用。**
   本插件调的是 `python tools/收尾报告.py runs/<run_id>`（不带 `--one-line`）。
   想要一行摘要的话，让模型在调完 `table_image_finalize` 之后自己读输出总结，
   或者改这里的调用参数。

8. **测试台里的"假 python"是个 Windows-only 的做法**（现场用 `csc.exe` 编一个
   只回固定文本的 `python.exe` 顶在 PATH 最前面）。换到没有 .NET Framework 的机器上
   这一段会跳过并如实说明，不会假装验过。

---

## 七、测试台怎么跑

```bat
dev\tools\run-all-tests.bat
```

或者分开跑：

```powershell
# ① 客户端半区（模拟 React，不需要浏览器、不需要装依赖）
node dev\tools\test-harness.mjs

# ② 宿主半区（真 spawn python + 真发 HTTP 请求；需要 --import 挂 dsh-tools 的替身）
node --import ./dev/tools/stub-loader.mjs dev\tools\test-host.mjs

# ③ 宿主冒烟（对着真工作区真跑一次 --status）
node dev\tools\host-harness.mjs "C:\Users\86173\Desktop\图像识别长期工程"

# ④ 真机联调（真包 + 真 dsh-tools + 真 python；上传那段在临时工作区里跑）
node dev\tools\real-check.mjs

# ⑤ 括号配平
node dev\tools\check-brackets.mjs
```

### 客户端测试台查什么（83 项）

| 检查 | 对应哪次真实事故 |
|------|------------------|
| 每个事件处理器是否都挂上了（拖拽四个 + 点击） | 漏挂 `onPointerMove`/`onPointerUp` → 拖不动、点不开 |
| 每个组件渲染是否抛异常（含 `inputActions` 缺失/为 null/为 `{}`） | 跨组件引用变量 → 整棵树被卸载 |
| `panelStyle` 是否同时给出 left 与 bottom、是否用了 top、13 组极端锚点下几何对不对 | 只给一个轴 → 跑到视口外；猜高度 → 压住按钮 |
| `settings.section` 是否带 `label` | 漏写 → 设置页空白行，无警告 |
| 全文件有没有 `var(--*)` 用法 | 变量名写错 → 静默兜底 → 面板全黑 |
| 点指令按钮是否只 `setDraft`、从不 `submit` | 本插件的硬规矩 |
| 上传成功/失败/非图片/空拖拽是否如实报状态 | 不许假装成功 |
| `ErrorBoundary` 是否真兜得住崩溃的子树 | 边界失效 = 整个插件凭空消失 |

### 宿主测试台查什么（205 项）

| 检查 | 说明 |
|------|------|
| 工作区定位三条出路 + 三条都不通时那个**唯一的错误**是否点名了三者 | config → 环境变量 → `workspace.txt`（带 BOM 也认；只有空白不算） |
| 9 个工具是否齐全、参数/输出/`presentCall`/描述是否完整 | `required` 必须是**布尔 `true`**（DSH 的 schema 规格只认这个） |
| 每个工具真跑一遍，命令拼得对不对 | 用假 `python.exe` 顶在 PATH 最前面，走真实 spawn 路径 |
| 退出码非 0 是否变成 `ok=false`、退出码是否原样带回、stderr 是否带回来 | 不许把失败说成成功 |
| `finalize` 是不是真按 ①落账 ②长表 ③报告 的顺序跑、`archive` 开关对不对 | 顺序错了交付就错了 |
| `submit` 的临时片段文件是否真写了、真删了（含 `--replace`） | 不留垃圾、不覆盖 |
| 脚本不存在 / `run_id` 不存在 / `runs/` 空 时是否如实说 | 不许猜结果 |
| 上传端点：GET 探活、POST 落盘、非图片拒收、**路径穿越被洗掉**、**撞名不覆盖**、同名已有图字节不变 | 不许覆盖用户的图 |
| 中文编码 | **本机 `python` 的 stdout 默认是 GBK**，不设 `PYTHONIOENCODING=utf-8` 的话脚本输出的中文全成乱码。测试台用**真 python** 正反两面都验了 |

### 真机联调查什么（31 项）

| 检查 | 说明 |
|------|------|
| `@deepseek-ai/dsh-tools` 从 profile 解析到哪一份 | 实测落在 `F:\deepseek-harness\packages\core\tools\lib\index.js`（真包，不是替身） |
| 真包的 `index.js` 能不能 import、`apply()` 会不会被真 `defineTool` 拒 | 真 `defineTool` 会校验 `parameters`/`output` 规格，比替身严格 |
| 真 python 真跑 `table_image_status` / `table_image_next` | 对着**真的** `runs/` 跑，检查退出码、中文、脚本原话有没有被带回来 |
| 真起 http 服务，真发 GET/POST/DELETE 到上传端点 | 在**临时工作区**里跑，绝不碰用户真实的 `inbox/` |
| 跑完再回头确认用户真实 `inbox/` 没被动过 | 自己给自己上约束 |

---

## 八、验证过的版本

| 项 | 值 |
|----|-----|
| DSH | `0.1.7-rc.2`（源码检出 `F:\deepseek-harness`，提交 `477b4f4`） |
| Node | v22.23.3 |
| 平台 | Windows x64，web profile（`patchReload: live`） |
| Python | 3.12.10（`C:\Users\86173\AppData\Local\Python\bin\python.exe`） |
| 插件版本 | 0.1.0 |

### 用到的 DSH API 与出处（都不是猜的）

| API | 出处 |
|-----|------|
| `import { defineTool } from '@deepseek-ai/dsh-tools'` | `packages/core/tools/lib/index.js:838`（`function defineTool(options)`）；真实调用先例 `packages/todo/tool-todo/src/index.ts:135` |
| `parameters` 是 JSON-Schema 子集，`required` 用**逐属性的布尔** | `packages/core/tools/lib/index.js:603-604`（`authorError: ${path}.required must be true when present`） |
| `output: { schema, render }`，`render(args, value)` 返回内容块数组 | `packages/core/tools/lib/index.js:855-863`；真实例子 `packages/fs/tool-fs/src/read-image.ts:216-235` |
| `execute(args, exec)` | `packages/core/tools/lib/index.js:866-870` |
| `presentCall` 的 `kind` 枚举 | `packages/core/tools/lib/types/presentation.d.ts:13`：`'read' \| 'edit' \| 'delete' \| 'move' \| 'search' \| 'execute' \| 'fetch' \| 'other'` —— **没有 `write`** |
| `export const inject = [...]` / `apply(ctx, config)` | `packages/todo/tool-todo/src/index.ts:23,117` |
| `ctx.webServer.register({ kind, path, handler })` | `packages/host/webserver/src/index.ts:42-48`（`WebRoute`）与 `:166`（`register(route)`）；真实调用先例 `packages/webhook/webhook-github/src/index.ts:58-61`、以及本机在跑的 `dsh-tavernweave/lib/index.js:484-540` |
| `ctx.inject(['webServer'], cb)` 做**可选**服务注入 | `packages/client/modules/src/index.ts:653` |
| 客户端槽位 `conversation.input.activity` 是 `single` + `session`，拿得到 `inputActions` | `packages/client/ui-conversation/src/client/apply.ts:430`、`.../contract/slots.ts:205` |
| 客户端 `window.__ModuleLoader__.load({ id, factory })` | `apps/web/tests/fixtures/plugins/fixture-input-extension/client.js` |

### `@deepseek-ai/dsh-tools` 从哪来（实测）

插件能 `import { defineTool } from '@deepseek-ai/dsh-tools'`，靠的是 **DSH 给 profile 建的软链场**：

```
~/.dsh/profiles/node_modules/@deepseek-ai/        ← 178 个包的软链（DSH 自己建的，不是本插件建的）
    dsh-tools  ->  ...\dsh-tools  （0.1.7-rc.2，指向 F:\deepseek-harness 里的实体）
    dsh-host-webserver  也在里面（所以 ctx.webServer 也是现成的）
```

本插件的 `package.json` 里把 `@deepseek-ai/dsh-tools` 声明成 `peerDependencies`
（`>=0.1.1-rc.2 <0.2.0`，与 TavernWeave 一致），装的时候 DSH 会按这份声明在
本包目录下也建一个 `node_modules/@deepseek-ai/dsh-tools` 软链，指向上面那个软链场。

不走软链场也行：从 profile 目录出发解析，Node 会直接找到
`F:\deepseek-harness\packages\core\tools\lib\index.js`（已实测，见
`dev/tools/real-check.mjs` 第 1 节）。两条路都能通，所以这一句 import 是安全的。

---

## 九、边界与纪律

这个插件**不碰**工作区的数据：

- 不写 `raw.json`（那是 `tools/提交识别.py` 的事）；
- 不写 `decisions.json`（那是 `tools/导入审核.py` 的事）；
- 不删 `inbox/`、`pending/`、`golden/` 里的任何东西；
- 归档交给 `validate_export.py --archive`（内部 `os.rename`，撞名自动加序号）；
- 唯一会写盘的地方是**上传端点把图片放进 `inbox/`**，而且撞名自动加 `_2`
  （用 `flag: 'wx'` 只新建不覆盖），路径穿越会被洗成纯文件名。

工作区的行为手册在根目录 `AGENTS.md`，插件里的一切都服从它。
