// 把 stub-hooks.mjs 挂成模块加载钩子。
//
// 注意 Node 版本差异：本机是 v22.23.3，module.register() 的第一个参数要的是
// **模块标识符**（不是 hooks 对象）—— 传对象会报
// ERR_UNSUPPORTED_RESOLVE_REQUEST: Failed to resolve module specifier "[object Object]"。
// 所以这里用 import.meta.resolve() 把钩子文件解析成 file: URL 再传进去。
//
// 用法：node --import ./dev/tools/stub-loader.mjs dev/tools/test-host.mjs

import { register } from 'node:module'

register(import.meta.resolve('./stub-hooks.mjs'))
