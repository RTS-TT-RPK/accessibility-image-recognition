/**
 * DSH 技能自检脚本（图像数据识别转化技能）
 *
 * 作用：借用 DSH 自己的技能扫描代码，检查本机 DSH 是否能发现「图像数据识别转化」技能。
 *
 * 用法（在任意目录打开 PowerShell）：
 *     node "<本脚本路径>" [工作区路径]
 * 例：
 *     node tools\DSH技能自检.mjs "C:\Users\86173\Desktop\图像识别长期工程"
 * 不带参数时，用当前所在目录当工作区检查。
 *
 * 退出码：0 = 技能可被发现且可读取；1 = 未发现或读取失败；2 = 找不到 DSH 模块。
 */
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'

const dshHome = process.env.DSH_HOME ?? join(homedir(), '.dsh')
const providerPath = join(
  dshHome,
  'profiles',
  'node_modules',
  '@deepseek-ai',
  'dsh-skill-filesystem',
  'lib',
  'index.js',
)
const workspace = process.argv[2] ?? process.cwd()
const SKILL_NAME = 'image-data-recognition'

if (!existsSync(providerPath)) {
  console.error('【自检失败】找不到 DSH 的技能扫描模块：')
  console.error('  ' + providerPath)
  console.error('请确认 DSH 已在本机安装，并至少启动过一次。')
  process.exit(2)
}

const { FileSystemSkillProvider } = await import(pathToFileURL(providerPath).href)

const warnings = []
const ctx = {
  logger: {
    warn: (msg) => warnings.push(String(msg)),
    error: (msg) => warnings.push(String(msg)),
    info: () => {},
    debug: () => {},
  },
  get: () => undefined,
}
const abort = new AbortController()
const control = { invalidate: () => {}, signal: abort.signal }
const provider = new FileSystemSkillProvider(ctx, control, {})

console.log('========================================')
console.log(' DSH 技能自检 —— 图像数据识别转化')
console.log('========================================')
console.log('DSH 主目录：   ' + dshHome)
console.log('检查的工作区： ' + workspace)
console.log('')

const listed = await provider.list({ cwd: workspace })
const candidates = Array.isArray(listed) ? listed : listed.candidates
console.log('DSH 共发现技能 ' + candidates.length + ' 个。')

const target = candidates.find((c) => c.name === SKILL_NAME)
if (target === undefined) {
  console.log('')
  console.log('【未发现】技能「' + SKILL_NAME + '」不在 DSH 的技能目录里。')
  console.log('请把技能文件夹放到：' + join(dshHome, 'skills', SKILL_NAME))
  process.exitCode = 1
} else {
  console.log('')
  console.log('【已发现】技能「' + target.name + '」（来源：' + target.source + '）')
  console.log('  描述：' + target.description)
  console.log('  文件：' + target.locator.path)
  const def = await provider.get(target, { signal: abort.signal })
  if (def === undefined) {
    console.log('【读取失败】技能正文打不开，文件可能损坏。')
    process.exitCode = 1
  } else {
    console.log('【可读取】技能正文 ' + def.content.length + ' 字，头部：')
    console.log('  ' + def.content.slice(0, 100).replace(/\n/g, ' ').trim() + ' ……')
  }
}

console.log('')
console.log('DSH 实际会扫描的技能根目录（按优先级从高到低）：')
const roots = await provider.roots(workspace)
for (const root of roots) {
  console.log('  [优先级 ' + root.rank + '] ' + root.path)
}

abort.abort()
await new Promise((resolve) => setTimeout(resolve, 400))

if (warnings.length > 0) {
  console.log('')
  console.log('扫描过程记录（供排查用）：')
  for (const w of warnings) console.log('  - ' + w)
}

process.exit(process.exitCode ?? 0)
