// 括号配平 / 引号状态检查器。
//
// 为什么单独写一个：在 PowerShell 里用 `node -e "..."` 写带引号状态的扫描器，
// 反斜杠和引号会被两层解释器啃掉好几轮，写出来的检查器本身先出语法错（真踩过）。
// 放进一个 .mjs 文件里就没有这个问题。
//
// 它按 JS 的词法状态机扫一遍源码：跳过字符串、模板串、行注释、块注释，
// 然后只统计真正参与语法的括号。任何一处提前闭合/交错闭合都会带行号报出来。
//
// 用法：node dev/tools/check-brackets.mjs [文件...]
//       （不给参数时默认检查包根目录的 client.js 与 index.js）

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '..', '..')

const files = process.argv.length > 2
  ? process.argv.slice(2)
  : [path.join(ROOT, 'client.js'), path.join(ROOT, 'index.js')]

const PAIRS = { '(': ')', '[': ']', '{': '}' }

/**
 * 吃掉一个正则字面量（调用时 src[i] 一定是起始的 `/`）。
 * 处理 `[...]` 字符类（里面的 `/` 不是结束符）与 `\/` 转义，末尾吃 flag 字母。
 * @param src - 源码。
 * @param i - 起始 `/` 的下标。
 * @returns 结束后（含）的下标。
 */
function skipRegex(src, i) {
  let j = i + 1
  let inClass = false
  while (j < src.length) {
    const c = src[j]
    if (c === '\\') { j += 2; continue }
    if (c === '\n') break // 正则不能跨行，说明判断错了，就地放弃
    if (c === '[') { inClass = true; j += 1; continue }
    if (c === ']') { inClass = false; j += 1; continue }
    if (c === '/' && !inClass) { j += 1; break }
    j += 1
  }
  while (j < src.length && /[a-z]/i.test(src[j])) j += 1
  return j - 1
}

/** 上一个有意义的字符（跳过空白），用来判断 `/` 是除号还是正则开头。 */
function prevMeaningful(src, i) {
  for (let j = i - 1; j >= 0; j -= 1) {
    const c = src[j]
    if (c === ' ' || c === '\t' || c === '\n' || c === '\r') continue
    return c
  }
  return ''
}

/**
 * 扫一个文件，返回配平结果。
 * @param src - 源码文本。
 * @returns { counts, mismatches, unclosed, unbalanced, quoteLeftOpen, blockCommentLeftOpen }
 */
function scan(src) {
  const counts = { '(': 0, ')': 0, '[': 0, ']': 0, '{': 0, '}': 0 }
  const mismatches = []
  const stack = []
  let line = 1
  let quote = null // 当前字符串的定界符：' " 或 `
  let escaped = false
  let lineComment = false
  let blockComment = false

  for (let i = 0; i < src.length; i += 1) {
    const c = src[i]
    const n = src[i + 1]

    if (c === '\n') {
      line += 1
      lineComment = false
      escaped = false
      continue
    }
    if (lineComment) continue
    if (blockComment) {
      if (c === '*' && n === '/') { blockComment = false; i += 1 }
      continue
    }
    if (quote) {
      if (escaped) { escaped = false; continue }
      if (c === '\\') { escaped = true; continue }
      if (c === quote) quote = null
      continue
    }
    if (c === '/' && n === '/') { lineComment = true; i += 1; continue }
    if (c === '/' && n === '*') { blockComment = true; i += 1; continue }

    // 正则字面量：`/` 出现在"值该出现的位置"时是正则的开头，不是除号。
    // 不处理它的话，`/[\u0000-\u001f<>:"/\\|?*]/g` 里那个 `"` 会被当成字符串开头，
    // 之后整个文件的引号状态就全乱了（这是本检查器第一版真踩过的坑）。
    if (c === '/') {
      const prev = prevMeaningful(src, i)
      if (prev === '' || '(,=:[!&|?{};+-*%~^<>'.includes(prev)
        || /^(return|typeof|instanceof|in|of|new|delete|void|do|else|case|yield|await)$/.test(trailingWord(src, i))) {
        i = skipRegex(src, i)
        continue
      }
    }

    if (c === "'" || c === '"' || c === '`') { quote = c; continue }

    if (Object.prototype.hasOwnProperty.call(counts, c)) {
      counts[c] += 1
      if (Object.prototype.hasOwnProperty.call(PAIRS, c)) {
        stack.push({ c, line })
      } else {
        const top = stack.pop()
        const want = top ? PAIRS[top.c] : null
        if (want !== c) {
          mismatches.push(`第 ${line} 行遇到 ${c}，但此时该闭合的是 ${want || '（栈已空）'}`
            + (top ? `（那个 ${top.c} 在第 ${top.line} 行）` : ''))
        }
      }
    }
  }

  const unbalanced = {
    round: counts['('] - counts[')'],
    square: counts['['] - counts[']'],
    curly: counts['{'] - counts['}'],
  }
  const unclosed = stack.map((s) => `${s.c}（第 ${s.line} 行开）`)
  return { counts, mismatches, unclosed, unbalanced, quoteLeftOpen: quote !== null, blockCommentLeftOpen: blockComment }
}

/** 取 i 前面那个完整的单词（用来认 return / typeof 这类关键字）。 */
function trailingWord(src, i) {
  let j = i - 1
  while (j >= 0 && /\s/.test(src[j])) j -= 1
  const end = j + 1
  while (j >= 0 && /[A-Za-z_$]/.test(src[j])) j -= 1
  return src.slice(j + 1, end)
}

let allOk = true
for (const f of files) {
  const label = path.relative(ROOT, f) || f
  console.log(`\n=== ${label} ===`)
  if (!fs.existsSync(f)) {
    console.log('  ❌ 文件不存在')
    allOk = false
    continue
  }
  const src = fs.readFileSync(f, 'utf8')
  const r = scan(src)
  console.log(`  括号计数：圆 ( ${r.counts['(']} / ) ${r.counts[')']}`
    + `　方 [ ${r.counts['[']} / ] ${r.counts[']']}`
    + `　花 { ${r.counts['{']} / } ${r.counts['}']}`)
  console.log(`  差值：圆 ${r.unbalanced.round}　方 ${r.unbalanced.square}　花 ${r.unbalanced.curly}`)

  if (r.mismatches.length) {
    console.log('  ❌ 有交错闭合：')
    r.mismatches.forEach((m) => console.log('     · ' + m))
  }
  if (r.unclosed.length) {
    console.log('  ❌ 有没闭合的括号：' + r.unclosed.join('、'))
  }
  if (r.quoteLeftOpen) console.log('  ❌ 扫描到文件末尾时还停在字符串里（引号没闭上）')
  if (r.blockCommentLeftOpen) console.log('  ❌ 扫描到文件末尾时还停在块注释里（*/ 没写）')

  const ok = r.mismatches.length === 0 && r.unclosed.length === 0
    && r.unbalanced.round === 0 && r.unbalanced.square === 0 && r.unbalanced.curly === 0
    && !r.quoteLeftOpen && !r.blockCommentLeftOpen
  console.log(ok ? '  ✅ 括号全部配平' : '  ❌ 不配平')
  if (!ok) allOk = false
}

console.log(`\n=== 结论 ===\n  ${allOk ? '✅ 全部配平' : '❌ 有文件不配平'}`)
process.exit(allOk ? 0 : 1)
