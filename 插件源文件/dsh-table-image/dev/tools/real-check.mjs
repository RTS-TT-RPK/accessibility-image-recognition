// 真机联调（不重启 DSH 的前提下能做的最后一步）：
//   在 **web profile 的 node_modules 里** 导入真实的 dsh-table-image，
//   用**真的 @deepseek-ai/dsh-tools**（不是替身）、**真的 python**、**真的工作区**跑一遍工具。
//
// 为什么要这么绕：本插件是装在 ~/.dsh/profiles/web/node_modules/ 下的 junction；
// 只有从那个目录出发解析，import 'dsh-table-image' 才能拿到真包，
// 而且 @deepseek-ai/dsh-tools 也才能按 profile 的依赖图解析到 F:\deepseek-harness 里那份。
// 从本仓库目录里跑是解析不到的（那正是 test-host.mjs 需要替身的原因）。
//
// 用法：
//   node "C:\Users\86173\.dsh\profiles\web\node_modules\dsh-table-image\dev\tools\real-check.mjs"
// 或从本包目录：
//   node dev/tools/real-check.mjs      （脚本自己会 cd 到 profile 目录）

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'

const PROFILE = 'C:\\Users\\86173\\.dsh\\profiles\\web'
const WORKSPACE = 'C:\\Users\\86173\\Desktop\\图像识别长期工程'

if (!fs.existsSync(PROFILE)) {
  console.log('❌ 找不到 profile 目录：' + PROFILE)
  process.exit(1)
}
process.chdir(PROFILE)

console.log('='.repeat(62))
console.log('  真机联调：真包 + 真 dsh-tools + 真 python + 真工作区')
console.log('='.repeat(62))
console.log('  cwd       : ' + process.cwd())
console.log('  工作区    : ' + WORKSPACE)

let failed = 0
const check = (label, ok, detail) => {
  console.log(`  ${ok ? '✅' : '❌'} ${label}${detail ? '  <- ' + detail : ''}`)
  if (!ok) failed += 1
}

// ── 1. 解析 @deepseek-ai/dsh-tools 到底落在哪
console.log('\n=== 1. @deepseek-ai/dsh-tools 解析到哪一份 ===')
let resolved = ''
try {
  resolved = (await import('node:module')).createRequire(path.join(PROFILE, 'noop.js')).resolve('@deepseek-ai/dsh-tools')
  console.log('  ' + resolved)
  // 判据：真的 dsh-tools 入口、且不是我们测试台那个合成 url（dsh-tools-stub:...）
  const okResolved = fs.existsSync(resolved)
    && resolved.includes('tools')
    && !resolved.startsWith('dsh-tools-stub:')
  check('解析到了真实的 dsh-tools 文件（不是替身）', okResolved, resolved)
} catch (e) {
  check('能解析 @deepseek-ai/dsh-tools', false, String(e.message).split('\n')[0])
}

// ── 2. 从 profile 导入真包
console.log('\n=== 2. 导入真实的 dsh-table-image ===')
const { createRequire } = await import('node:module')
const { pathToFileURL } = await import('node:url')

/**
 * 按 **profile 的解析上下文** 找到真包并导入。
 *
 * 注意：`import 'dsh-table-image'` 这样按裸名字导入是**不行的** ——
 * profile 的 node_modules 里只有 junction，没有给 ESM 用的 package.json 映射，
 * 裸名字解析会直接失败。所以这里用 createRequire 从 profile 出发解析包入口，
 * 再用绝对路径导入。解析走的仍然是 profile 的依赖图（这正是要验的东西）。
 */
let pkgEntry = ''
try {
  const req = createRequire(path.join(PROFILE, 'noop.js'))
  pkgEntry = req.resolve('dsh-table-image')
  console.log('  解析到：' + pkgEntry)
} catch (e) {
  check('从 profile 解析 dsh-table-image', false, String(e.message).split('\n')[0])
  console.log('\n结论：❌ 装不上，见上面的报错')
  process.exit(1)
}

let mod
try {
  mod = await import(pathToFileURL(pkgEntry).href)
  check('导入真实包成功（走的是 profile 里的 junction）', true)
  check('导出 apply()', typeof mod.apply === 'function')
  check('inject = ["tools"]', JSON.stringify(mod.inject) === '["tools"]', JSON.stringify(mod.inject))
} catch (e) {
  check('导入真实包成功', false, String(e.code || '') + ' ' + String(e.message).split('\n')[0])
  console.log('\n结论：❌ 装不上，见上面的报错')
  process.exit(1)
}

