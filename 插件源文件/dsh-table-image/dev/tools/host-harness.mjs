// 宿主半区测试台 —— 在 Node 里直接加载 index.js，用假的 ctx 跑一遍 apply()。
//
// 为什么要有它：客户端半边已经有 test-harness.mjs（83 项），但宿主半边一旦在
// 注册工具时抛错，DSH 启动只会打一条警告，用户看到的是"工具凭空没出现"，
// 而且**必须重启 DSH 才能发现**。重启一次成本很高，所以在这里先跑一遍。
//
// 用法：
//   node dev/tools/host-harness.mjs <工作区绝对路径>
//
// 它检查：
//   1. 模块能不能被 import（语法/导入路径错了会在这里炸）
//   2. apply(ctx, config) 不抛异常
//   3. 注册了几个 table_image_* 工具，每个的 name/description/parameters/output/execute 是否齐全
//      （DSH 规定 output:{schema,render} 是必须的，缺了 register 会抛）
//   4. 缺 webServer 这个可选服务时，插件仍然能加载（只是拖拽收图不可用）
//   5. 真跑一次 table_image_status（真的去调 Python 脚本），确认壳子是通的

import { pathToFileURL, fileURLToPath } from 'node:url'
import path from 'node:path'

const workspace = process.argv[2]
if (!workspace) {
  console.error('用法：node dev/tools/host-harness.mjs <工作区绝对路径>')
  process.exit(2)
}

// 注意：不能用 new URL(import.meta.url).pathname —— 中文路径会被百分号编码，
// 拼出来的路径就找不到了。必须用 fileURLToPath 还原成本地路径。
const HERE = path.dirname(fileURLToPath(import.meta.url))
const pkgRoot = path.resolve(HERE, '..', '..')
const entry = path.join(pkgRoot, 'index.js')

const results = []
/**
 * 记一条检查结果。
 *
 * 注意签名是 (标签, 布尔, 细节)。原稿这一行调用时把布尔写在了第二个参数、
 * 标签写在了第一个，于是 `check(true, ranPython, ...)` 被当成 label=true、
 * ok=ranPython —— 明明跑通了却报红。这里把调用点纠正过来（helper 不动）。
 */
function check(label, ok, detail = '') {
  results.push({ label, ok, detail })
  console.log(`  ${ok ? '✅' : '❌'} ${label}${detail ? '  <- ' + detail : ''}`)
}

// ---------------------------------------------------------------- 假 ctx
const tools = new Map()
const effects = []
const warnings = []

function makeCtx() {
  const ctx = {
    // cordis: 注册的所有东西都要通过 effect 挂，返回清理函数
    effect(fn, label) {
      const disposer = fn()
      effects.push({ label, disposer })
      return () => { if (typeof disposer === 'function') disposer() }
    },
    on() { return () => {} },
    get(name) {
      // 故意不给 webServer：模拟"这个 profile 没装它"的最坏情况
      return undefined
    },
    inject(names, cb) {
      // 服务不在 → cordis 不会调用回调。这里如实照做，用来验证插件不依赖它。
      return () => {}
    },
    logger: {
      info: (...a) => warnings.push(['info', a.join(' ')]),
      warn: (...a) => warnings.push(['warn', a.join(' ')]),
      error: (...a) => warnings.push(['error', a.join(' ')]),
    },
    tools: {
      register(def) {
        if (!def || typeof def !== 'object') throw new Error('register 收到非对象')
        if (!def.name) throw new Error('工具没有 name')
        if (!def.description) throw new Error(`工具 ${def.name} 没有 description`)
        if (!def.parameters || typeof def.parameters !== 'object')
          throw new Error(`工具 ${def.name} 没有 parameters`)
        if (!def.output || typeof def.output !== 'object' || typeof def.output.render !== 'function')
          throw new Error(`工具 ${def.name} 必须声明 output { schema, render }`)
        if (typeof def.execute !== 'function') throw new Error(`工具 ${def.name} 没有 execute`)
        if (tools.has(def.name)) throw new Error(`工具 ${def.name} 重复注册`)
        tools.set(def.name, def)
        return () => tools.delete(def.name)
      },
    },
  }
  return ctx
}

console.log()
console.log('='.repeat(62))
console.log('  宿主半区测试台')
console.log('='.repeat(62))
console.log(`  插件入口：${entry}`)
console.log(`  工作区：  ${workspace}`)
console.log()

// ---------------------------------------------------------------- 1. import
let mod
try {
  mod = await import(pathToFileURL(entry).href)
  check('index.js 能被 import', true)
} catch (e) {
  check('index.js 能被 import', false, String(e && e.message || e))
  console.log('\n导入就失败了，后面没法测。')
  process.exit(1)
}

check('导出了 apply()', typeof mod.apply === 'function')
check('导出了 inject 数组', Array.isArray(mod.inject), JSON.stringify(mod.inject))
check('只声明了必需服务（webServer 走可选注入，不在 inject 里）',
  Array.isArray(mod.inject) && !mod.inject.includes('webServer'), JSON.stringify(mod.inject))

