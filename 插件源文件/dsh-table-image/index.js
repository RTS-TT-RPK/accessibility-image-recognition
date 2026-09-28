/**
 * dsh-table-image —— 宿主半区（thin shell）
 *
 * 本文件不含任何业务逻辑：每一个工具都只是把命令交给工作区里的 Python 脚本执行，
 * 把 **stdout + stderr + 退出码** 原样交回给模型。裁判是脚本，不是本插件。
 *
 * 两条对外通路：
 *   ① ctx.tools.register(defineTool(...))  注册 9 个 table_image_* 工具
 *   ② ctx.webServer.register({ kind:'exact', path:'/table-image/api', handler })
 *      给客户端半区的「拖拽收图」提供一个真正的落盘端点（上传图片 → inbox/）
 *
 * ② 用的是 DSH 里**已验证存在**的 API，证据：
 *   - 服务声明：packages/host/webserver/src/index.ts:22-36   declare module '@deepseek-ai/cordis' { interface Context { webServer: WebServer } }
 *   - 路由签名：packages/host/webserver/src/index.ts:42-48   interface WebRoute { kind; path; handler(req: IncomingMessage, res: ServerResponse) }
 *   - 注册方法：packages/host/webserver/src/index.ts:166     register(route: WebRoute): () => void
 *   - 真实调用先例：packages/webhook/webhook-github/src/index.ts:58-61
 *       ctx.effect(() => ctx.webServer.register(route), 'webhook-github: ...')
 *   - 可选注入先例：packages/client/modules/src/index.ts:653  ctx.inject(['webServer'], registerWebCarrier)
 */

import { spawn } from 'node:child_process'
import { createHash, randomUUID } from 'node:crypto'
import fs from 'node:fs'
import fsp from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineTool } from '@deepseek-ai/dsh-tools'

/** Cordis function-plugin name. */
export const name = 'table-image'

/**
 * 只声明 `tools` 是**必需**的；`webServer` 走 ctx.inject 可选挂载，
 * 免得在没有 Web 服务的组合里整个插件加载失败（Cordis 的 inject 对缺失服务会抛错）。
 */
export const inject = ['tools']

/** 一次任务里，单个流最多回给模型多少字符。超了截断并在末尾注明。 */
const MAX_STREAM_CHARS = 16000
/** 上传端点的单文件上限（字节）。 */
const MAX_UPLOAD_BYTES = 64 * 1024 * 1024
/** 上传端点的对外路径（客户端半区与这里必须一致）。 */
const ROUTE_PATH = '/table-image/api'
/** 认得出的图片扩展名（收图侧还会按魔数再判一次）。 */
const IMAGE_EXT = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.heic', '.heif'])

// ─────────────────────────────────────────────────────────────────────────────
// 工作区定位
// ─────────────────────────────────────────────────────────────────────────────

/** 剥掉 UTF-8 BOM（本工作区的文本文件可能带 BOM，读了必须去掉再用）。 */
function stripBom(text) {
  return text.charCodeAt(0) === 0xfeff ? text.slice(1) : text
}

/** 本文件所在目录（ESM 里没有 __dirname）。 */
function moduleDir() {
  try {
    return path.dirname(fileURLToPath(import.meta.url))
  } catch {
    return process.cwd()
  }
}

/**
 * 从本文件所在目录逐级向上找 package.json，用它的目录当基准目录。
 * 只是为了让 workspace.txt 的查找不写死任何路径 —— 找不到就退回本文件目录。
 * @returns 基准目录的绝对路径。
 */
