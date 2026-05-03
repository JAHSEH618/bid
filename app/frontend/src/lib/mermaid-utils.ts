// Mermaid 健壮性辅助:LLM 生成的图表在不同 mermaid 版本(8.x ~ 11.x)间常见
// 兼容差异。这里做一遍预处理,把 LLM 输出 normalize 成 11.x 接受的形式;
// render 失败再 fallback 显示源码 + 在线编辑器链接(用户可手动修)。

const ZERO_WIDTH_RE = /[​‌‍﻿]/g
const NBSP_RE = / /g
const FULLWIDTH_SPACE_RE = /　/g

// LLM 经常用 `graph TD` / `graph LR`(老语法,等价 `flowchart TD/LR`)。
// mermaid 11.x 都接受,但保险起见统一成 flowchart 减少歧义。
const GRAPH_HEADER_RE = /^\s*graph\s+(TD|LR|RL|BT|TB)\b/im

// 老版本要求 `;` 行尾,新版本可省。如果检测到部分行有 `;` 部分没,统一去掉
// 避免老语法分号被解析成 statement 终止符触发 11.x 边界 bug(在 sequenceDiagram
// 内极易出现)。

// 中文标签或包含特殊字符的节点 label 没用引号包,容易把 `[xxx 中文]` 解析失败。
// 检测 `\w+\[[^\]"]*[一-鿿][^\]"]*\]` 这种模式补引号。
// 这是 best-effort,不能 100% 保证,但常见情况能改善。
const NAKED_CN_LABEL_RE =
  /([A-Za-z_][\w-]*)\[((?!")[^\]]*[一-鿿][^\]]*)\]/g

export interface NormalizeResult {
  code: string
  changes: string[]
}

export function normalizeMermaidSource(raw: string): NormalizeResult {
  const changes: string[] = []
  let code = raw

  // 1. 清理破坏性 unicode(zero-width / nbsp / 全角空格 → 普通空格)
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

  // 2. graph TD/LR → flowchart TD/LR(等价但 11.x 推荐)
  if (GRAPH_HEADER_RE.test(code)) {
    code = code.replace(GRAPH_HEADER_RE, 'flowchart $1')
    changes.push('graph → flowchart')
  }

  // 3. 中文 / 非英文 label 自动 quote 包裹(naked → quoted)
  const cnQuoted = code.replace(NAKED_CN_LABEL_RE, (_m, id, label) => {
    // label 内本身有未配对的引号则跳过,避免破坏
    if (label.includes('"')) return _m
    return `${id}["${label.trim()}"]`
  })
  if (cnQuoted !== code) {
    code = cnQuoted
    changes.push('quoted CJK labels')
  }

  // 4. 行尾分号 → 移除(防 11.x sequenceDiagram 分号 statement 解析 bug)
  // 只在 sequenceDiagram 块中处理(flowchart 分号无害)
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