// ── 3. 用真 dsh-tools 跑一遍 apply
console.log('\n=== 3. apply() + 真 dsh-tools 注册 ===')
const tools = new Map()
const routes = []
const effects = []
const ctx = {
  logger: { info: (m) => console.log('  [log] ' + m), warn: () => {}, error: () => {} },
  tools: { register: (def) => { tools.set(def.name, def); return () => {} } },
  effect: (fn, label) => { const d = fn(); effects.push({ label, d }); return () => { if (typeof d === 'function') d() } },
  inject: (deps, cb) => cb({
    webServer: { register: (r) => { routes.push(r); return () => {} } },
    effect: (fn, label) => { const d = fn(); effects.push({ label, d }); return () => { if (typeof d === 'function') d() } },
  }),
}
try {
  mod.apply(ctx, { workspace: WORKSPACE })
  check('apply() 没抛异常（真 dsh-tools 的 defineTool 接受了这 9 份定义）', true)
} catch (e) {
  check('apply() 没抛异常', false, String(e.message).split('\n')[0])
}
check('注册了 9 个工具', tools.size === 9, `实际 ${tools.size}：${[...tools.keys()].join(', ')}`)
check('挂了上传端点 /table-image/api', routes.length === 1 && routes[0].path === '/table-image/api',
  routes.map((r) => r.path).join(', '))

// ── 4. 真 python 跑一次 status
console.log('\n=== 4. 真跑一次 table_image_status（真 python + 真工作区）===')
const st = tools.get('table_image_status')
if (!st) {
  check('找到 table_image_status', false)
} else {
  try {
    const value = await st.execute({}, { signal: undefined })
    const text = st.output.render({}, value)[0].text
    check('跑通了，拿到了退出码', typeof value.exit_code === 'number', `exit_code=${value.exit_code}`)
    check('退出码 0', value.exit_code === 0, `exit_code=${value.exit_code}`)
    check('自动挑了最新任务', /20260910_\d+/.test(value.run_id), value.run_id)
    check('输出里带了脚本的原话', text.includes('──── stdout ────'))
    check('中文没有乱码（PYTHONIOENCODING 生效）', !text.includes('\uFFFD') && /任务|阶段|进度|识别/.test(text))
    console.log('\n  ── 模型实际会看到的内容（前 34 行）──')
    text.split('\n').slice(0, 34).forEach((l) => console.log('  │ ' + l))
  } catch (e) {
    check('table_image_status 真跑成功', false, String(e.message).split('\n')[0])
  }
}

// ── 5. 真 python 跑一次 next
console.log('\n=== 5. 真跑一次 table_image_next ===')
const nx = tools.get('table_image_next')
if (nx) {
  try {
    const runId = (await tools.get('table_image_status').execute({}, {})).run_id
    const value = await nx.execute({ run_id: runId }, {})
    const text = nx.output.render({}, value)[0].text
    console.log('\n  ── 前 20 行 ──')
    text.split('\n').slice(0, 20).forEach((l) => console.log('  │ ' + l))

    // 两种结果都算"通"：
    //   · 有 source/names.json 台账 → 退出码 0，给出下一张图
    //   · 这份 runs/ 是测试/演示留下的、没有台账 → 脚本退出码 2 并说清"缺台账"
    // 关键是**退出的原因必须被原样带回来**，而不是壳子自己编一个说法。
    const hasLedger = fs.existsSync(path.join(WORKSPACE, 'runs', runId, 'source', 'names.json'))
    if (hasLedger) {
      check('有台账：--next 跑通（退出码 0）', value.exit_code === 0, `exit_code=${value.exit_code}`)
      check('输出里给了下一张图的绝对路径', /绝对路径/.test(text))
    } else {
      check('没有台账：脚本退出码 2，且说清了原因', value.exit_code === 2 && text.includes('台账'),
        `exit_code=${value.exit_code}`)
      check('脚本的原话被原样带回来了（壳子没替它编说法）', text.includes('[错误] 这个任务没有'))
      check('宿主给了"退出码非 0"的提醒', text.includes('退出码非 0'))
    }
    check('中文没有乱码', !text.includes('\uFFFD'))
  } catch (e) {
    check('table_image_next 真跑成功', false, String(e.message).split('\n')[0])
  }
}

// ── 6. 上传端点真发一次请求
//     注意：**在一份临时工作区里跑**，绝不往用户真实的 inbox/ 里写东西。
//     用真包、真路由 handler、真 http 服务，只有 cwd/工作区是临时的。
console.log('\n=== 6. 上传端点：真起 http 服务，真发 GET / POST（在临时工作区里）===')

const TMPWS = fs.mkdtempSync(path.join((await import('node:os')).tmpdir(), 'dsh-ti-real-'))
fs.mkdirSync(path.join(TMPWS, 'inbox'), { recursive: true })
fs.mkdirSync(path.join(TMPWS, 'runs', '20260910_001'), { recursive: true })
fs.writeFileSync(path.join(TMPWS, 'inbox', '用户原有的图.png'), Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]))
console.log('  临时工作区：' + TMPWS)

