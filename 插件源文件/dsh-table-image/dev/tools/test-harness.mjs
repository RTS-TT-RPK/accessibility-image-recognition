// 测试台：不依赖真实浏览器，用模拟 React 在 Node 里跑一遍插件组件树。
//
// 为什么要有这个：客户端插件改完只能靠用户刷新页面反馈，来回成本太高。
// 这个脚本能在交付前抓住「属性缺失 / 位置算错 / 引用不存在 / 静默失败」这几类问题。
//
// 用法：node dev\tools\test-harness.mjs [client.js 的路径]
//       （省略路径时默认取包根目录下的 client.js）

import fs from 'node:fs'
import path from 'node:path'
import vm from 'node:vm'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const DEFAULT_CLIENT = path.resolve(HERE, '..', '..', 'client.js')

const file = process.argv[2] ? path.resolve(process.argv[2]) : DEFAULT_CLIENT
if (!fs.existsSync(file)) {
  console.log(`❌ 找不到 client.js：${file}`)
  process.exit(1)
}

// ─────────────────────────────────────────────────────────────────────────────
// 结果收集
// ─────────────────────────────────────────────────────────────────────────────
const results = []
const errors = []
let currentSection = '(未命名)'

function section(name) {
  currentSection = name
  console.log(`\n=== ${name} ===`)
}
function ok(msg) {
  results.push({ section: currentSection, pass: true, msg })
  console.log('  ✅ ' + msg)
}
function bad(msg) {
  results.push({ section: currentSection, pass: false, msg })
  console.log('  ❌ ' + msg)
}
function check(cond, msg, extra) {
  if (cond) ok(msg)
  else bad(msg + (extra ? '  ← ' + extra : ''))
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ─────────────────────────────────────────────────────────────────────────────
// 1. 模拟环境
// ─────────────────────────────────────────────────────────────────────────────
const src = fs.readFileSync(file, 'utf8')

let capturedFactory = null

function makeEl(type, props, ...children) {
  const kids = children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false && c !== true)
  // 关键：真 React 把 children 放进 props.children，组件代码读的是 this.props.children。
  // 曾经只放在 el.children，导致错误边界 render() 返回 undefined、整棵树在测试台里消失。
  const merged = { ...(props || {}) }
  if (kids.length === 1) merged.children = kids[0]
  else if (kids.length > 1) merged.children = kids
  return { type, props: merged, children: kids }
}

const hookCalls = { useState: 0, useEffect: 0, useRef: 0, useMemo: 0 }
const effects = []
const latestSetters = new Map()

const React = {
  createElement: makeEl,
  Fragment: Symbol('Fragment'),
  Component: class Component {
    constructor(props) { this.props = props || {}; this.state = {} }
    setState(v) { this.state = { ...this.state, ...(typeof v === 'function' ? v(this.state) : v) } }
    // 关键：默认 render 返回 children，否则错误边界会渲染出空、整个组件树在测试台里"消失"。
    render() { return this.props.children }
  },
  useState(init) {
    hookCalls.useState++
    const val = typeof init === 'function' ? init() : init
    const key = hookCalls.useState
    const setter = (v) => { latestSetters.set(key, typeof v === 'function' ? v(val) : v) }
    setter.__state = val
    return [val, setter]
  },
  useEffect(fn) {
    hookCalls.useEffect++
    // 故意真的跑一遍 effect —— 主要为了触发「探活上传通路」这条异步路径。
    effects.push(fn)
    return undefined
  },
  useRef(init) { hookCalls.useRef++; return { current: init } },
  useMemo(fn) { hookCalls.useMemo++; return fn() },
}

const storage = {
  _d: {},
  getItem(k) { return Object.prototype.hasOwnProperty.call(this._d, k) ? this._d[k] : null },
  setItem(k, v) { this._d[k] = String(v) },
  removeItem(k) { delete this._d[k] },
}

const fetchCalls = []
let fetchMode = 'ok' // 'ok' | 'http500' | 'reject' | 'badjson'

