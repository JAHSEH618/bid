/* eslint-disable no-irregular-whitespace, no-misleading-character-class */
// 文件级 disable 说明:本模块的核心职责就是"清洗 LLM 输出里的不可见字符"(零宽空格、
// NBSP、全角空格)。正则字面量内出现这些字符是**有意为之**,不是误输入;
// eslint 的两条规则在此场景反向触发了误报。
// Mermaid 健壮性辅助:LLM 生成的图表在不同 mermaid 版本(8.x ~ 11.x)间常见
// 兼容差异。这里做一遍预处理,把 LLM 输出 normalize 成 11.x 接受的形式;
// render 失败再 fallback 显示源码 + 在线编辑器链接(用户可手动修)。

const ZERO_WIDTH_RE = /[​‌‍﻿⁠]/g
const NBSP_RE = / /g
const FULLWIDTH_SPACE_RE = /　/g
const BOM_RE = /^﻿/

// LLM 经常用 `graph TD` / `graph LR`(老语法,等价 `flowchart TD/LR`)。
// mermaid 11.x 都接受,但保险起见统一成 flowchart 减少歧义。
const GRAPH_HEADER_RE = /^\s*graph\s+(TD|LR|RL|BT|TB)\b/im

// 中文 LLM 常见错误:把箭头里的连字符写成中文破折号 / 短破折号 / 全角连字符,
// 或干脆用方向箭头符号。统一拉回 ASCII `-->` / `---`,否则 mermaid 词法直接挂。
// 注意:替换边语法时也要兼顾标签边 `--text-->` 这种长形式 → 先把破折号统一化,
// 再交给 mermaid。
const ARROW_DASH_RE = /[—–―]{1,3}(?=\s*>)/g          // ——> / —> / – → 用 -- 顶替
const ARROW_DASH_NONTRAIL_RE = /(?<=>\s*)[—–―]{1,3}/g // 反向:>—— → >--
const ARROW_ASCII_GTLT_RE = /[＞﹥]/g                 // 全角 > → ASCII >
const ARROW_FULLWIDTH_HYPHEN_RE = /－/g              // 全角短横 → ASCII -
const ARROW_CJK_DIRECTIONAL_RE = /→/g                // 单字符箭头 → -->

// 标签 / 节点定义里的全角方括号 / 圆括号 / 大括号 ([U+FF08-U+FF5D] 系列)
// 都在 FULLWIDTH_ALNUM_RE 的 U+FF01-U+FF5E 范围内,由统一的全角→ASCII 转换吃掉;
// 这里不另设独立 regex。CJK 区间内的"中文括号" `【】` `「」` U+3010/U+3011 形态不同
// 且通常是用户在 label 文案里的有意写法,不动。

// 全角拉丁字母 / 数字 / ASCII 符号(U+FF01-U+FF5E)→ ASCII。
// 出现在节点 ID 里会让 mermaid 词法挂掉(`B１[xxx]` 解析失败),全部统一更安全;
// label 文案里出现的概率几乎为 0(中文里几乎不用全角 ABC123)。
// 用 \uXXXX 转义而非裸字符:工具链 / 编辑器有时把全角符号"规范化"成 ASCII,
// 让本意检查全角的 regex 退化成检查 ASCII(实测踩过)。
const FULLWIDTH_ALNUM_RE = /[！-～]/g

// 智能引号 → ASCII 双引号(在 mermaid `"label"` 语法位置必须 ASCII)
const SMART_QUOTES_RE = /[“”„‟]/g
const SMART_SINGLE_QUOTES_RE = /[‘’‚‛]/g

