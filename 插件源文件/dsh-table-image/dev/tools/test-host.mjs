// 宿主半区测试台：把真实的 index.js 跑起来，验证工具注册、工作区定位、上传端点与命令拼装。
//
// 用法：
//   node --import ./dev/tools/stub-loader.mjs dev/tools/test-host.mjs
//   （或运行 dev\tools\跑宿主测试.bat）
//
// ── 这个测试台怎么"骗"过宿主半区 ────────────────────────────────────────────
// 宿主半区是 spawn('python', [脚本, ...])。本机 Windows 上 `where python` 给的是
// 一个不带扩展名的启动器，而 Node 的 child_process 在 Windows 上按 .exe 解析
// （.cmd/.bat 不参与，除非走 shell:true，而本插件特意没走 shell）——
// 所以在 PATH 前面放一个假的 python.cmd 是**骗不过** Node 的，实测会被忽略。
// 因此这里换成"往 PATH 前面放一个真的 python.exe"：一个用 C# 现编译的小启动器，
// 它把收到的参数原样打印出来（好让测试断言命令拼得对不对），再按固定输出回话。
//
// 覆盖：
//   · 工作区定位的三条出路 + 三条都不通时的那个唯一错误
//   · 9 个 table_image_* 工具全部注册、名字/描述/参数/输出/呈现齐全
//   · 每个工具的 execute 都真跑一遍（真 python 进程），检查命令与返回值
//   · 退出码非 0 时 ok=false、退出码原样带回
//   · 脚本不存在时给的是"脚本不存在"而不是假装成功
//   · 上传端点：GET 探活 / POST 落盘 / 非图片拒收 / 路径穿越被洗掉 / 撞名不覆盖

import { execFileSync, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..', '..')
const INDEX = path.join(ROOT, 'index.js')

// ─────────────────────────────────────────────────────────────────────────────
// 结果收集
// ─────────────────────────────────────────────────────────────────────────────
const results = []
let currentSection = '(未命名)'

function section(name) {
  currentSection = name
  console.log(`\n=== ${name} ===`)
}
function ok(msg) { results.push({ section: currentSection, pass: true, msg }); console.log('  ✅ ' + msg) }
function bad(msg) { results.push({ section: currentSection, pass: false, msg }); console.log('  ❌ ' + msg) }
function check(cond, msg, extra) { if (cond) ok(msg); else bad(msg + (extra ? '  ← ' + extra : '')) }

// ─────────────────────────────────────────────────────────────────────────────
// 临时目录 + 假 python（真 exe）
// ─────────────────────────────────────────────────────────────────────────────
const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'dsh-table-image-test-'))
const BIN = path.join(TMP, 'bin')
fs.mkdirSync(BIN, { recursive: true })

const HOST = process.execPath || 'node'

/** 假 python 的源码（C#）。纯 ASCII，用 csc 编译成真 exe。 */
const STUB_CS = `
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

public static class StubPython {
  public static int Main(string[] args) {
    string log = Environment.GetEnvironmentVariable("DSH_PY_STUB_LOG");
    if (!String.IsNullOrEmpty(log)) {
      try { File.AppendAllText(log, "ARGS=" + String.Join(" ", args) + "\\n", new UTF8Encoding(false)); } catch {}
    }
    string mode = Environment.GetEnvironmentVariable("DSH_PY_STUB_MODE");
    if (String.IsNullOrEmpty(mode)) mode = "ok";
    if (mode == "empty") return 0;
    if (mode == "fail") {
      Console.Error.WriteLine("STUB-ERROR(exit3): " + String.Join(" ", args));
      Console.Out.WriteLine("partial stdout before failing");
      return 3;
    }
    if (mode == "big") {
      var sb = new StringBuilder();
      for (int i = 0; i < 4000; i++) sb.Append("padding-padding-padding-padding-padding-padding-").Append(i).Append("\\n");
      Console.Out.Write(sb.ToString());
      return 0;
    }
    Console.Out.WriteLine("STUB-OK " + String.Join(" ", args));
    Console.Out.WriteLine("stdout line two");
    return 0;
  }
}
`.trim()

const STUB_EXE = path.join(BIN, 'python.exe')
let stubBuilt = false
{
  const csFile = path.join(TMP, 'stub-python.cs')
  fs.writeFileSync(csFile, STUB_CS, 'ascii')
  const candidates = [
    path.join(process.env.WINDIR || 'C:\\Windows', 'Microsoft.NET', 'Framework64', 'v4.0.30319', 'csc.exe'),
    path.join(process.env.WINDIR || 'C:\\Windows', 'Microsoft.NET', 'Framework', 'v4.0.30319', 'csc.exe'),
  ]
  const csc = candidates.find((p) => fs.existsSync(p))
  if (csc) {
    try {
      execFileSync(csc, ['/nologo', '/optimize+', `/out:${STUB_EXE}`, csFile], { stdio: 'pipe' })
      stubBuilt = fs.existsSync(STUB_EXE)
    } catch (e) {
      console.log('csc 编译失败：' + e.message)
    }
  }
}

// PATH 前面插上假 python 所在目录（写进环境变量，之后所有 spawn 都继承它）
process.env.PATH = `${BIN}${path.delimiter}${process.env.PATH}`
process.env.DSH_PY_STUB_LOG = path.join(TMP, 'python-args.log')
process.env.DSH_PY_STUB_MODE = 'ok'
delete process.env.DSH_TABLE_IMAGE_WORKSPACE