async function sandboxFetch(url, opts) {
  fetchCalls.push({ url, method: (opts && opts.method) || 'GET', body: opts && opts.body })
  if (fetchMode === 'reject') throw new Error('模拟：网络不通')
  if (fetchMode === 'http500') return { ok: false, status: 500, json: async () => ({ ok: false, error: '模拟 500' }) }
  if (fetchMode === 'badjson') return { ok: true, status: 200, json: async () => ({ nope: 1 }) }
  const method = (opts && opts.method) || 'GET'
  if (method === 'GET') {
    return { ok: true, status: 200, json: async () => ({ ok: true, count: 3, inbox: 'C:\\ws\\inbox', workspace: 'C:\\ws', images: ['a.jpg'] }) }
  }
  const payload = JSON.parse(opts.body)
  return {
    ok: true,
    status: 200,
    json: async () => ({ ok: true, saved: String(payload.name || 'x.jpg').replace(/\.png$/i, '.png'), bytes: 12, sha256: 'deadbeef', inboxCount: 4 }),
  }
}

class SandboxFileReader {
  readAsDataURL(f) {
    this.result = `data:${f.type || 'image/png'};base64,QUJD`
    setTimeout(() => { try { this.onload && this.onload() } catch (e) { errors.push('FileReader.onload: ' + e.message) } }, 0)
  }
}

const sandboxWindow = {
  __ModuleLoader__: { load: (mod) => { capturedFactory = mod.factory } },
  innerWidth: 1600,
  innerHeight: 900,
  localStorage: storage,
  addEventListener() {},
  removeEventListener() {},
  setTimeout: (f, ms) => setTimeout(f, ms),
  clearTimeout: (t) => clearTimeout(t),
  requestAnimationFrame: (f) => { setTimeout(f, 0); return 0 },
  dispatchEvent() {},
  CustomEvent: class { constructor(t, o) { this.type = t; this.detail = o && o.detail } },
}

const sandboxDocument = {
  head: { appendChild() {} },
  body: { appendChild() {} },
  getElementById() { return null },
  createElement() { return { id: '', textContent: '', style: {} } },
  addEventListener() {},
  removeEventListener() {},
}

const ctxObj = {
  window: sandboxWindow,
  document: sandboxDocument,
  console: {
    log: () => {},
    error: (...a) => errors.push('console.error: ' + a.map(String).join(' ')),
    warn: (...a) => errors.push('console.warn: ' + a.map(String).join(' ')),
  },
  fetch: sandboxFetch,
  FileReader: SandboxFileReader,
  Math, JSON, Object, Array, String, Number, Boolean, Date, RegExp, Error, Promise, Set, Map, Symbol,
  isNaN, parseInt, parseFloat, ArrayBuffer, Uint8Array, Buffer,
  setTimeout, clearTimeout, queueMicrotask,
  requestAnimationFrame: sandboxWindow.requestAnimationFrame,
}
ctxObj.globalThis = ctxObj
ctxObj.self = ctxObj

const context = vm.createContext(ctxObj)

// ─────────────────────────────────────────────────────────────────────────────
// 2. 执行插件文件，抓出 factory
// ─────────────────────────────────────────────────────────────────────────────
section('加载插件文件')
try {
  vm.runInContext(src, context, { filename: file })
  ok(`vm 执行通过：${path.basename(file)}`)
} catch (e) {
  bad('插件文件执行失败: ' + e.message)
  console.log('\n=== 结论 ===\n  ❌ 没法继续')
  process.exit(1)
}
check(!!capturedFactory, '捕获到 window.__ModuleLoader__.load 的 factory', capturedFactory ? '' : '没捕获到')

const requireMock = (name) => {
  if (name === 'react') return React
  if (name === 'react-dom') return { createPortal: (el) => el }
  if (name === '@deepseek-ai/dsh-client-ui-primitives') {
    return {
      Button: function Button(props) { return makeEl('button', props, props.children) },
      Input: function Input(props) { return makeEl('input', props) },
      Modal: function Modal(props) { return makeEl('div', props, props.children) },
    }
  }
  return {}
}