// 节点标签自动加引号:LLM 输出里 `id[label]` 形式的 label,只要包含任何非 ASCII
// 字符(中文 / 全角 / 数学符号 ×÷ / 度°)就给套上引号,避免 mermaid 词法 / 语法歧义。
// 老版规则只覆盖 CJK 基本块,放宽到"任何非 ASCII 字符"。
// 同时支持 `(...)`, `{...}` 形状,但这两种在 mermaid 内是其他形状语法,LLM 误用
// 概率小;先只处理 `[...]`(用得最多)。
//   匹配组: 1 = node id (例 B1, abc-1), 2 = label 文本(不含未配对引号)
const NAKED_NONASCII_LABEL_RE =
  // eslint-disable-next-line no-control-regex
  /([A-Za-z_][\w-]*)\[((?!")[^\]\n]*[^\x00-\x7F][^\]\n]*)\]/g

// 即便 label 是纯 ASCII,LLM 也可能塞进 `(` `)` `|` 这些 mermaid 在 label 上下文
// 内不允许的字符。给整段 ASCII label 自动加引号,只要它含可疑字符。
const NAKED_ASCII_RISKY_LABEL_RE =
  /([A-Za-z_][\w-]*)\[((?!")[^\]\n"]*[()|#&<>][^\]\n"]*)\]/g

export interface NormalizeResult {
  code: string
  changes: string[]
}

function fullwidthToAscii(s: string): string {
  // U+FF01-U+FF5E (! 到 ~) → U+0021-U+007E
  return s.replace(FULLWIDTH_ALNUM_RE, (c) =>
    String.fromCharCode(c.charCodeAt(0) - 0xfee0),
  )
}

// 单行级:把未闭合的 `[` 在行末补 `]`。考虑引号转义,引号内的 `[` `]` 不计数。
// 行外 `]` 多余的情况不动(可能是其它语法)。
// 这一步是为了治"LLM 输出截断 / 漏 `]`"导致 mermaid 把后续行吞成 label 的连锁错。
function autoCloseBracketsPerLine(code: string): { code: string; changed: boolean } {
  let changed = false
  const lines = code.split('\n')
  const fixed = lines.map((line) => {
    let depth = 0
    let inQuote = false
    for (let i = 0; i < line.length; i++) {
      const c = line[i]
      if (inQuote) {
        if (c === '"') inQuote = false
      } else if (c === '"') {
        inQuote = true
      } else if (c === '[') {
        depth++
      } else if (c === ']') {
        if (depth > 0) depth--
      }
    }
    if (depth > 0) {
      changed = true
      // 如果断在引号内,先补 `"` 再补 `]`(避免后续解析出错)
      const closer = (inQuote ? '"' : '') + ']'.repeat(depth)
      return line + closer
    }
    return line
  })
  return { code: fixed.join('\n'), changed }
}

export function normalizeMermaidSource(raw: string): NormalizeResult {
  const changes: string[] = []
  let code = raw

  // 0. BOM(常见于复制粘贴 Excel/Word 内容时)
  if (BOM_RE.test(code)) {
    code = code.replace(BOM_RE, '')
    changes.push('removed BOM')
  }

  // 1. 清理破坏性 unicode(zero-width / nbsp / 全角空格 / word joiner → 普通空格 / 删除)
  if (ZERO_WIDTH_RE.test(code)) {
    code = code.replace(ZERO_WIDTH_RE, '')
    changes.push('removed zero-width chars')
  }
  if (NBSP_RE.test(code)) {
    code = code.replace(NBSP_RE, ' ')
    changes.push('replaced nbsp')
  }
  if (FULLWIDTH_SPACE_RE.test(code)) {
    code = code.replace(FULLWIDTH_SPACE_RE, ' ')
    changes.push('replaced fullwidth space')
  }

  // 2. 全角 ASCII (U+FF01-U+FF5E) → ASCII。覆盖全角数字 / 字母 / 标点(含括号/分号/引号
  //    等"看起来一样但不是 ASCII"的同形字符)。一次扫描,后续步骤就只看到 ASCII。
  if (FULLWIDTH_ALNUM_RE.test(code)) {
    code = fullwidthToAscii(code)
    changes.push('fullwidth → ascii')
  }

  // 3. 智能引号 → ASCII 双引号(在 mermaid `"label"` 语法位置必须 ASCII)
  if (SMART_QUOTES_RE.test(code)) {
    code = code.replace(SMART_QUOTES_RE, '"')
    changes.push('smart quotes → ascii')
  }
  if (SMART_SINGLE_QUOTES_RE.test(code)) {
    code = code.replace(SMART_SINGLE_QUOTES_RE, "'")
    changes.push('smart single quotes → ascii')
  }

  // 4. 箭头修正:em-dash / en-dash / horizontal-bar → ASCII --,全角 > → ASCII >,
  //    单字符箭头 → -->;全角短横统一回 ASCII -。
  if (ARROW_DASH_RE.test(code) || ARROW_DASH_NONTRAIL_RE.test(code)) {
    code = code.replace(ARROW_DASH_RE, '--').replace(ARROW_DASH_NONTRAIL_RE, '--')
    changes.push('normalized arrow dashes')
  }
  if (ARROW_ASCII_GTLT_RE.test(code)) {
    code = code.replace(ARROW_ASCII_GTLT_RE, '>')
    changes.push('normalized fullwidth gt')
  }
  if (ARROW_FULLWIDTH_HYPHEN_RE.test(code)) {
    code = code.replace(ARROW_FULLWIDTH_HYPHEN_RE, '-')
    changes.push('normalized fullwidth hyphen')
  }
  if (ARROW_CJK_DIRECTIONAL_RE.test(code)) {
    code = code.replace(ARROW_CJK_DIRECTIONAL_RE, '-->')
    changes.push('normalized → to -->')
  }

  // 5. graph TD/LR → flowchart TD/LR(等价但 11.x 推荐)
  if (GRAPH_HEADER_RE.test(code)) {
    code = code.replace(GRAPH_HEADER_RE, 'flowchart $1')
    changes.push('graph → flowchart')
  }

  // 6. 自动补 `]`:LLM 截断 / 漏写会让后续行被吞成 label,触发 `got '1'` 这类
  //    级联 parse error。先按行补齐再交给 mermaid。
  const closed = autoCloseBracketsPerLine(code)
  if (closed.changed) {
    code = closed.code
    changes.push('auto-closed unmatched brackets')
  }

  // 7. 非 ASCII label 自动 quote 包裹(naked → quoted)
  const cnQuoted = code.replace(NAKED_NONASCII_LABEL_RE, (_m, id, label) => {
    if (label.includes('"')) return _m
    return `${id}["${label.trim()}"]`
  })
  if (cnQuoted !== code) {
    code = cnQuoted
    changes.push('quoted non-ASCII labels')
  }

  // 8. 纯 ASCII 但含可疑字符的 label 也加引号(`(`, `)`, `|`, `#`, `&`, `<`, `>`)
  const asciiQuoted = code.replace(NAKED_ASCII_RISKY_LABEL_RE, (_m, id, label) => {
    if (label.includes('"')) return _m
    return `${id}["${label.trim()}"]`
  })
  if (asciiQuoted !== code) {
    code = asciiQuoted
    changes.push('quoted ASCII labels with risky chars')
  }

  // 9. 行尾分号 → 移除(防 11.x sequenceDiagram 分号 statement 解析 bug)
  if (/^\s*sequenceDiagram\b/im.test(code)) {
    const stripped = code.replace(/;\s*$/gm, '')
    if (stripped !== code) {
      code = stripped
      changes.push('stripped trailing ; in sequenceDiagram')
    }
  }

  return { code: code.trim(), changes }
}

// mermaid live editor:用户复制源码可以在线调试。
// pako 编码格式比较复杂,这里给简单的 base64 fallback URL。
export function buildMermaidLiveUrl(code: string): string {
  try {
    const payload = JSON.stringify({
      code,
      mermaid: { theme: 'default' },
    })
    // mermaid live 接受 base64;不做 pako 压缩(节省 30 行依赖)
    const encoded = btoa(unescape(encodeURIComponent(payload)))
    return `https://mermaid.live/edit#base64:${encoded}`
  } catch {
    return 'https://mermaid.live/edit'
  }
}