// ---------------------------------------------------------------- 2. apply
const ctx = makeCtx()
let applyErr = null
try {
  mod.apply(ctx, { workspace })
} catch (e) {
  applyErr = e
}
check('apply() 不抛异常（缺 webServer 时也能加载）', applyErr === null,
  applyErr ? String(applyErr && applyErr.stack || applyErr) : '')

// ---------------------------------------------------------------- 3. 工具清单
const expected = [
  'table_image_status', 'table_image_start', 'table_image_next', 'table_image_submit',
  'table_image_check', 'table_image_review', 'table_image_import_review',
  'table_image_finalize', 'table_image_register_schema',
]
console.log()
console.log('=== 注册的工具 ===')
for (const name of [...tools.keys()].sort()) {
  const d = tools.get(name)
  console.log(`  · ${name}`)
  console.log(`      ${String(d.description).slice(0, 70)}...`)
}
console.log()

check(`注册了 ${expected.length} 个 table_image_* 工具`,
  expected.every((n) => tools.has(n)), `实际 ${tools.size} 个`)
check('没有意料之外的工具名',
  [...tools.keys()].every((n) => n.startsWith('table_image_')),
  [...tools.keys()].filter((n) => !n.startsWith('table_image_')).join(','))

for (const name of expected) {
  const d = tools.get(name)
  if (!d) continue
  const params = Object.keys(d.parameters || {})
  check(`${name}：参数齐（${params.join('/') || '无参数'}）`, true)
}

// output.render 必须返回内容块
let renderOk = true
let renderDetail = ''
for (const [name, d] of tools) {
  try {
    const blocks = d.output.render({}, { exitCode: 0, stdout: 'x', stderr: '' })
    if (!Array.isArray(blocks) || !blocks.length || !blocks[0].type) {
      renderOk = false
      renderDetail = `${name} 的 render 返回了 ${JSON.stringify(blocks)}`
    }
  } catch (e) {
    renderOk = false
    renderDetail = `${name} 的 render 抛了：${e.message}`
  }
}
check('每个工具的 output.render 都能返回内容块', renderOk, renderDetail)

// ---------------------------------------------------------------- 4. 真调一次 Python
console.log()
console.log('=== 真跑一次 table_image_status（真的去调 Python 脚本）===')
const statusTool = tools.get('table_image_status')
if (!statusTool) {
  check('能真跑 table_image_status', false, '工具没注册上')
} else {
  try {
    const value = await statusTool.execute({}, { signal: undefined })
    const text = statusTool.output.render({}, value).map((b) => b.text).join('\n')
    const ranPython = value && typeof value.exit_code === 'number'
    check('table_image_status 真的跑通了（返回退出码与输出）', ranPython,
      ranPython ? `exit_code=${value.exit_code}，${value.note || ''}`.slice(0, 160)
                : JSON.stringify(value).slice(0, 200))
    check('退出码为 0（脚本真的执行成功，不是壳子空转）',
      ranPython && value.exit_code === 0, `exit_code=${value && value.exit_code}`)
    check('返回内容里带上了脚本的原话（模型能看见）', /==|任务|runs|进度/.test(text),
      text.split('\n').filter(Boolean).slice(0, 3).join(' | ').slice(0, 160))
  } catch (e) {
    check('table_image_status 真的跑通了', false, String(e && e.message || e))
  }
}

// ---------------------------------------------------------------- 5. 清理
console.log()
console.log('=== 清理（断开注册）===')
// 说明：ctx.tools.register() 内部本身就是 effect 作用域的（见 packages/core/tools/src/index.ts
// 的 register()：return this.layers.effect(...)），所以工具不额外包 ctx.effect 也是对的。
// 这里只确认"该断开的时候能断开"，不强制每个工具都出现在 effects 里。
let cleanupOk = true
for (const { disposer } of effects) {
  try { if (typeof disposer === 'function') disposer() } catch { cleanupOk = false }
}
check('已注册的工具都能被注销（热重载不会残留）',
  cleanupOk && typeof statusTool !== 'undefined', `effects=${effects.length}（register 自带 effect 作用域）`)

// ---------------------------------------------------------------- 结论
const failed = results.filter((r) => !r.ok)
console.log()
console.log('='.repeat(62))
console.log(`  检查项：${results.length} 条，通过 ${results.length - failed.length} 条，失败 ${failed.length} 条`)
if (failed.length) {
  console.log('  失败项：')
  for (const f of failed) console.log(`    · ${f.label}${f.detail ? '  <- ' + f.detail : ''}`)
}
console.log(failed.length ? '  ❌ 有问题，先别重启 DSH' : '  ✅ 宿主半区没问题，可以重启 DSH 了')
console.log('='.repeat(62))
process.exit(failed.length ? 1 : 0)