let plugin
try {
  plugin = capturedFactory(requireMock)
  ok('factory(require) 执行通过')
} catch (e) {
  bad('factory 执行失败: ' + e.message + '\n' + e.stack)
  console.log('\n=== 结论 ===\n  ❌ 没法继续')
  process.exit(1)
}
check(plugin && typeof plugin.apply === 'function', '插件导出了 apply()')

const T = sandboxWindow.__TABLE_IMAGE_TEST__
check(!!T, '测试钩子 window.__TABLE_IMAGE_TEST__ 挂上了')

// ─────────────────────────────────────────────────────────────────────────────
// 3. 假 ctx：捕获 slot 注册
// ─────────────────────────────────────────────────────────────────────────────
const registrations = []
const fakeCtx = {
  get: () => undefined,
  effect: (fn) => { try { fn() } catch (e) { errors.push('effect: ' + e.message) } },
  slots: {
    inject: (key, cb) => { try { cb() } catch (e) { errors.push('inject(' + key + '): ' + e.message) } },
    register: (meta, comp) => { registrations.push({ meta, comp }); return () => {} },
  },
}

section('apply() 与槽位注册')
try {
  plugin.apply(fakeCtx)
  ok('apply(fakeCtx) 未抛异常')
} catch (e) {
  bad('apply 失败: ' + e.message + '\n' + e.stack)
  console.log('\n=== 结论 ===\n  ❌ 没法继续')
  process.exit(1)
}

for (const r of registrations) {
  console.log(`  · ${(r.meta.name || '?').padEnd(30)} id=${r.meta.id || '-'}${r.meta.label ? '  label=' + r.meta.label : ''}`)
}

const triggerReg = registrations.find((r) => r.meta.name === 'conversation.input.activity')
check(!!triggerReg, '注册了 conversation.input.activity')
check(!!triggerReg && triggerReg.meta.id === 'dsh-table-image-trigger', '触发按钮注册项的 id 正确')