function packageBaseDir() {
  let dir = moduleDir()
  for (let i = 0; i < 8; i += 1) {
    if (fs.existsSync(path.join(dir, 'package.json'))) return dir
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return moduleDir()
}

/**
 * 按固定优先级解析工作区绝对路径：config.workspace → 环境变量 → 同目录 workspace.txt。
 * 三者都拿不到时抛**一个**错误，把三条出路一次讲清。
 * @param config - 插件的 config 对象（来自 patch 行的 config: 段），可能为空。
 * @returns 工作区绝对路径。
 */
export function resolveWorkspace(config) {
  const tried = []

  // ① patch 行的 config: { workspace: '...' }
  const fromConfig = config && typeof config.workspace === 'string' ? config.workspace.trim() : ''
  if (fromConfig) {
    const abs = path.resolve(fromConfig)
    if (fs.existsSync(abs)) return abs
    tried.push(`config.workspace = ${fromConfig} —— 这个目录不存在`)
  } else {
    tried.push('config.workspace —— patch 行的 config: 里没有 workspace 字段')
  }

  // ② 环境变量
  const fromEnv = (process.env.DSH_TABLE_IMAGE_WORKSPACE || '').trim()
  if (fromEnv) {
    const abs = path.resolve(fromEnv)
    if (fs.existsSync(abs)) return abs
    tried.push(`环境变量 DSH_TABLE_IMAGE_WORKSPACE = ${fromEnv} —— 这个目录不存在`)
  } else {
    tried.push('环境变量 DSH_TABLE_IMAGE_WORKSPACE —— 没有设置')
  }

  // ③ 同目录的 workspace.txt（UTF-8，可能带 BOM）
  const txtPath = path.join(packageBaseDir(), 'workspace.txt')
  if (fs.existsSync(txtPath)) {
    try {
      const raw = stripBom(fs.readFileSync(txtPath, 'utf8')).trim()
      if (raw) {
        const abs = path.resolve(raw.split(/\r?\n/)[0].trim())
        if (fs.existsSync(abs)) return abs
        tried.push(`${txtPath} 里写的是 ${raw} —— 这个目录不存在`)
      } else {
        tried.push(`${txtPath} 是空文件`)
      }
    } catch (err) {
      tried.push(`${txtPath} 读不了：${err && err.message}`)
    }
  } else {
    tried.push(`${txtPath} —— 文件不存在`)
  }

  throw new Error(
    'dsh-table-image：找不到工作区目录。请用下面任意一种方式指定（按优先级）：\n'
    + `  1) patch 行的 config: 里写 workspace，例如\n`
    + `       config: { workspace: 'C:\\\\Users\\\\<你>\\\\Desktop\\\\图像识别长期工程' }\n`
    + `  2) 设置环境变量 DSH_TABLE_IMAGE_WORKSPACE=<工作区绝对路径>\n`
    + `  3) 在本插件 index.js 旁边放一个 workspace.txt（UTF-8，可带 BOM），里面只写一行工作区绝对路径\n`
    + `本次逐个试过的结果：\n  - ${tried.join('\n  - ')}`,
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 跑脚本
// ─────────────────────────────────────────────────────────────────────────────

/** 截断超长输出，并如实说明截了多少。 */
function clip(text) {
  const s = String(text == null ? '' : text)
  if (s.length <= MAX_STREAM_CHARS) return { text: s, clipped: false, dropped: 0 }
  return {
    text: s.slice(0, MAX_STREAM_CHARS),
    clipped: true,
    dropped: s.length - MAX_STREAM_CHARS,
  }
}

/** 把一条命令的显示形式拼出来（给人和模型看，不做 shell 转义解释）。 */
function displayCommand(rel, args) {
  return ['python', rel, ...args].join(' ')
}

/**
 * 用工作区当 cwd 跑一条 python 命令，收集 stdout / stderr / 退出码。
 * 永不抛：失败也返回结构化结果 + 结尾的 [宿主] 说明行。
 * @param ws - 工作区绝对路径（cwd）。
 * @param rel - 相对工作区的脚本路径，例如 'validate_export.py'。
 * @param args - 脚本参数数组。
 * @returns 结构化执行结果。
 */
function runPython(ws, rel, args) {
  return new Promise((resolve) => {
    const scriptAbs = path.join(ws, rel)
    const shown = displayCommand(rel, args)
    const head = [
      `[宿主] 工作目录：${ws}`,
      `[宿主] 命令：${shown}`,
    ]

    if (!fs.existsSync(scriptAbs)) {
      resolve({
        command: shown,
        exitCode: null,
        output: `${head.join('\n')}\n[宿主] 脚本不存在：${scriptAbs}\n`
          + '[宿主] 这不代表任务状态，只说明这个脚本还没就位。请如实告诉用户缺哪个脚本，不要猜结果。',
      })
      return
    }

    let child
    try {
      child = spawn('python', [rel, ...args], {
        cwd: ws,
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe'],
        env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' },
      })
    } catch (err) {
      resolve({
        command: shown,
        exitCode: null,
        output: `${head.join('\n')}\n[宿主] 起不了 python 进程：${err && err.message}`,
      })
      return
    }

    let out = ''
    let errOut = ''
    child.stdout.on('data', (d) => { out += d.toString('utf8') })
    child.stderr.on('data', (d) => { errOut += d.toString('utf8') })
    child.on('error', (err) => {
      resolve({
        command: shown,
        exitCode: null,
        output: `${head.join('\n')}\n[宿主] python 进程出错：${err && err.message}\n`
          + '[宿主] 系统里可能没有 python（或不在 PATH）。请如实告诉用户，不要猜结果。',
      })
    })
    child.on('close', (code) => {
      const o = clip(out)
      const e = clip(errOut)
      const parts = [...head]
      parts.push(`[宿主] 退出码：${code === null ? '（没拿到，进程可能被终止）' : code}`)
      parts.push('')
      parts.push('──── stdout ────')
      parts.push(o.text.length ? o.text : '（空）')
      if (o.clipped) parts.push(`…（stdout 太长，已截断，丢掉 ${o.dropped} 个字符）`)
      parts.push('')
      parts.push('──── stderr ────')
      parts.push(e.text.length ? e.text : '（空）')
      if (e.clipped) parts.push(`…（stderr 太长，已截断，丢掉 ${e.dropped} 个字符）`)
      if (code !== 0) {
        parts.push('')
        parts.push('[宿主] 退出码非 0。脚本的报错原文在上面，请原样转述给用户，不要替它编一个说法。')
      }
      resolve({ command: shown, exitCode: code, output: parts.join('\n') })
    })
  })
}

/** 把结构化结果压成模型的输出对象。 */
function toValue(run, note, runId) {
  return {
    ok: run.exitCode === 0,
    exit_code: run.exitCode === null ? -1 : run.exitCode,
    run_id: runId || '',
    command: run.command,
    note: note || '',
    output: run.output,
  }
}

/** 所有工具共用的 output 声明。 */
const RUN_OUTPUT = {
  schema: {
    type: 'object',
    additionalProperties: false,
    properties: {
      ok: { type: 'boolean', required: true },
      exit_code: { type: 'integer', required: true },
      run_id: { type: 'string', required: true },
      command: { type: 'string', required: true },
      note: { type: 'string', required: true },
      output: { type: 'string', required: true },
    },
  },
  render: (_args, value) => [{ type: 'text', text: value.output }],
}

/** 只读工具的 presentCall。 */
function readCall(title, extra) {
  return (args) => ({
    card: 'generic',
    title,
    kind: 'read',
    rawInput: extra ? extra(args) : (args && args.run_id ? { run_id: args.run_id } : undefined),
  })
}

/**
 * 写类工具的 presentCall。
 * kind 只能取 'read' | 'edit' | 'delete' | 'move' | 'search' | 'execute' | 'fetch' | 'other'
 * （证据：packages/core/tools/lib/types/presentation.d.ts:13 —— 没有 'write' 这个值），
 * 所以跑脚本改状态的工具统一报 'execute'。
 */
function runCall(title) {
  return (args) => ({
    card: 'generic',
    title,
    kind: 'execute',
    rawInput: args && args.run_id ? { run_id: args.run_id } : undefined,
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// run_id 解析：省略时挑最新的 runs/*
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 列出 runs/ 下的任务目录，按名字（即时间序）挑最新一个。
 * @param ws - 工作区绝对路径。
 * @returns { runId, picked, all } —— 没找到时 runId 为 null。
 */
function pickNewestRun(ws) {
  const runsDir = path.join(ws, 'runs')
  if (!fs.existsSync(runsDir)) return { runId: null, all: [] }
  const all = fs.readdirSync(runsDir, { withFileTypes: true })
    .filter((d) => d.isDirectory() && /^\d{8}_\d{3,}$/.test(d.name))
    .map((d) => d.name)
    .sort()
  return { runId: all.length ? all[all.length - 1] : null, all }
}

const RUN_ID_ARG = {
  type: 'string',
  description: '任务号，形如 20260910_001。省略时自动挑 runs/ 下最新的一个任务目录。',
}

/**
 * 解析 run_id：给了就用；没给就挑最新，并在 note 里说明挑了哪个。
 * @returns { runId, note } —— run_id 找不到时 runId 为 null，note 说明原因。
 */
function resolveRunId(ws, runId) {
  const wanted = typeof runId === 'string' ? runId.trim() : ''
  if (wanted) {
    const abs = path.join(ws, 'runs', wanted)
    if (!fs.existsSync(abs)) {
      const { all } = pickNewestRun(ws)
      return {
        runId: wanted,
        note: `指定的任务目录不存在：runs/${wanted}。`
          + (all.length ? `当前已有的任务：${all.join('、')}` : '（runs/ 下还没有任何任务目录）')
          + ' 下面的输出是脚本对不存在目录的报错原文。',
      }
    }
    return { runId: wanted, note: '' }
  }
  const { runId: newest, all } = pickNewestRun(ws)
  if (!newest) {
    return { runId: '', note: 'runs/ 下没有形如 YYYYMMDD_NNN 的任务目录，也没有指定 run_id。请先建任务。' }
  }
  return {
    runId: newest,
    note: `没有指定 run_id，已自动挑最新任务：${newest}（runs/ 下共 ${all.length} 个：${all.join('、')}）`,
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 工具注册
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 注册全部 table_image_* 工具。
 * @param ctx - 插件上下文（需要 ctx.tools）。
 * @param ws - 已解析好的工作区绝对路径。
 */
function registerTools(ctx, ws) {
  ctx.tools.register(defineTool({
    name: 'table_image_status',
    description:
      '查「表格图片转录」任务现在做到哪一步了：处在哪一阶段、今天几张图、识别了几张、还剩哪几张没识别、'
      + '校验做了没有、有多少问题、还差几条没人工处理、归档做完了没有，最后一行给出「下一步该干什么」。'
      + '用户问「进度怎么样 / 做到哪了 / 还剩多少」时用这个，不要凭记忆回答。'
      + '省略 run_id 时会自动挑 runs/ 下最新的任务目录，并告诉你挑了哪个。'
      + '工作区里的 validate_export.py 是唯一裁判，它的输出就是权威，本工具只负责原样转达。',
    parameters: { run_id: RUN_ID_ARG },
    output: RUN_OUTPUT,
    async execute(args) {
      const { runId, note } = resolveRunId(ws, args.run_id)
      if (!runId) return toValue({ command: '(未执行)', exitCode: null, output: `[宿主] ${note}` }, note, '')
      const run = await runPython(ws, 'validate_export.py', [`runs/${runId}`, '--status'])
      return toValue(run, note, runId)
    },
    presentCall: readCall('查表格转录进度'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_start',
    description:
      '新建一次「表格图片转录」任务：建 runs/日期_编号/、把原图复制进 source/、逐张算 sha256、写图片台账。'
      + '用户把新照片放进 inbox/ 之后、要开始干活时用这个。'
      + 'files 省略则收 inbox/ 里全部图片；files 里给的是**绝对路径**时照用，相对路径按工作区解析。'
      + 'all_inbox=true 与省略 files 等价（都是「收 inbox 全部」）。'
      + '返回新任务号 run_id 与待识别图片清单。'
      + '工作区里的 tools/建任务.py 是权威，本工具只负责原样转达它的输出。',
    parameters: {
      run_id: {
        type: 'string',
        description: '直接指定任务号 YYYYMMDD_NNN；省略时由脚本自动取当天下一个空号。',
      },
      files: {
        type: 'array',
        items: { type: 'string' },
        description: '要收的图片路径（建议绝对路径）。不传就收 inbox/ 里全部图片。',
      },
      all_inbox: {
        type: 'boolean',
        description: 'true = 收 inbox/ 里全部图片（与不传 files 等价）。默认 false。',
      },
    },
    output: RUN_OUTPUT,
    async execute(args) {
      const argv = []
      if (typeof args.run_id === 'string' && args.run_id.trim()) argv.push('--run-id', args.run_id.trim())
      const files = Array.isArray(args.files) ? args.files.filter((f) => typeof f === 'string' && f.trim()) : []
      for (const f of files) argv.push(f)
      const scope = files.length
        ? `指定了 ${files.length} 个文件`
        : (args.all_inbox === true ? 'all_inbox=true，收 inbox/ 全部' : '没指定文件，收 inbox/ 全部')
      const run = await runPython(ws, 'tools/建任务.py', argv)
      return toValue(run, `收图范围：${scope}。新任务号在脚本输出里（形如「任务：20260910_003」）。`, args.run_id || '')
    },
    presentCall: runCall('新建表格转录任务（收图）'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_next',
    description:
      '拿「下一张还没识别的图」：返回它的绝对路径、image_id、这张表对应的**冻结契约摘要**'
      + '（已注册表型、一行代表什么、有哪些列、哪些必填、计量项单位与合计上限），'
      + '以及**必须照抄提交的 JSON 片段格式**。'
      + '本工具**不返回图片内容**：拿到路径后用已有的 read_image 工具去看那张图。'
      + '看清了就调 table_image_submit 交结果；看不清的格子写进 uncertain，绝不猜。'
      + '工作区里的 tools/提交识别.py 是权威，本工具只负责原样转达它的输出。',
    parameters: { run_id: RUN_ID_ARG },
    output: RUN_OUTPUT,
    async execute(args) {
      const { runId, note } = resolveRunId(ws, args.run_id)
      if (!runId) return toValue({ command: '(未执行)', exitCode: null, output: `[宿主] ${note}` }, note, '')
      const run = await runPython(ws, 'tools/提交识别.py', [`runs/${runId}`, '--next'])
      return toValue(
        run,
        `${note}${note ? ' ' : ''}请用 read_image 打开输出里的「绝对路径」去看图；识别完用 table_image_submit 交回。`,
        runId,
      )
    },
    presentCall: readCall('取下一条待识别图片'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_submit',
    description:
      '交**一张图**的识别结果。fragment 的结构照 table_image_next 给出的片段格式写：'
      + '顶层 image_id / sheet_label / date_text / rows；每个 row 要有 row_role（data|summary|header|note，'
      + '身份不明的行会被拦下）、object_label、fields、components、uncertain。'
      + '不要自己写 row_id / value / unit / sha256 —— 那些由脚本算。'
      + '工具会把 fragment 写成临时 JSON 再调 tools/提交识别.py --file，跑完就删临时文件，'
      + '然后把脚本的裁定（收下 / 拒收 + 原因）原样交回给你。'
      + '被拒收时照原因改，改完重交；确实要覆盖已交过的图再加 replace=true。'
      + '工作区里的 tools/提交识别.py 是唯一裁判，它说收下才算收下。',
    parameters: {
      run_id: { type: 'string', required: true, description: '任务号，形如 20260910_001。' },
      image_id: { type: 'string', required: true, description: '图片编号，例如 IMG_0001（要和片段里的 image_id 一致）。' },
      fragment: { type: 'object', additionalProperties: true, required: true, description: '这一张图的识别片段（按 table_image_next 给的格式）。' },
      replace: { type: 'boolean', description: '这张图已经交过、确认要覆盖重交时传 true。默认 false。' },
    },
    output: RUN_OUTPUT,
    async execute(args) {
      const runId = String(args.run_id || '').trim()
      const imageId = String(args.image_id || '').trim()
      if (!runId) throw new Error('run_id 不能为空')
      if (!imageId) throw new Error('image_id 不能为空')
      if (args.fragment === null || typeof args.fragment !== 'object' || Array.isArray(args.fragment)) {
        throw new Error('fragment 必须是一个 JSON 对象（{...}）')
      }

      const tmp = path.join(os.tmpdir(), `dsh-table-image-${randomUUID()}.json`)
      let run
      let note = ''
      try {
        await fsp.writeFile(tmp, JSON.stringify(args.fragment, null, 2), 'utf8')
      } catch (err) {
        throw new Error(`写不了临时片段文件 ${tmp}：${err && err.message}`)
      }
      try {
        const argv = [`runs/${runId}`, '--image', imageId, '--file', tmp]
        if (args.replace === true) argv.push('--replace')
        run = await runPython(ws, 'tools/提交识别.py', argv)
        note = `片段已写成临时文件 ${tmp}，交给脚本；无论脚本收没收，临时文件都已删除。`
      } finally {
        try { await fsp.unlink(tmp) } catch { /* 已经没了就算了 */ }
      }
      return toValue(run, note, runId)
    },
    presentCall: (args) => ({
      card: 'generic',
      title: `提交识别结果 ${args && args.image_id ? args.image_id : ''}`.trim(),
      kind: 'execute',
      rawInput: { run_id: args && args.run_id, image_id: args && args.image_id },
    }),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_check',
    description:
      '全部图片识别完之后跑一次校验，把问题集中成账本（issues.json）。'
      + '这是交给用户复核**之前**的固定动作（对应流程里的 --init-issues）。'
      + '它会刷新问题清单，不会改动已落盘的识别数据。'
      + '工作区里的 validate_export.py 是唯一裁判，本工具只负责原样转达它的输出。',
    parameters: { run_id: RUN_ID_ARG },
    output: RUN_OUTPUT,
    async execute(args) {
      const { runId, note } = resolveRunId(ws, args.run_id)
      if (!runId) return toValue({ command: '(未执行)', exitCode: null, output: `[宿主] ${note}` }, note, '')
      const run = await runPython(ws, 'validate_export.py', [`runs/${runId}`, '--init-issues'])
      return toValue(run, note, runId)
    },
    presentCall: runCall('刷新问题账本（校验）'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_review',
    description:
      '生成交给用户复核的审核表（runs/<run_id>/task_review.xlsx），并报出问题条数。'
      + '固定动作：table_image_check 之后跑这个，然后把 task_review.xlsx 交给用户，'
      + '请他**只填「问题清单」页的员工决定列**。'
      + '注意 task_review.xlsx 是派生文件、每次重跑整表重写，不要把它当权威数据源。'
      + '工作区里的 validate_export.py 是唯一裁判，本工具只负责原样转达它的输出。',
    parameters: { run_id: RUN_ID_ARG },
    output: RUN_OUTPUT,
    async execute(args) {
      const { runId, note } = resolveRunId(ws, args.run_id)
      if (!runId) return toValue({ command: '(未执行)', exitCode: null, output: `[宿主] ${note}` }, note, '')
      const run = await runPython(ws, 'validate_export.py', [`runs/${runId}`])
      const reviewPath = path.join(ws, 'runs', runId, 'task_review.xlsx')
      const exists = fs.existsSync(reviewPath)
      return toValue(
        run,
        `${note}${note ? ' ' : ''}审核表路径：${exists ? reviewPath : `${reviewPath}（脚本跑完但这个文件不在，请如实告诉用户）`}`
          + ' 问题条数见脚本输出的「问题清单」段。',
        runId,
      )
    },
    presentCall: runCall('生成人工审核表'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_import_review',
    description:
      '把用户填好的审核表收回成 decisions.json：读 runs/<run_id>/task_review.xlsx 的决定列，'
      + '写进权威的 decisions.json（Excel 是派生的，decisions.json 才是权威）。'
      + '用户回复「审核完成」之后立即跑这个，然后用 table_image_finalize 重跑落账并归档。'
      + '返回导入了多少条决定、哪些被拒。被拒的条目要原样转述给用户请他确认，不要替他换算或替他拍板。'
      + '工作区里的 tools/导入审核.py 是权威，本工具只负责原样转达它的输出。',
    parameters: { run_id: RUN_ID_ARG },
    output: RUN_OUTPUT,
    async execute(args) {
      const { runId, note } = resolveRunId(ws, args.run_id)
      if (!runId) return toValue({ command: '(未执行)', exitCode: null, output: `[宿主] ${note}` }, note, '')
      const run = await runPython(ws, 'tools/导入审核.py', [`runs/${runId}`])
      return toValue(run, note, runId)
    },
    presentCall: runCall('导入审核决定'),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_finalize',
    description:
      '收尾：连着跑三步 —— ① validate_export.py runs/<run_id> [--archive] 重跑落账'
      + '（archive=true 时按确认结果把原图移进 archive/YYYY-MM-DD/）'
      + '② tools/导出长表.py 出最终长表 ③ tools/收尾报告.py 出收尾报告。'
      + '用户确认审核结果之后用这个。三步的输出都会原样带回，哪一步失败要说清是哪一步。'
      + '归档一律交给脚本（内部用 os.rename，撞名自动加序号），本工具不删不动 inbox/、pending/、golden/。'
      + '工作区里的这三个脚本是权威，本工具只负责原样转达它们的输出。',
    parameters: {
      run_id: { type: 'string', required: true, description: '任务号，形如 20260910_001。' },
      archive: { type: 'boolean', description: '是否归档原图。默认 true。' },
    },
    output: RUN_OUTPUT,
    async execute(args) {
      const runId = String(args.run_id || '').trim()
      if (!runId) throw new Error('run_id 不能为空')
      const doArchive = args.archive !== false
      let note = ''
      if (!fs.existsSync(path.join(ws, 'runs', runId))) {
        const { all } = pickNewestRun(ws)
        note = `指定的任务目录不存在：runs/${runId}。`
          + (all.length ? `当前已有的任务：${all.join('、')}。` : '（runs/ 下还没有任何任务目录）')
      }

      const steps = [
        ['① 重跑落账' + (doArchive ? ' + 归档' : ''), 'validate_export.py', [`runs/${runId}`, ...(doArchive ? ['--archive'] : [])]],
        ['② 导出长表', 'tools/导出长表.py', [`runs/${runId}`]],
        ['③ 收尾报告', 'tools/收尾报告.py', [`runs/${runId}`]],
      ]

      const results = []
      for (const [label, script, argv] of steps) {
        const r = await runPython(ws, script, argv)
        results.push({ label, r })
      }

      const blocks = results.map(({ label, r }) => {
        const verdict = r.exitCode === 0 ? '退出码 0' : `退出码 ${r.exitCode === null ? '未拿到' : r.exitCode}`
        return `════════ ${label}（${verdict}）════════\n${r.output}`
      })

      const failed = results.filter(({ r }) => r.exitCode !== 0).map(({ label }) => label)
      const summary = failed.length
        ? `[宿主] 三步里有 ${failed.length} 步退出码非 0：${failed.join('、')}。请按上面的原文如实告诉用户是哪一步出的问题，不要只说「失败了」。`
        : '[宿主] 三步的退出码都是 0。但退出码 0 不等于干净 —— 请把上面脚本打印的文字读一遍，有问题照样要讲。'

      return {
        ok: failed.length === 0,
        exit_code: failed.length === 0 ? 0 : 1,
        run_id: runId,
        command: `python validate_export.py runs/${runId}${doArchive ? ' --archive' : ''} ; python tools/导出长表.py runs/${runId} ; python tools/收尾报告.py runs/${runId}`,
        note: `${note}${note ? ' ' : ''}分三步执行，逐条输出如下。归档开关：${doArchive ? '开（--archive）' : '关（没加 --archive）'}。`,
        output: [...blocks, '', summary].join('\n'),
      }
    },
    presentCall: (args) => ({
      card: 'generic',
      title: `收尾：落账${args && args.archive === false ? '' : ' + 归档'} + 导出长表 + 收尾报告`,
      kind: 'execute',
      rawInput: { run_id: args && args.run_id },
    }),
  }))

  ctx.tools.register(defineTool({
    name: 'table_image_register_schema',
    description:
      '注册一种新表型（版式契约）：把这张表的列、必填、合计规则落成 schemas/<sheet_name>_v1.json。'
      + '用在「立样板」阶段用户已确认样板之后。sheet_name 是这张表叫什么（例如「来料分析」）。'
      + '注册成功后，table_image_next 给出的契约摘要里就会带上这个表型。'
      + '同一张表改版要新开 _v2，旧版永久保留；本工具不会覆盖已有契约。'
      + '工作区里的 tools/注册表型.py 是权威，本工具只负责原样转达它的输出。',
    parameters: {
      run_id: { type: 'string', required: true, description: '任务号，形如 20260910_001。' },
      sheet_name: { type: 'string', required: true, description: '表型名，例如「来料分析」。' },
    },
    output: RUN_OUTPUT,
    async execute(args) {
      const runId = String(args.run_id || '').trim()
      const sheetName = String(args.sheet_name || '').trim()
      if (!runId) throw new Error('run_id 不能为空')
      if (!sheetName) throw new Error('sheet_name 不能为空')
      const run = await runPython(ws, 'tools/注册表型.py', [`runs/${runId}`, '--name', sheetName])
      return toValue(run, `表型名：${sheetName}`, runId)
    },
    presentCall: (args) => ({
      card: 'generic',
      title: `注册表型 ${args && args.sheet_name ? args.sheet_name : ''}`.trim(),
      kind: 'execute',
      rawInput: { run_id: args && args.run_id, sheet_name: args && args.sheet_name },
    }),
  }))
}

// ─────────────────────────────────────────────────────────────────────────────
// 上传端点（拖拽收图）
// ─────────────────────────────────────────────────────────────────────────────

/** 从魔数认图片格式；认不出返回 null。 */
function sniffImage(buf) {
  if (buf.length >= 8 && buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) return '.png'
  if (buf.length >= 3 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return '.jpg'
  if (buf.length >= 6 && buf.toString('latin1', 0, 3) === 'GIF') return '.gif'
  if (buf.length >= 12 && buf.toString('latin1', 0, 4) === 'RIFF' && buf.toString('latin1', 8, 12) === 'WEBP') return '.webp'
  if (buf.length >= 2 && buf[0] === 0x42 && buf[1] === 0x4d) return '.bmp'
  if (buf.length >= 4 && ((buf[0] === 0x49 && buf[1] === 0x49 && buf[2] === 0x2a) || (buf[0] === 0x4d && buf[1] === 0x4d && buf[2] === 0x00))) return '.tiff'
  return null
}

/** 去掉文件名里的目录成分与危险字符，保留中文。 */
function safeBaseName(inputName, fallbackExt) {
  let base = path.basename(String(inputName || '').replace(/\\/g, '/'))
  base = base.replace(/[\u0000-\u001f<>:"/\\|?*]/g, '_').replace(/^\.+/, '').trim()
  if (!base) base = `image_${Date.now()}${fallbackExt || '.jpg'}`
  if (base.length > 120) {
    const ext = path.extname(base)
    base = base.slice(0, 120 - ext.length) + ext
  }
  return base
}

/** 撞名就加 _2 / _3 …，绝不覆盖 inbox/ 里已有的图。 */
function uniquePath(dir, base) {
  let candidate = path.join(dir, base)
  if (!fs.existsSync(candidate)) return candidate
  const ext = path.extname(base)
  const stem = base.slice(0, base.length - ext.length)
  for (let i = 2; i < 10000; i += 1) {
    candidate = path.join(dir, `${stem}_${i}${ext}`)
    if (!fs.existsSync(candidate)) return candidate
  }
  return path.join(dir, `${stem}_${Date.now()}${ext}`)
}

/** 统一的 JSON 回包。 */
function sendJson(res, status, obj) {
  const body = Buffer.from(JSON.stringify(obj), 'utf8')
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': String(body.length),
    'Cache-Control': 'no-store',
  })
  res.end(body)
}

/** 读完整请求体，超过上限就中止。 */
function readBody(req, limit) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', (c) => {
      size += c.length
      if (size > limit) {
        reject(new Error(`请求体超过上限 ${limit} 字节`))
        try { req.destroy() } catch { /* 忽略 */ }
        return
      }
      chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', (e) => reject(e))
  })
}

/**
 * 给客户端半区用的落盘端点。
 *
 *   GET  /table-image/api           → { ok:true, workspace, inbox, images:[...], count }
 *   POST /table-image/api           → body { name, dataBase64 } → 写进 inbox/，回 { ok:true, saved }
 *
 * 只用 node:http 的原生 req/res，不引任何框架，也不解析 multipart
 * （客户端用 FileReader 转 base64，简单且可验证）。
 * @param ws - 工作区绝对路径。
 * @returns 处理函数。
 */function createRouteHandler(ws) {
  const inboxDir = path.join(ws, 'inbox')
  const inboxImages = () => {
    if (!fs.existsSync(inboxDir)) return []
    return fs.readdirSync(inboxDir, { withFileTypes: true })
      .filter((d) => d.isFile() && IMAGE_EXT.has(path.extname(d.name).toLowerCase()))
      .map((d) => d.name)
      .sort()
  }

  return async (req, res) => {
    try {
      if (req.method === 'GET') {
        sendJson(res, 200, {
          ok: true,
          workspace: ws,
          inbox: inboxDir,
          count: inboxImages().length,
          images: inboxImages().slice(-50),
        })
        return
      }
      if (req.method !== 'POST') {
        sendJson(res, 405, { ok: false, error: `不支持的方法 ${req.method}，只用 GET / POST` })
        return
      }

      const raw = await readBody(req, MAX_UPLOAD_BYTES)
      let payload
      try {
        payload = JSON.parse(raw.toString('utf8'))
      } catch {
        sendJson(res, 400, { ok: false, error: '请求体不是合法 JSON' })
        return
      }

      const b64 = typeof payload.dataBase64 === 'string' ? payload.dataBase64 : ''
      if (!b64) {
        sendJson(res, 400, { ok: false, error: '缺少 dataBase64（图片的 base64，可带 data:image/...;base64, 前缀）' })
        return
      }
      const comma = b64.indexOf(',')
      const pure = b64.startsWith('data:') && comma !== -1 ? b64.slice(comma + 1) : b64

      let buf
      try {
        buf = Buffer.from(pure, 'base64')
      } catch {
        sendJson(res, 400, { ok: false, error: 'dataBase64 解不开' })
        return
      }
      if (buf.length === 0) {
        sendJson(res, 400, { ok: false, error: '解出来的文件是空的' })
        return
      }
      const sniffed = sniffImage(buf)
      if (sniffed === null) {
        sendJson(res, 400, {
          ok: false,
          error: '这些字节不像图片（PNG/JPEG/GIF/WebP/BMP/TIFF 都不是）。本端点只收图片，没有落盘。',
        })
        return
      }

      await fsp.mkdir(inboxDir, { recursive: true })
      const base = safeBaseName(payload.name, sniffed)
      const finalPath = uniquePath(inboxDir, base)
      // wx = 只新建、不覆盖。理论上撞不到（uniquePath 已查过），撞到就报错而不覆盖。
      await fsp.writeFile(finalPath, buf, { flag: 'wx' })

      sendJson(res, 200, {
        ok: true,
        saved: path.basename(finalPath),
        path: finalPath,
        bytes: buf.length,
        sha256: createHash('sha256').update(buf).digest('hex'),
        inboxCount: inboxImages().length,
      })
    } catch (err) {
      try {
        sendJson(res, 500, { ok: false, error: String((err && err.message) || err) })
      } catch { /* 响应已经发出去就算了 */ }
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 入口
// ─────────────────────────────────────────────────────────────────────────────

/**
 * 插件入口。先解析工作区（拿不到就抛一个讲清三条出路的错误），
 * 再注册工具，最后按需挂上传端点。
 * @param ctx - 插件上下文。
 * @param config - patch 行的 config: 段。
 */
export function apply(ctx, config) {
  const ws = resolveWorkspace(config)

  registerTools(ctx, ws)

  // webServer 是可选服务：没有它也要能用（只是拖拽收图不可用）。
  // 用 ctx.inject(['webServer'], cb) 而不是写进 inject 数组 —— 缺服务时不会让插件加载失败。
  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => {
      const route = { kind: 'exact', path: ROUTE_PATH, handler: createRouteHandler(ws) }
      // register() 遇到重复 (kind, path) 会抛错，而路由表是按 path 存的单表。
      // 插件被热重载 / 上一次失败的 fiber 留下孤儿路由时，注册会撞车 —— 那时不该让整个插件挂掉。
      // 这条自愈写法抄自本机在跑的 dsh-tavernweave/lib/index.js:531-539（它有同样的注释与处置）。
      try {
        const disposer = webCtx.webServer.register(route)
        if (typeof disposer === 'function') return disposer
      } catch {
        try {
          const table = webCtx.webServer.exact
          if (table && typeof table.set === 'function') {
            table.set(ROUTE_PATH, route)
            return () => { try { table.delete(ROUTE_PATH) } catch { /* 忽略 */ } }
          }
        } catch { /* 表结构不是预期的，就当没挂上 */ }
      }
      return undefined
    }, `dsh-table-image: 上传端点 ${ROUTE_PATH}`)
  })

  ctx.logger?.info?.(`dsh-table-image: 工作区 ${ws}；已注册 9 个 table_image_* 工具；上传端点 ${ROUTE_PATH}`)
}