console.log('临时目录：' + TMP)
console.log('node：' + HOST)

section('前置：假 python 必须真的被 spawn("python") 选中')
{
  check(stubBuilt, `假 python.exe 编译成功（${STUB_EXE}）`, stubBuilt ? '' : '没找到 csc.exe')
  const probe = spawnSync('python', ['--probe'], { encoding: 'utf8', windowsHide: true })
  const out = (probe.stdout || '') + (probe.stderr || '')
  check(out.includes('STUB-OK --probe'),
    'Node 的 spawn("python") 命中的是假 python（不是本机真 python）',
    `status=${probe.status} out=${JSON.stringify(out.slice(0, 200))}`)
  console.log('  探测输出：' + JSON.stringify(out.trim()))
  const real = (() => {
    try { return spawnSync('where', ['python'], { encoding: 'utf8', windowsHide: true }).stdout.trim().split(/\r?\n/)[0] } catch { return '?' }
  })()
  console.log('  where python 的第一条（已被假 python 顶到最前）：' + real)
  console.log('  假 python：' + STUB_EXE)
  if (!out.includes('STUB-OK --probe')) {
    console.log('\n❌ 假 python 没生效，宿主测试没法进行。')
    console.log('   这本身是个有价值的发现：本机的 python 是一个不带扩展名的启动器，')
    console.log('   而 Windows 的 CreateProcess 只按 .exe 解析 PATH —— 用 .cmd 假货是骗不过去的。')
    process.exit(1)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 造一个假工作区
// ─────────────────────────────────────────────────────────────────────────────
function makeWorkspace(name, withScripts) {
  const ws = path.join(TMP, name)
  fs.mkdirSync(path.join(ws, 'runs', '20260910_001'), { recursive: true })
  fs.mkdirSync(path.join(ws, 'runs', '20260910_002'), { recursive: true })
  fs.mkdirSync(path.join(ws, 'inbox'), { recursive: true })
  fs.writeFileSync(path.join(ws, 'inbox', '已有图.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))
  fs.writeFileSync(path.join(ws, 'inbox', '说明.txt'), 'not an image')
  if (withScripts) {
    fs.writeFileSync(path.join(ws, 'validate_export.py'), '# stub\n')
    fs.mkdirSync(path.join(ws, 'tools'), { recursive: true })
    for (const n of ['建任务.py', '提交识别.py', '导入审核.py', '注册表型.py', '导出长表.py', '收尾报告.py']) {
      fs.writeFileSync(path.join(ws, 'tools', n), '# stub\n')
    }
  }
  return ws
}

const WS = makeWorkspace('ws-full', true)
const WS_NO_SCRIPTS = makeWorkspace('ws-noscripts', false)

// ─────────────────────────────────────────────────────────────────────────────
// 加载插件模块
// ─────────────────────────────────────────────────────────────────────────────
section('加载宿主半区')
check(fs.existsSync(INDEX), 'index.js 在')

globalThis.__DSH_STUB_TOOLS__ = []
let mod
try {
  mod = await import(pathToFileURL(INDEX).href)
  ok('import(index.js) 成功（@deepseek-ai/dsh-tools 由替身提供）')
} catch (e) {
  bad('import 失败：' + e.message)
  console.log('\n=== 结论 ===\n  ❌ 没法继续')
  process.exit(1)
}
check(typeof mod.apply === 'function', '导出了 apply()')
check(Array.isArray(mod.inject) && mod.inject.includes('tools'), `inject 声明包含 'tools'（实际 ${JSON.stringify(mod.inject)}）`)
check(mod.inject && !mod.inject.includes('webServer'), "inject 里**没有** webServer（它是可选的，写进去会在缺服务时让插件加载失败）")

// ─────────────────────────────────────────────────────────────────────────────
// 工作区定位
// ─────────────────────────────────────────────────────────────────────────────
section('工作区定位：三条出路 + 全不通时的报错')
check(typeof mod.resolveWorkspace === 'function', '导出了 resolveWorkspace()')

try {
  const got = mod.resolveWorkspace({ workspace: WS })
  check(got === path.resolve(WS), `① config.workspace 生效：${got}`)
} catch (e) { bad('① config.workspace 抛异常：' + e.message) }

process.env.DSH_TABLE_IMAGE_WORKSPACE = WS
try {
  const got = mod.resolveWorkspace({})
  check(got === path.resolve(WS), `② 环境变量 DSH_TABLE_IMAGE_WORKSPACE 生效：${got}`)
} catch (e) { bad('② 环境变量抛异常：' + e.message) }
delete process.env.DSH_TABLE_IMAGE_WORKSPACE

// ③ workspace.txt（带 BOM，模拟记事本另存为「UTF-8 带 BOM」）
{
  const txtPath = path.join(ROOT, 'workspace.txt')
  let wrote = false
  try {
    fs.writeFileSync(txtPath, '\uFEFF' + WS + '\r\n', 'utf8')
    wrote = true
    const got = mod.resolveWorkspace({})
    check(got === path.resolve(WS), `③ workspace.txt（带 BOM）生效，且 BOM 被剥掉：${got}`)
  } catch (e) { bad('③ workspace.txt 抛异常：' + e.message) }
  finally { if (wrote) { try { fs.unlinkSync(txtPath) } catch { /* 忽略 */ } } }
}

process.env.DSH_TABLE_IMAGE_WORKSPACE = WS_NO_SCRIPTS
try {
  const got = mod.resolveWorkspace({ workspace: WS })
  check(got === path.resolve(WS), 'config.workspace 的优先级高于环境变量')
} catch (e) { bad('优先级检查抛异常：' + e.message) }
delete process.env.DSH_TABLE_IMAGE_WORKSPACE

{
  let err = null
  try { mod.resolveWorkspace({ workspace: path.join(TMP, '不存在的目录') }) } catch (e) { err = e }
  check(!!err, '三条出路都不通时抛错（没有静默返回空值）')
  if (err) {
    const m = err.message
    const named = ['config.workspace', 'DSH_TABLE_IMAGE_WORKSPACE', 'workspace.txt']
    const missing = named.filter((n) => !m.includes(n))
    check(missing.length === 0, '抛出的这一个错误里同时点名了三条出路', missing.length ? '没提到：' + missing.join('、') : '')
    check(m.split('\n').length >= 5, '错误里逐条列出了「每个出路为什么不行」')
    console.log('  错误原文：')
    m.split('\n').forEach((l) => console.log('    ' + l))
  }
}

{
  let err = null
  try { mod.resolveWorkspace({ workspace: path.join(TMP, 'no-such-ws') }) } catch (e) { err = e }
  check(!!err, 'config.workspace 指向不存在的目录时，不静默接受')
}

// ─────────────────────────────────────────────────────────────────────────────
// 工具注册
// ─────────────────────────────────────────────────────────────────────────────
section('注册 9 个 table_image_* 工具')

const EXPECTED = [
  'table_image_status',
  'table_image_start',
  'table_image_next',
  'table_image_submit',
  'table_image_check',
  'table_image_review',
  'table_image_import_review',
  'table_image_finalize',
  'table_image_register_schema',
]

const registered = []
const routes = []
const effects = []

function makeFakeCtx() {
  return {
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    tools: { register: (def) => { registered.push(def); return () => {} } },
    effect: (fn, label) => { effects.push(label); return fn() },
    inject: (deps, cb) => {
      const webCtx = {
        webServer: { register: (route) => { routes.push(route); return () => {} } },
        effect: (fn, label) => { effects.push(label); return fn() },
      }
      cb(webCtx)
    },
  }
}

try {
  mod.apply(makeFakeCtx(), { workspace: WS })
  ok('apply(fakeCtx, { workspace }) 未抛异常')
} catch (e) {
  bad('apply 抛异常：' + e.message + '\n' + e.stack)
}

const names = registered.map((t) => t.name)
check(EXPECTED.filter((n) => !names.includes(n)).length === 0, '9 个工具名一个不缺',
  '缺：' + EXPECTED.filter((n) => !names.includes(n)).join('、'))
check(names.filter((n) => !EXPECTED.includes(n)).length === 0, '没有多注册计划外的工具',
  '多出：' + names.filter((n) => !EXPECTED.includes(n)).join('、'))
check(globalThis.__DSH_STUB_TOOLS__.length === 9, `defineTool 被调了 9 次（实际 ${globalThis.__DSH_STUB_TOOLS__.length}）`)

const needsParams = {
  table_image_status: ['run_id'],
  table_image_start: ['run_id', 'files', 'all_inbox'],
  table_image_next: ['run_id'],
  table_image_submit: ['run_id', 'image_id', 'fragment'],
  table_image_check: ['run_id'],
  table_image_review: ['run_id'],
  table_image_import_review: ['run_id'],
  table_image_finalize: ['run_id', 'archive'],
  table_image_register_schema: ['run_id', 'sheet_name'],
}
const requiredParams = {
  table_image_submit: ['run_id', 'image_id', 'fragment'],
  table_image_finalize: ['run_id'],
  table_image_register_schema: ['run_id', 'sheet_name'],
}
const KINDS = ['read', 'edit', 'delete', 'move', 'search', 'execute', 'fetch', 'other']

for (const t of registered) {
  const p = []
  if (!t.description || t.description.length < 60) p.push('描述太短')
  if (!/脚本|权威|裁判/.test(t.description || '')) p.push('描述里没说清"脚本才是权威"')
  if (!/用|何时|时用|之后|前|用来|用在|要|先|再/.test(t.description || '')) p.push('描述里没说清什么时候用')
  if (!t.parameters || typeof t.parameters !== 'object') p.push('没有 parameters')
  const want = needsParams[t.name]
  if (want) {
    const got = Object.keys(t.parameters || {})
    const miss = want.filter((k) => !got.includes(k))
    if (miss.length) p.push('参数缺：' + miss.join('、'))
  }
  // required 必须是**布尔 true**（parameterSchemaSpecToJsonSchema 只认这个，见 README 证据）
  for (const [k, spec] of Object.entries(t.parameters || {})) {
    if (Object.prototype.hasOwnProperty.call(spec, 'required') && spec.required !== true) {
      p.push(`参数 ${k} 的 required 不是布尔 true`)
    }
  }
  for (const k of requiredParams[t.name] || []) {
    if (t.parameters?.[k]?.required !== true) p.push(`参数 ${k} 应该是 required: true`)
  }
  for (const k of ['run_id', 'files', 'all_inbox']) {
    // 只有在"不属于本工具必填参数"时，才要求它是可选的
    if ((requiredParams[t.name] || []).includes(k)) continue
    if (t.parameters?.[k] && t.parameters[k].required === true) p.push(`参数 ${k} 不该是必填`)
  }
  if (t.parameters?.files) {
    if (t.parameters.files.type !== 'array') p.push('files 不是 array')
    if (t.parameters.files.items?.type !== 'string') p.push('files 的 items 不是 string')
  }
  if (t.parameters?.fragment && t.parameters.fragment.type !== 'object') p.push('fragment 不是 object')
  if (typeof t.execute !== 'function') p.push('没有 execute')
  if (typeof t.presentCall !== 'function') p.push('没有 presentCall')
  if (!t.output || typeof t.output.render !== 'function') p.push('没有 output.render')
  if (!t.output || !t.output.schema) p.push('没有 output.schema')

  let view = null
  try {
    view = t.presentCall({ run_id: '20260910_001', image_id: 'IMG_0001', sheet_name: 'X', fragment: {} })
  } catch (e) { p.push('presentCall 抛异常：' + e.message) }
  if (view) {
    if (view.card !== 'generic') p.push(`presentCall.card 是 ${view.card}`)
    if (view.kind && !KINDS.includes(view.kind)) p.push(`presentCall.kind=${view.kind} 不在真实枚举里`)
    if (!view.title) p.push('presentCall.title 是空的')
  }

  if (p.length) bad(`${t.name}：${p.join('；')}`)
  else ok(`${t.name} 定义完整（参数/输出/presentCall/描述都齐）`)
}

section('presentCall 的读/写分类')
{
  const byName0 = Object.fromEntries(registered.map((t) => [t.name, t]))
  for (const n of ['table_image_status', 'table_image_next']) {
    const v = byName0[n]?.presentCall({ run_id: 'x', files: [], all_inbox: true })
    check(v && v.kind === 'read', `${n} 报成 read`)
  }
  for (const n of ['table_image_start', 'table_image_check', 'table_image_review', 'table_image_import_review', 'table_image_register_schema']) {
    const v = byName0[n]?.presentCall({ run_id: 'x', sheet_name: 's' })
    check(v && v.kind === 'execute', `${n} 报成 execute`)
  }
  for (const n of ['table_image_submit', 'table_image_finalize']) {
    const v = byName0[n]?.presentCall({ run_id: 'x', image_id: 'i', fragment: {}, archive: true })
    check(v && v.kind === 'execute', `${n} 报成 execute`)
  }
  const used = new Set(registered.map((t) => t.presentCall({ run_id: 'x', image_id: 'i', sheet_name: 's', fragment: {} })?.kind))
  check([...used].every((k) => KINDS.includes(k)), `用到的 kind 都在真实枚举内：${[...used].join(', ')}`)
  check(!used.has('write'), "没有用不存在的 kind: 'write'（真实枚举里没有 write）")
}

// ─────────────────────────────────────────────────────────────────────────────
// 上传端点
// ─────────────────────────────────────────────────────────────────────────────
section('上传端点 HTTP 行为')
check(routes.length === 1, `注册了 1 条路由（实际 ${routes.length}）`)
const route = routes[0]
if (route) {
  check(route.kind === 'exact', `路由 kind=exact（实际 ${route.kind}）`)
  check(route.path === '/table-image/api', `路由 path=/table-image/api（实际 ${route.path}）`)
  check(typeof route.handler === 'function', '路由 handler 是函数')
  check(effects.some((l) => String(l).includes('dsh-table-image')), '路由通过 ctx.effect 挂了生命周期')
}

/** 造一个假的 req/res，跑一遍 route.handler。 */
function callRoute(method, body, rawBody) {
  return new Promise((resolve) => {
    const chunks = rawBody !== undefined
      ? [Buffer.from(rawBody, 'utf8')]
      : (body === undefined ? [] : [Buffer.from(JSON.stringify(body), 'utf8')])
    const req = {
      method,
      url: '/table-image/api',
      on(evt, cb) {
        if (evt === 'data') chunks.forEach((c) => cb(c))
        if (evt === 'end') cb()
        return req
      },
      destroy() {},
    }
    let status = null
    let headers = null
    const bufs = []
    const res = {
      writeHead(code, h) { status = code; headers = h },
      end(chunk) {
        if (chunk) bufs.push(Buffer.from(chunk))
        resolve({ status, headers, body: Buffer.concat(bufs).toString('utf8') })
      },
    }
    route.handler(req, res).catch((e) => resolve({ status: 500, body: 'handler threw: ' + e.message }))
  })
}

if (route) {
  let r = await callRoute('GET')
  let json = null
  try { json = JSON.parse(r.body) } catch { /* 不是 JSON */ }
  check(r.status === 200, `GET 返回 200（实际 ${r.status}）`)
  check(json && json.ok === true, 'GET 回 { ok: true }')
  check(json && json.workspace === WS, `GET 报了工作区路径（${json && json.workspace}）`)
  check(json && json.count === 1, `GET 只数图片、不数说明.txt（count=${json && json.count}）`)
  check(json && Array.isArray(json.images) && json.images.includes('已有图.png'), 'GET 列出 inbox 里的图')
  check(r.headers && String(r.headers['Content-Type']).includes('application/json'), 'GET 声明了 JSON content-type')

  r = await callRoute('DELETE')
  check(r.status === 405, `DELETE 返回 405（实际 ${r.status}）`)

  const before = fs.readdirSync(path.join(WS, 'inbox')).length
  r = await callRoute('POST', { name: 'fake.png', dataBase64: Buffer.from('这不是图片').toString('base64') })
  check(r.status === 400, `非图片字节返回 400（实际 ${r.status}）`)
  check(fs.readdirSync(path.join(WS, 'inbox')).length === before, '非图片字节没有落盘')

  const png = Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    Buffer.from('fake-png-body-一二三', 'utf8'),
  ])
  r = await callRoute('POST', { name: '新样品.png', dataBase64: 'data:image/png;base64,' + png.toString('base64') })
  json = null
  try { json = JSON.parse(r.body) } catch { /* 不是 JSON */ }
  check(r.status === 200 && json && json.ok === true, `真 PNG 收下（status=${r.status}）`)
  check(json && json.saved === '新样品.png', `落盘文件名正确（${json && json.saved}）`)
  check(json && fs.existsSync(json.path), '回包里的 path 真的存在')
  check(json && json.sha256 && json.sha256.length === 64, '回了 sha256')
  check(json && fs.readFileSync(json.path).equals(png), '落盘的字节与上传的字节一致')

  const second = Buffer.concat([png, Buffer.from([0x01])])
  r = await callRoute('POST', { name: '新样品.png', dataBase64: second.toString('base64') })
  json = JSON.parse(r.body)
  check(json.saved !== '新样品.png' && json.saved.startsWith('新样品'), `撞名自动改名为 ${json.saved}（不覆盖 inbox 已有的图）`)
  check(fs.readFileSync(json.path).equals(second), '改名的那个文件内容是对的')

  r = await callRoute('POST', { name: '../../../evil.png', dataBase64: png.toString('base64') })
  json = JSON.parse(r.body)
  check(r.status === 200 && json.saved === 'evil.png', `路径穿越被洗成纯文件名（${json.saved}）`)
  check(fs.existsSync(path.join(WS, 'inbox', 'evil.png')), '穿越后的文件老老实实落在 inbox/ 里')
  check(!fs.existsSync(path.join(TMP, 'evil.png')), '没有写到 inbox/ 外面去')

  r = await callRoute('POST', { name: 'x.png' })
  check(r.status === 400, `缺 dataBase64 返回 400（实际 ${r.status}）`)

  r = await callRoute('POST', undefined)
  check(r.status === 400, `空 body 返回 400（实际 ${r.status}）`)

  r = await callRoute('POST', undefined, '这不是 JSON')
  check(r.status === 400, `body 不是合法 JSON 返回 400（实际 ${r.status}）`)

  // 已有图不能被覆盖
  const beforeBytes = fs.readFileSync(path.join(WS, 'inbox', '已有图.png'))
  r = await callRoute('POST', { name: '已有图.png', dataBase64: png.toString('base64') })
  json = JSON.parse(r.body)
  check(fs.readFileSync(path.join(WS, 'inbox', '已有图.png')).equals(beforeBytes), '同名的已有图**没有被覆盖**')
  check(json.saved !== '已有图.png', `同名时存成了 ${json.saved}`)
}

// ─────────────────────────────────────────────────────────────────────────────
// 每个工具真跑一遍（真 python 进程）
// ─────────────────────────────────────────────────────────────────────────────
section('工具执行：命令拼装与输出回传（真 python 进程）')

const byName = Object.fromEntries(registered.map((t) => [t.name, t]))
const execCtx = { signal: undefined }
const LOG = process.env.DSH_PY_STUB_LOG

function argsLog() {
  try { return fs.readFileSync(LOG, 'utf8').trim().split(/\r?\n/).filter(Boolean) } catch { return [] }
}
function clearLog() { try { fs.writeFileSync(LOG, '') } catch { /* 忽略 */ } }

async function callTool(name, args) {
  const t = byName[name]
  const value = await t.execute(args, execCtx)
  const text = t.output.render(args, value)[0].text
  return { value, text }
}

async function assertCall(label, toolName, args, expectScript, expectArgs) {
  clearLog()
  let out
  try {
    out = await callTool(toolName, args)
  } catch (e) {
    bad(`${label}：execute 抛异常 ${e.message}`)
    return null
  }
  const joined = argsLog().join(' | ')
  check(argsLog().length >= 1, `${label}：真的起了 python 进程`, joined)
  check(joined.includes(expectScript), `${label}：脚本是 ${expectScript}`, joined)
  for (const a of expectArgs) check(joined.includes(a), `${label}：参数里有 ${a}`, joined)
  check(out.value.ok === true, `${label}：ok=true`)
  check(out.value.exit_code === 0, `${label}：exit_code=0（实际 ${out.value.exit_code}）`)
  check(out.text.includes('──── stdout ────'), `${label}：渲染文本里有 stdout 段`)
  check(out.text.includes('STUB-OK'), `${label}：脚本打印的文字被原样带回来了`)
  check(out.text.includes('工作目录：' + WS), `${label}：cwd 是工作区`)
  return out
}

await assertCall('status（指定 run_id）', 'table_image_status', { run_id: '20260910_001' },
  'validate_export.py', ['runs/20260910_001', '--status'])
{
  const out = await assertCall('status（省略 run_id → 挑最新）', 'table_image_status', {},
    'validate_export.py', ['runs/20260910_002', '--status'])
  check(out && out.value.run_id === '20260910_002', `挑了最新任务 20260910_002（实际 ${out && out.value.run_id}）`)
  check(out && out.value.note.includes('自动挑最新'), `note 说明了挑的是哪个：${out && out.value.note}`)
}
await assertCall('start（收 inbox 全部，不传 files）', 'table_image_start', { all_inbox: true },
  'tools/建任务.py', [])
await assertCall('start（指定文件）', 'table_image_start', { files: ['C:\\a\\b.png', 'C:\\a\\c.jpg'] },
  'tools/建任务.py', ['C:\\a\\b.png', 'C:\\a\\c.jpg'])
{
  clearLog()
  const out = await callTool('table_image_start', { run_id: '20260910_003' })
  check(argsLog().join(' ').includes('--run-id 20260910_003'), 'start：把 run_id 转成了 --run-id', argsLog().join(' '))
  check(out.value.note.includes('收图范围'), `start：note 交代了收图范围（${out.value.note}）`)
}
await assertCall('next', 'table_image_next', { run_id: '20260910_001' },
  'tools/提交识别.py', ['runs/20260910_001', '--next'])
await assertCall('check', 'table_image_check', { run_id: '20260910_001' },
  'validate_export.py', ['runs/20260910_001', '--init-issues'])
await assertCall('review', 'table_image_review', { run_id: '20260910_001' },
  'validate_export.py', ['runs/20260910_001'])
await assertCall('import_review', 'table_image_import_review', { run_id: '20260910_001' },
  'tools/导入审核.py', ['runs/20260910_001'])
await assertCall('register_schema', 'table_image_register_schema', { run_id: '20260910_001', sheet_name: '来料分析' },
  'tools/注册表型.py', ['--name', '来料分析'])

section('table_image_review：回包里带上 task_review.xlsx 的路径')
{
  clearLog()
  const out = await callTool('table_image_review', { run_id: '20260910_001' })
  check(out.value.note.includes('task_review.xlsx'), `note 里有审核表路径：${out.value.note}`)
  check(out.value.note.includes('但这个文件不在'), '文件其实不存在时如实说明（假 python 不会真生成它）')
  // 真放一个文件进去，看措辞会不会变成"就是这条路径"
  const xlsx = path.join(WS, 'runs', '20260910_001', 'task_review.xlsx')
  fs.writeFileSync(xlsx, 'stub')
  const out2 = await callTool('table_image_review', { run_id: '20260910_001' })
  check(!out2.value.note.includes('但这个文件不在'), '文件存在时不再报"不在"')
  check(out2.value.note.includes('20260910_001'), '路径里带 run_id')
}

section('table_image_submit：临时片段文件真的写了、也真的删了')
{
  clearLog()
  const frag = { image_id: 'IMG_0001', sheet_label: '来料分析', rows: [{ row_role: 'data' }] }
  const out = await callTool('table_image_submit', { run_id: '20260910_001', image_id: 'IMG_0001', fragment: frag })
  const joined = argsLog().join(' | ')
  check(joined.includes('--image IMG_0001'), '命令里有 --image IMG_0001', joined)
  const m = joined.match(/--file (\S+\.json)/)
  check(!!m, '命令里有 --file <临时文件>.json', joined)
  if (m) {
    check(!fs.existsSync(m[1]), `跑完临时文件已删除（${m[1]}）`)
  }
  check(out.value.ok === true, 'submit：ok=true')
  check(out.value.note.includes('临时文件'), `note 里说明了临时文件的处置：${out.value.note}`)

  clearLog()
  await callTool('table_image_submit', { run_id: '20260910_001', image_id: 'IMG_0001', fragment: frag, replace: true })
  check(argsLog().join(' ').includes('--replace'), 'replace=true 时带上了 --replace', argsLog().join(' '))

  for (const bogus of [[1, 2], 'string']) {
    let threw = null
    try { await byName.table_image_submit.execute({ run_id: 'r', image_id: 'i', fragment: bogus }, execCtx) } catch (e) { threw = e }
    check(!!threw, `fragment 传 ${Array.isArray(bogus) ? '数组' : typeof bogus} 时直接拒（不当成对象用）`)
  }
  for (const badArgs of [{ image_id: 'i', fragment: {} }, { run_id: 'r', fragment: {} }, { run_id: 'r', image_id: '', fragment: {} }]) {
    let threw = null
    try { await byName.table_image_submit.execute(badArgs, execCtx) } catch (e) { threw = e }
    check(!!threw, `submit 缺 run_id / image_id 时直接拒（${JSON.stringify(badArgs).slice(0, 40)}）`)
  }
}

section('table_image_finalize：三步都跑，顺序与开关要对')
{
  clearLog()
  const out = await callTool('table_image_finalize', { run_id: '20260910_001' })
  const joined = argsLog().join(' | ')
  const i1 = joined.indexOf('validate_export.py')
  const i2 = joined.indexOf('导出长表.py')
  const i3 = joined.indexOf('收尾报告.py')
  check(i1 !== -1 && i2 !== -1 && i3 !== -1, '三步脚本都跑了', joined)
  check(i1 < i2 && i2 < i3, '顺序是 落账 → 导出长表 → 收尾报告')
  check(joined.includes('--archive'), 'archive 默认 true，带了 --archive', joined)
  check(out.text.includes('① 重跑落账 + 归档') && out.text.includes('② 导出长表') && out.text.includes('③ 收尾报告'),
    '输出里三段分开放，能看出每步的结论')
  check(out.value.ok === true, 'finalize：ok=true')

  clearLog()
  const out2 = await callTool('table_image_finalize', { run_id: '20260910_001', archive: false })
  check(!argsLog().join(' | ').includes('--archive'), 'archive=false 时不带 --archive')
  check(out2.value.note.includes('归档开关：关'), `note 里写明了归档开关状态：${out2.value.note}`)

  let threw = null
  try { await byName.table_image_finalize.execute({}, execCtx) } catch (e) { threw = e }
  check(!!threw, 'finalize 缺 run_id 直接拒')
}

section('退出码非 0：必须如实报失败')
{
  process.env.DSH_PY_STUB_MODE = 'fail'
  clearLog()
  const out = await callTool('table_image_status', { run_id: '20260910_001' })
  check(out.value.ok === false, 'ok=false')
  check(out.value.exit_code === 3, `exit_code=3 原样带回（实际 ${out.value.exit_code}）`)
  check(out.text.includes('STUB-ERROR'), 'stderr 原文被带回来了')
  check(out.text.includes('──── stderr ────'), '渲染文本里有 stderr 段')
  check(out.text.includes('退出码非 0'), '明确提示模型留意非 0 退出码')
  check(out.text.includes('partial stdout'), '失败时 stdout 也没丢')

  // finalize 的某一步失败要指出是哪一步
  const out2 = await callTool('table_image_finalize', { run_id: '20260910_001' })
  check(out2.value.ok === false, 'finalize：有步骤失败时 ok=false')
  check(out2.text.includes('步退出码非 0'), `finalize 指出是哪一步失败：${out2.text.slice(-260).replace(/\n/g, ' / ')}`)
  process.env.DSH_PY_STUB_MODE = 'ok'
}

section('退出码 0 但没打印 → 不编造内容')
{
  process.env.DSH_PY_STUB_MODE = 'empty'
  const out = await callTool('table_image_status', { run_id: '20260910_001' })
  check(out.value.ok === true, 'ok=true（退出码确实是 0）')
  check(out.text.includes('（空）'), 'stdout 段如实写「（空）」，不编内容')
  check(!/task_review\.xlsx/.test(out.text), '没有凭空捏造 task_review.xlsx 路径')
  process.env.DSH_PY_STUB_MODE = 'ok'
}

section('中文编码：这是本插件最要命的一处环境设置（用真 python 实测）')
{
  // 背景（在本机实测出来的）：本机 Windows 上 python 的 sys.stdout.encoding 默认是 **gbk**。
  // 脚本输出中文时字节是 GBK，而本插件按 utf-8 解码 —— 不设还是设错，中文全成乱码。
  // 处置：spawn 时把 PYTHONIOENCODING=utf-8 / PYTHONUTF8=1 塞进环境（见 index.js runPython）。
  const zh = '来料分析'
  const code = `import sys; sys.stdout.reconfigure(encoding=sys.stdout.encoding); print(${JSON.stringify(zh)})`

  /**
   * 找本机真 python：把假 python 所在目录从 PATH 里剔掉再找。
   * （不能直接 where python —— 假 python 就在 PATH 最前面，会把自己找出来。）
   */
  const findRealPython = () => {
    const parts = String(process.env.PATH || '').split(path.delimiter)
      .filter((p) => p && path.resolve(p) !== path.resolve(BIN))
    const stripped = parts.join(path.delimiter)
    const c = spawnSync('where', ['python'], { encoding: 'utf8', windowsHide: true, env: { ...process.env, PATH: stripped } })
    const first = (c.stdout || '').trim().split(/\r?\n/)[0]
    return first || 'python'
  }
  const realPython = findRealPython()
  console.log('  本机真 python：' + realPython)
  const isReal = path.resolve(realPython) !== path.resolve(STUB_EXE)
  check(isReal, '确实拿到了本机真 python（不是假的那个）', realPython)
  if (!isReal) {
    console.log('  ⚠️ 没找到本机真 python，这一段跳过（不假装验过）')
  } else {
    /** 用给定的环境变量跑一段真 python，把 stdout 按 utf-8 解出来。 */
    const runReal = (env) => {
      const r = spawnSync(realPython, ['-c', code], { encoding: 'buffer', windowsHide: true, env: { ...process.env, ...env } })
      return { out: r.stdout.toString('utf8'), hex: r.stdout.toString('hex'), err: (r.stderr || Buffer.alloc(0)).toString('utf8') }
    }

    const withEnv = runReal({ PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' })
    check(withEnv.out.includes(zh),
      `本插件用的环境（PYTHONIOENCODING=utf-8）下，中文按 utf-8 解出来是对的：${JSON.stringify(withEnv.out.trim())}`,
      withEnv.hex)

    const withoutEnv = runReal({ PYTHONIOENCODING: '', PYTHONUTF8: '' })
    if (!withoutEnv.out.includes(zh)) {
      ok(`反证成立：不设 PYTHONIOENCODING 时同一段中文会乱码（${JSON.stringify(withoutEnv.out.trim())}）—— 所以那行环境变量是**必须**的，不是装饰`)
    } else {
      ok(`本机上 python 默认就输出了 utf-8（${JSON.stringify(withoutEnv.out.trim())}），所以这条防护在这台机器上不是决定性的`)
    }
  }

  // 顺带确认 spawn 环境里确实带了这两个变量（写死在 index.js 里，改坏了这条会红）
  const src = fs.readFileSync(INDEX, 'utf8')
  check(/PYTHONIOENCODING:\s*'utf-8'/.test(src), "index.js 的 spawn 环境里写死了 PYTHONIOENCODING: 'utf-8'")
  check(/PYTHONUTF8:\s*'1'/.test(src), "index.js 的 spawn 环境里写死了 PYTHONUTF8: '1'")
  check(/windowsHide:\s*true/.test(src), 'spawn 带了 windowsHide: true（不闪黑框）')
  check(!/shell:\s*true/.test(src), 'spawn **没有**用 shell:true（免得中文路径被 cmd 的解释器啃掉）')
  check(/stdio:\s*\['ignore',\s*'pipe',\s*'pipe'\]/.test(src), 'stdio 收 stdout+stderr，且 stdin 不开')
}

section('超长输出被截断且注明')
{
  process.env.DSH_PY_STUB_MODE = 'big'
  const out = await callTool('table_image_status', { run_id: '20260910_001' })
  check(out.text.includes('已截断'), '长输出注明已截断')
  check(out.text.length < 24000, `回给模型的文本有上界（实际 ${out.text.length} 字符）`)
  process.env.DSH_PY_STUB_MODE = 'ok'
}

section('脚本不存在：如实说，不假装成功')
{
  const reg2 = []
  const ctx2 = {
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    tools: { register: (d) => { reg2.push(d); return () => {} } },
    effect: (fn) => fn(),
    inject: () => {},
  }
  mod.apply(ctx2, { workspace: WS_NO_SCRIPTS })
  const t2 = Object.fromEntries(reg2.map((t) => [t.name, t]))
  check(reg2.length === 9, `缺脚本的工作区里也照常注册 9 个工具（实际 ${reg2.length}）—— 注册与脚本存在与否解耦`)
  clearLog()
  const v = await t2.table_image_status.execute({ run_id: '20260910_001' }, execCtx)
  const text = t2.table_image_status.output.render({}, v)[0].text
  check(v.ok === false, 'ok=false')
  check(text.includes('脚本不存在'), '明确说「脚本不存在」')
  check(text.includes('不代表任务状态') || text.includes('不要猜结果'), '提示模型不要猜结果')
  check(argsLog().length === 0, '脚本不存在时根本没起 python 进程')
}

section('run_id 不存在：如实说，并把已有任务列出来')
{
  const out = await callTool('table_image_status', { run_id: '20200101_999' })
  check(out.value.note.includes('不存在'), `note 说明了指定目录不存在`)
  check(out.value.note.includes('20260910_001') && out.value.note.includes('20260910_002'), 'note 里列出了已有的任务')
  console.log('  note：' + out.value.note)
}

section('runs/ 下一个任务都没有：不硬跑')
{
  const wsEmpty = path.join(TMP, 'ws-empty-runs')
  fs.mkdirSync(path.join(wsEmpty, 'runs'), { recursive: true })
  const reg3 = []
  mod.apply({
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    tools: { register: (d) => { reg3.push(d); return () => {} } },
    effect: (fn) => fn(),
    inject: () => {},
  }, { workspace: wsEmpty })
  const t3 = Object.fromEntries(reg3.map((t) => [t.name, t]))
  clearLog()
  const v = await t3.table_image_status.execute({}, execCtx)
  const text = t3.table_image_status.output.render({}, v)[0].text
  check(v.ok === false, 'ok=false')
  check(text.includes('没有形如 YYYYMMDD_NNN 的任务目录'), '说清了为什么没跑')
  check(argsLog().length === 0, '没起 python 进程')
}

section('workspace.txt 只有空白时不被当成有效路径')
{
  const p = path.join(ROOT, 'workspace.txt')
  fs.writeFileSync(p, '\uFEFF   \r\n', 'utf8')
  let err = null
  try { mod.resolveWorkspace({}) } catch (e) { err = e }
  try { fs.unlinkSync(p) } catch { /* 忽略 */ }
  check(!!err, 'workspace.txt 只有空白时不被当成有效路径')
}

// ─────────────────────────────────────────────────────────────────────────────
// 清理 + 结论
// ─────────────────────────────────────────────────────────────────────────────
try { fs.rmSync(TMP, { recursive: true, force: true }) } catch { /* 忽略 */ }

section('结论')
const failed = results.filter((r) => !r.pass)
console.log(`  检查项：${results.length} 条，通过 ${results.length - failed.length} 条，失败 ${failed.length} 条`)
if (failed.length) {
  console.log('  失败项：')
  failed.forEach((f) => console.log(`    ❌ [${f.section}] ${f.msg}`))
}
console.log(failed.length === 0 ? '  ✅ 全部通过' : '  ⚠️ 有问题，见上面')
process.exit(failed.length === 0 ? 0 : 1)