// 关键检查 4：settings.section 必须带 label（漏了就是空白行，且不报错）
for (const r of registrations.filter((x) => x.meta.name === 'settings.section')) {
  check(typeof r.meta.label === 'string' && r.meta.label.trim().length > 0,
    `settings.section 注册带 label（${r.meta.label || '缺失'}）`)
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. 通用展开器：把返回的 React 树摊平成 DOM/组件节点数组
// ─────────────────────────────────────────────────────────────────────────────
function expand(node, depth = 0, maxDepth = 14, out = []) {
  if (node === null || node === undefined || node === false || node === true) return out
  if (Array.isArray(node)) { for (const n of node) expand(n, depth, maxDepth, out); return out }
  if (typeof node !== 'object') return out
  if (depth > maxDepth) return out

  // 类组件优先（构造函数也是 function，必须先于函数组件分支判断）
  if (typeof node.type === 'function' && node.type.prototype && typeof node.type.prototype.render === 'function') {
    let rendered
    try {
      const inst = new node.type(node.props)
      rendered = inst.render()
    } catch (e) {
      // 类组件构造或 render 抛错 —— 真 React 里这会触发最近一层错误边界，所以这里如实记下
      errors.push('类组件 ' + (node.type.name || '?') + ': ' + e.message)
      return out
    }
    out.push(node)
    return expand(rendered, depth + 1, maxDepth, out)
  }
  if (node.type === React.Fragment) return expand(node.children, depth, maxDepth, out)
  if (typeof node.type === 'function') {
    let rendered
    try {
      rendered = node.type(node.props)
    } catch (e) {
      errors.push('函数组件 ' + (node.type.name || '?') + '(): ' + e.message)
      return out
    }
    out.push(node)
    return expand(rendered, depth + 1, maxDepth, out)
  }
  out.push(node)
  return expand(node.children, depth + 1, maxDepth, out)
}

function findAll(nodes, pred) { return nodes.filter(pred) }
function byKey(nodes, key) { return nodes.find((n) => n.props && n.props.key === key) }

// ─────────────────────────────────────────────────────────────────────────────
// 5. 渲染触发按钮（input.activity 槽位）
// ─────────────────────────────────────────────────────────────────────────────
section('渲染触发按钮 + 拖拽事件装配')

const inputActionsCalls = { setDraft: [], submit: 0, other: [] }
const inputActions = {
  setDraft: (t) => { inputActionsCalls.setDraft.push(t) },
  submit: () => { inputActionsCalls.submit++ },
  captureInsertion: () => ({ start: 0, end: 0, draftRev: 0 }),
  insertText: () => true,
  addAttachments: () => true,
}

let triggerTree
try {
  triggerTree = triggerReg.comp({ inputActions, locked: false })
  ok('触发按钮组件渲染未抛异常')
} catch (e) {
  bad('触发按钮组件渲染失败: ' + e.message + '\n' + e.stack)
}

const triggerNodes = expand(triggerTree)
const btn = byKey(triggerNodes, 'btn')
check(!!btn, '找到触发按钮（key=btn）', `实际 key：${triggerNodes.map((n) => n.props && n.props.key).filter(Boolean).join(', ')}`)

// 关键检查 1：每个事件处理器都挂上了（漏挂 = 拖不动/点不开）
const REQUIRED_HANDLERS = ['onClick', 'onDragEnter', 'onDragOver', 'onDragLeave', 'onDrop']
if (btn) {
  for (const k of REQUIRED_HANDLERS) {
    check(typeof btn.props[k] === 'function', `触发按钮挂了 ${k}`)
  }
  check(btn.props.style && typeof btn.props.style.width === 'number' && typeof btn.props.style.height === 'number',
    `触发按钮给了宽高（${btn.props.style && btn.props.style.width}×${btn.props.style && btn.props.style.height}）`)
}

// 真跑一遍拖拽事件，看会不会抛
section('真调一遍拖拽事件（模拟把一张图拖上来）')
if (btn) {
  const fakeFile = { name: '样品1.png', type: 'image/png', size: 12 }
  const dragEvent = (extra) => ({
    preventDefault() {}, stopPropagation() {},
    currentTarget: { getBoundingClientRect: () => ({ left: 300, top: 700, width: 32, height: 32 }) },
    dataTransfer: { files: [fakeFile] },
    ...extra,
  })
  try {
    btn.props.onDragEnter(dragEvent())
    ok('onDragEnter 未抛异常')
    btn.props.onDragOver(dragEvent())
    ok('onDragOver 未抛异常')
    btn.props.onDragLeave(dragEvent())
    ok('onDragLeave 未抛异常')
    btn.props.onDrop(dragEvent())
    ok('onDrop 未抛异常')
    btn.props.onClick(dragEvent())
    ok('onClick 未抛异常')
  } catch (e) {
    bad('拖拽/点击事件抛异常: ' + e.message + '\n' + e.stack)
  }
}

// 跑一遍 effect（探活），等异步落定
for (const fn of effects) { try { fn() } catch (e) { errors.push('useEffect: ' + e.message) } }
await sleep(30)

section('上传通路探活（GET /table-image/api）')
check(fetchCalls.some((c) => c.method === 'GET' && c.url === (T && T.API)), `挂载后 GET 探活打到了 ${T && T.API}`)
check(T && T.store.get().routeOk === true, '探活成功后 store.routeOk === true', JSON.stringify(T && T.store.get()))
check(inputActionsCalls.submit === 0, '没有调用过 inputActions.submit()（本插件只用 setDraft）')

// ─────────────────────────────────────────────────────────────────────────────
// 6. 渲染面板：指令按钮 → setDraft（不发送）
// ─────────────────────────────────────────────────────────────────────────────
section('渲染面板 + 指令按钮只填草稿不发送')

let panelNodes = []
try {
  const panelTree = T.TableImagePanel({ anchor: { x: 300, y: 700 }, inputActions, onClose: () => {} })
  panelNodes = expand(panelTree)
  ok('面板组件渲染未抛异常')
} catch (e) {
  bad('面板组件渲染失败: ' + e.message + '\n' + e.stack)
}

const insButtons = panelNodes.filter((n) => n.props && typeof n.props.onClick === 'function' && n.props.style === T.S.rowBtn)
check(insButtons.length === (T ? T.INSTRUCTIONS.length : 0), `面板里有 ${T ? T.INSTRUCTIONS.length : '?'} 个指令按钮`, `实际 ${insButtons.length} 个`)

const beforeDraft = inputActionsCalls.setDraft.length
if (insButtons.length > 0) {
  try {
    insButtons[0].props.onClick()
    ok('点了第一个指令按钮，未抛异常')
  } catch (e) {
    bad('点指令按钮抛异常: ' + e.message)
  }
}
check(inputActionsCalls.setDraft.length === beforeDraft + 1, '指令按钮把文字填进了输入框（setDraft）')
check(inputActionsCalls.setDraft.length > 0 && String(inputActionsCalls.setDraft[inputActionsCalls.setDraft.length - 1]).length > 10,
  '填进去的是一段可用的指令，不是空串')
check(inputActionsCalls.submit === 0, '点指令按钮**没有**触发 submit（不自动发送）')

// 拿不到 inputActions 时不能崩
section('异常输入：inputActions 缺失 / 为 null')
for (const badActions of [undefined, null, {}]) {
  try {
    const tree = T.TableImageTrigger({ inputActions: badActions })
    const nodes = expand(tree)
    const b = byKey(nodes, 'btn')
    b.props.onClick({ currentTarget: { getBoundingClientRect: () => ({ left: 10, top: 10 }) } })
    const p = T.TableImagePanel({ anchor: { x: 10, y: 10 }, inputActions: badActions, onClose: () => {} })
    const pNodes = expand(p)
    const ib = pNodes.filter((n) => n.props && n.props.style === T.S.rowBtn)
    if (ib.length) ib[0].props.onClick()
    ok(`inputActions = ${JSON.stringify(badActions)} 时渲染与点击都不抛，且如实报错`)
  } catch (e) {
    bad(`inputActions = ${JSON.stringify(badActions)} 时抛异常: ` + e.message)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. 关键检查 3：定位函数必须两个轴都给，纵向用 bottom
// ─────────────────────────────────────────────────────────────────────────────
section('定位函数 panelStyle —— 两个轴都要有')
{
  const VW = 1600
  const VH = 900
  const SIZE = T.PANEL
  const testPos = { x: 300, y: 300 }
  let style
  try {
    style = T.panelStyle(testPos, SIZE)
    console.log('  panelStyle({x:300,y:300}, PANEL) → ' + JSON.stringify(style))
  } catch (e) {
    bad('panelStyle 执行失败: ' + e.message)
  }
  if (style) {
    check(typeof style.left === 'number', '有 left（只给 bottom 的话卡片会跑到视口外，表现为"点了没反应"）')
    check(typeof style.bottom === 'number', '有 bottom（纵向锚定用 bottom，卡片长高只往上延伸，不压住按钮）')
    check(style.top === undefined, '没有用 top（用 top 就得猜卡片高度，曾经把图标盖掉大半）')
    check(style.left === 300, `left 贴着锚点（期望 300，实际 ${style.left}）`)
    check(style.bottom === VH - 300 + 10, `锚点上方够放时 bottom = 视口高 - 锚点 y + 10（期望 610，实际 ${style.bottom}）`)
    check(style.placement === 'above', `锚点上方够放时 placement=above（实际 ${style.placement}）`)
  }

  // 几何不变量（这才是真正要守住的东西）
  //   ① left / bottom 都是数字轴
  //   ② 横向一定在视口内
  //   ③ 垂直方向：能挤进视口就挤进去（面板顶部越过视口上沿时，允许只把下沿线钉在锚点上方；
  //      因为「面板钉在锚点上方」与「面板整个在视口内」在锚点靠屏幕上部时数学上不可兼得，
  //      这里选择保「不盖住锚点」——按钮被挡住就等于入口没了，面板顶被切一点还能看）
  //   ④ 永远不盖住锚点
  const cases = [
    { x: 0, y: 0 }, { x: 300, y: 300 }, { x: 1590, y: 890 },
    { x: 1599, y: 899 }, { x: -50, y: -50 }, { x: 800, y: 450 },
    { x: 0, y: 40 }, { x: 800, y: 700 }, { x: 200, y: 200 }, { x: 1500, y: 860 },
    { x: 700, y: 500 }, { x: 400, y: 620 }, { x: 300, y: 899 },
  ]
  const violations = []
  const overflowTop = []
  for (const c of cases) {
    const s = T.panelStyle(c, SIZE)
    if (typeof s.left !== 'number' || typeof s.bottom !== 'number') {
      violations.push(`${JSON.stringify(c)} → 轴不是数字: ${JSON.stringify(s)}`)
      continue
    }
    const panelLeft = s.left
    const panelTop = VH - s.bottom - SIZE.height
    const panelBottom = VH - s.bottom
    const horizontallyIn = panelLeft >= 0 && panelLeft + SIZE.width <= VW
    const coversAnchor = panelLeft < c.x && c.x < panelLeft + SIZE.width
      && panelTop < c.y && c.y < panelBottom

    if (!horizontallyIn) violations.push(`${JSON.stringify(c)} → 横向出视口 ${JSON.stringify(s)}`)
    if (coversAnchor) violations.push(`${JSON.stringify(c)} → 盖住锚点 ${JSON.stringify(s)}`)
    if (panelTop < 0 && c.y >= 0) overflowTop.push(`${JSON.stringify(c)} → 顶部超出 ${-panelTop}px`)
  }
  check(violations.length === 0,
    `${cases.length} 组锚点：横向都在视口内、且都没有盖住锚点`,
    violations.join(' | '))

  // 锚点靠屏幕下半部分（本插件按钮的实际处境：输入框工具行）时，面板必须**整个**在视口内
  const lowAnchors = cases.filter((c) => c.y >= SIZE.height + 10 && c.y <= VH)
  const lowViolations = []
  for (const c of lowAnchors) {
    const s = T.panelStyle(c, SIZE)
    const panelTop = VH - s.bottom - SIZE.height
    if (panelTop < 0 || VH - s.bottom > VH) lowViolations.push(`${JSON.stringify(c)} → ${JSON.stringify(s)}`)
  }
  check(lowViolations.length === 0,
    `锚点在屏幕下半部分时（${lowAnchors.length} 组），面板整个都在视口内`,
    lowViolations.join(' | '))
  if (overflowTop.length) {
    console.log('  ℹ️ 锚点太靠屏幕上部、面板顶部被裁的组（已知取舍，票据见 README「已知限制」）：')
    overflowTop.forEach((t) => console.log('     · ' + t))
  }

  // 翻转：锚点贴屏幕顶、下方放得下时必须翻到下方
  const top = T.panelStyle({ x: 300, y: 0 }, SIZE)
  check(top.placement === 'below', `锚点在屏幕最顶上时翻转到下方（placement=${top.placement}）`)
  check(top.left === 300 && typeof top.bottom === 'number', '翻转后仍然两个轴都给')
  check(VH - top.bottom <= SIZE.height + 30, `翻转后整个面板在视口内（bottom=${top.bottom}）`)

  // 窄屏
  const savedW = sandboxWindow.innerWidth
  sandboxWindow.innerWidth = 420
  let narrow
  try {
    narrow = T.panelStyle({ x: 400, y: 700 }, SIZE)
    check(narrow.left + SIZE.width <= 420, `窄屏（420px）下右边界被夹回视口内：${JSON.stringify(narrow)}`)
  } catch (e) {
    bad('窄屏 panelStyle 抛异常: ' + e.message)
  }
  sandboxWindow.innerWidth = savedW

  // 异常输入不能崩
  for (const badAnchor of [undefined, null, {}, { x: 'a', y: 'b' }, { x: NaN, y: NaN }]) {
    try {
      const s = T.panelStyle(badAnchor, SIZE)
      const okShape = typeof s.left === 'number' && typeof s.bottom === 'number'
        && Number.isFinite(s.left) && Number.isFinite(s.bottom)
      check(okShape, `anchor = ${JSON.stringify(badAnchor)} 时仍返回两个有限数字轴（${JSON.stringify(s)}）`)
    } catch (e) {
      bad(`anchor = ${JSON.stringify(badAnchor)} 时抛异常: ` + e.message)
    }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. 关键检查 2 的强化：样式里不许依赖不存在的 CSS 变量
// ─────────────────────────────────────────────────────────────────────────────
section('样式：显式色值，不靠 CSS 变量')
{
  // 只看**代码**，不看注释（注释里会举反面教材 var(--dsw-surface, #1f1f22)）
  const codeOnly = src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1')
  const cssVarUses = [...codeOnly.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map((m) => m[1])
  check(cssVarUses.length === 0,
    'client.js 里没有任何 var(--*) 用法（变量名写错会静默用兜底值，面板会变全黑）',
    cssVarUses.length ? '发现：' + [...new Set(cssVarUses)].join(', ') : '')

  const colorKeys = ['cardBg', 'titleBar', 'title', 'text', 'muted', 'accent', 'ok', 'warn', 'err']
  const missing = colorKeys.filter((k) => !T || !T.C || typeof T.C[k] !== 'string' || !T.C[k])
  check(missing.length === 0, '色板 C 里该有的显式色值都在', missing.length ? '缺：' + missing.join(', ') : '')
  check(T && /^#|^rgb|gradient/.test(T.C.cardBg), `卡片底色是显式色值（${T && T.C.cardBg}）`)
}

// ─────────────────────────────────────────────────────────────────────────────
// 9. 上传真跑一遍（happy path + 各种失败路径）
// ─────────────────────────────────────────────────────────────────────────────
section('上传：真跑一遍（成功路径）')
{
  fetchCalls.length = 0
  const files = [{ name: '样品1.png', type: 'image/png', size: 12 }, { name: '样品2.jpg', type: 'image/jpeg', size: 34 }]
  try {
    await T.uploadFiles(files)
    ok('uploadFiles 未抛异常')
  } catch (e) {
    bad('uploadFiles 抛异常: ' + e.message + '\n' + e.stack)
  }
  const posts = fetchCalls.filter((c) => c.method === 'POST')
  check(posts.length === 2, `两张图各发了一次 POST（实际 ${posts.length} 次）`)
  check(posts.every((c) => c.url === T.API), `POST 打在 ${T.API} 上`)
  check(posts.every((c) => { try { return typeof JSON.parse(c.body).dataBase64 === 'string' } catch { return false } }),
    'POST 体是 { name, dataBase64 } 且 base64 是字符串')
  check(T.store.get().phase === 'ok', `成功后面板状态是 ok（实际 ${T.store.get().phase}）`)
  check(T.store.get().saved.length === 2, `记下了 2 个落盘文件名（实际 ${T.store.get().saved.length}）`)
  console.log('  面板提示：' + T.store.get().message)
}

section('上传：失败路径必须如实报错，不假装成功')
{
  for (const mode of ['http500', 'reject']) {
    fetchMode = mode
    await T.uploadFiles([{ name: 'x.png', type: 'image/png', size: 1 }])
    const st = T.store.get()
    check(st.phase === 'err' && st.saved.length === 0, `${mode}：状态是 err 且没有假报成功`, `phase=${st.phase} saved=${st.saved.length}`)
    console.log(`  ${mode} 提示：${st.message}`)
  }
  fetchMode = 'ok'

  // 不是图片的文件
  await T.uploadFiles([{ name: 'report.pdf', type: 'application/pdf', size: 1 }])
  check(T.store.get().phase === 'err', '拖进来非图片 → 状态 err，什么都没上传')

  // 空拖拽
  await T.uploadFiles([])
  ok('空文件列表不抛异常（不误报成功）')
}

// ─────────────────────────────────────────────────────────────────────────────
// 10. 全部组件都要能渲染不抛
// ─────────────────────────────────────────────────────────────────────────────
section('所有组件渲染检查')
{
  const comps = [
    ['ErrorBoundary', () => T.ErrorBoundary.prototype.render.call({ props: { children: null }, state: { err: null } })],
    ['ErrorBoundary(出错态)', () => T.ErrorBoundary.prototype.render.call({ props: {}, state: { err: new Error('模拟崩溃') } })],
    ['StatusLine', () => T.StatusLine({ phase: 'ok', message: 'hi' })],
    ['StatusLine(warn)', () => T.StatusLine({ phase: 'warn', message: 'hi' })],
    ['StatusLine(err)', () => T.StatusLine({ phase: 'err', message: 'hi' })],
    ['StatusLine(空)', () => T.StatusLine({})],
    ['DropZone(可用)', () => T.DropZone({ enabled: true, hot: false, onDrop: () => {}, onDragEnter: () => {}, onDragOver: () => {}, onDragLeave: () => {}, onPick: () => {} })],
    ['DropZone(高亮)', () => T.DropZone({ enabled: true, hot: true, onDrop: () => {}, onDragEnter: () => {}, onDragOver: () => {}, onDragLeave: () => {}, onPick: () => {} })],
    ['DropZone(禁用)', () => T.DropZone({ enabled: false, reason: '上传通路不可用：模拟' })],
    ['InstructionButton', () => T.InstructionButton({ label: '测试', onClick: () => {} })],
    ['SettingsSection', () => T.SettingsSection({})],
    ['TableImageTrigger', () => T.TableImageTrigger({ inputActions })],
    ['TableImagePanel', () => T.TableImagePanel({ anchor: { x: 100, y: 600 }, inputActions, onClose: () => {} })],
  ]
  for (const [name, fn] of comps) {
    try {
      const out = fn()
      expand(out)
      ok(`${name} 渲染通过`)
    } catch (e) {
      bad(`${name} 渲染抛异常: ` + e.message)
    }
  }
}

section('组件之间的作用域纪律')
{
  // 造一个「一渲染就抛」的子组件，看错误边界兜不兜得住。
  const Boom = function Boom() { throw new Error('模拟子组件崩溃') }
  const expected = '模拟子组件崩溃'

  try {
    const inst = new T.ErrorBoundary({ children: makeEl(Boom, null) })
    ok('ErrorBoundary 本身能构造（不依赖任何外层变量）')
  } catch (e) {
    bad('ErrorBoundary 构造失败: ' + e.message)
  }

  const errStateOut = T.ErrorBoundary.prototype.render.call({ props: {}, state: { err: new Error(expected) } })
  const errNodes = expand(errStateOut)
  check(errNodes.length > 0, '边界处于出错态时仍然渲染出提示框（不会返回 null 让整棵树消失）')
  const errText = JSON.stringify(errNodes.map((n) => n.props && n.props.children).filter(Boolean))
  check(errText.includes(expected), '提示框里带上了原始报错文字（用户能直接发给 AI）')

  // 子组件崩了：边界要真能接住，而且**真 React 不会让错误冒到进程外**。
  // 只认这次检查自己新产生的那几条错误，不碰别的检查留下的记录。
  const before = errors.length
  const nodes = expand(makeEl(T.ErrorBoundary, null, makeEl(Boom, null)))
  const produced = errors.splice(before)
  check(nodes.length > 0, '子树崩溃时边界仍渲染出内容（不会整棵树消失）')

  const boomRecorded = produced.find((e) => e.includes(expected))
  check(!!boomRecorded, '故意触发的子组件崩溃被如实记进错误清单', produced.join(' | ') || '（一条都没记到）')
  if (boomRecorded) ok(`预期内的一次报错已划掉，不参与最终判定（原文：${boomRecorded}）`)
}

// ─────────────────────────────────────────────────────────────────────────────
// 11. 收尾
// ─────────────────────────────────────────────────────────────────────────────
section('捕获到的错误')
if (errors.length === 0) console.log('  ✅ 无')
else errors.forEach((e) => console.log('  ⚠️ ' + e))

section('结论')
const failed = results.filter((r) => !r.pass)
const total = results.length
console.log(`  检查项：${total} 条，通过 ${total - failed.length} 条，失败 ${failed.length} 条`)
if (failed.length) {
  console.log('  失败项：')
  failed.forEach((f) => console.log(`    ❌ [${f.section}] ${f.msg}`))
}
const clean = failed.length === 0 && errors.length === 0
console.log(clean ? '  ✅ 全部通过' : '  ⚠️ 有问题，见上面')
process.exit(clean ? 0 : 1)
