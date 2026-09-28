// 模块加载钩子本体：把 '@deepseek-ai/dsh-tools' 换成一个记录用的替身。
//
// 为什么需要：本插件装在 ~/.dsh/profiles/web/node_modules，运行期由 DSH 提供
// @deepseek-ai/dsh-tools（见 package.json 的 peerDependencies）。开发机上这个包
// 不在本插件目录的解析范围里，所以这里用 Node 的模块加载钩子把它换成替身，
// 好让 dev\tools\test-host.mjs 能在不安装任何东西的前提下把宿主半区跑起来。
//
// 本文件由 stub-loader.mjs 通过 module.register() 挂上，不要直接跑。
// 用法：node --import ./dev/tools/stub-loader.mjs dev/tools/test-host.mjs

/** 替身模块的合成 url。 */
export const STUB_URL = 'dsh-tools-stub:defineTool'

/** load 钩子交给 Node 的源码：defineTool 只记录、原样返回。 */
const STUB_SOURCE = 'export function defineTool(options) { globalThis.__DSH_STUB_TOOLS__.push(options); return options }\n'

export function resolve(specifier, context, nextResolve) {
  if (specifier === '@deepseek-ai/dsh-tools') {
    return { url: STUB_URL, shortCircuit: true, format: 'module' }
  }
  return nextResolve(specifier, context)
}

export function load(url, context, nextLoad) {
  if (url === STUB_URL) {
    return { format: 'module', shortCircuit: true, source: STUB_SOURCE }
  }
  return nextLoad(url, context)
}