const tmpTools = new Map()
const tmpRoutes = []
try {
  mod.apply({
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    tools: { register: (def) => { tmpTools.set(def.name, def); return () => {} } },
    effect: (fn) => { const d = fn(); return () => { if (typeof d === 'function') d() } },
    inject: (deps, cb) => cb({
      webServer: { register: (r) => { tmpRoutes.push(r); return () => {} } },
      effect: (fn) => { const d = fn(); return () => { if (typeof d === 'function') d() } },
    }),
  }, { workspace: TMPWS })
  check('在临时工作区上 apply() 成功', tmpTools.size === 9 && tmpRoutes.length === 1, `tools=${tmpTools.size} routes=${tmpRoutes.length}`)
} catch (e) {
  check('在临时工作区上 apply() 成功', false, String(e.message).split('\n')[0])
}

if (tmpRoutes.length === 1) {
  const http = await import('node:http')
  const server = http.createServer((req, res) => tmpRoutes[0].handler(req, res))
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  const port = server.address().port
  const base = `http://127.0.0.1:${port}/table-image/api`
  console.log('  临时服务：' + base)

  try {
    const getRes = await fetch(base)
    const getJson = await getRes.json()
    check('GET 200 且 ok=true', getRes.status === 200 && getJson.ok === true, `status=${getRes.status}`)
    check('GET 报的是临时工作区', getJson.workspace === TMPWS, getJson.workspace)
    check('GET 只数图片', getJson.count === 1, `count=${getJson.count}`)

    // 真发一张图（1x1 PNG）
    const png1x1 = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==',
      'base64',
    )
    const beforeBytes = fs.readFileSync(path.join(TMPWS, 'inbox', '用户原有的图.png'))
    const postRes = await fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '联调自测.png', dataBase64: png1x1.toString('base64') }),
    })
    const postJson = await postRes.json()
    check('POST 200 且 ok=true', postRes.status === 200 && postJson.ok === true,
      `status=${postRes.status} ${JSON.stringify(postJson).slice(0, 120)}`)
    check('真的落进了 inbox/', !!(postJson.path && fs.existsSync(postJson.path)), postJson.path)
    check('落盘的字节与上传的一致', !!(postJson.path && fs.readFileSync(postJson.path).equals(png1x1)))
    check('文件名是纯文件名（没带目录）', postJson.saved === '联调自测.png', postJson.saved)
    check('回了 sha256', typeof postJson.sha256 === 'string' && postJson.sha256.length === 64)

    // 非图片
    const badRes = await fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'x.png', dataBase64: Buffer.from('这不是图片').toString('base64') }),
    })
    check('非图片字节被 400 拒收', badRes.status === 400, String(badRes.status))

    // 撞名不覆盖
    const dupRes = await fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '用户原有的图.png', dataBase64: png1x1.toString('base64') }),
    })
    const dupJson = await dupRes.json()
    check('同名的已有图**没有被覆盖**',
      fs.readFileSync(path.join(TMPWS, 'inbox', '用户原有的图.png')).equals(beforeBytes))
    check('同名时另存了一个新名字', dupJson.saved !== '用户原有的图.png', dupJson.saved)

    // 路径穿越
    const travRes = await fetch(base, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '../../../evil.png', dataBase64: png1x1.toString('base64') }),
    })
    const travJson = await travRes.json()
    check('路径穿越被洗成纯文件名', travJson.saved === 'evil.png', travJson.saved)
    check('穿越后的文件落在 inbox/ 里', fs.existsSync(path.join(TMPWS, 'inbox', 'evil.png')))
    check('没有写到工作区外面', !fs.existsSync(path.join(TMPWS, 'evil.png')))

    const delRes = await fetch(base, { method: 'DELETE' })
    check('DELETE 405', delRes.status === 405, String(delRes.status))
  } catch (e) {
    check('上传端点联调', false, String(e.message).split('\n')[0])
  } finally {
    await new Promise((r) => server.close(r))
  }
} else {
  check('拿到上传端点路由', false, `routes=${tmpRoutes.length}`)
}

// 清掉临时工作区；用户的真实 inbox/ 全程没被碰过
try { fs.rmSync(TMPWS, { recursive: true, force: true }) } catch { /* 忽略 */ }

// 复核：用户真实工作区的 inbox/ 张数没变
console.log('\n=== 7. 确认用户的真实 inbox/ 没被动过 ===')
try {
  const realInbox = path.join(WORKSPACE, 'inbox')
  const n = fs.existsSync(realInbox) ? fs.readdirSync(realInbox).length : 0
  check('真实 inbox/ 里没有本次自测留下的文件', !fs.readdirSync(realInbox).some((f) => f.includes('联调自测') || f === 'evil.png'),
    `inbox 现有 ${n} 个文件`)
} catch (e) {
  check('能读用户真实 inbox/', false, String(e.message).split('\n')[0])
}

// ── 结论
console.log('\n' + '='.repeat(62))
console.log(failed === 0 ? '  ✅ 真机联调全部通过' : `  ❌ 有 ${failed} 项没过`)
console.log('='.repeat(62))
process.exit(failed === 0 ? 0 : 1)
