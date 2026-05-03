import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import mermaid from 'mermaid'
import { cn } from './utils'
import { buildMermaidLiveUrl, normalizeMermaidSource } from './mermaid-utils'

// mermaid 全局只 init 一次。'loose' 用于允许 D2 中文等 token,与后端导出保持口径一致。
let mermaidInitialised = false
function ensureMermaidInit() {
  if (mermaidInitialised) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    // PingFang SC / Microsoft YaHei:中文 label 优先字体;后端 mermaid-cli
    // 渲染 png 时也用同款字体(docker/mermaid-config.json 一致)。
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    // dark mode 兼容:让浏览器主题切换时自动用合适色板。
    themeVariables: {
      primaryColor: '#f1f5f9',
      primaryTextColor: '#0f172a',
      lineColor: '#64748b',
      secondaryColor: '#e2e8f0',
    },
  })
  mermaidInitialised = true
}

export interface MarkdownRendererProps {
  markdown: string
  className?: string
}

// react-markdown wrapper:GFM 表格 + 原始 HTML(rehype-raw) + mermaid 自渲。
// IMPLEMENTATION_SPEC §16.4。
export function MarkdownRenderer({ markdown, className }: MarkdownRendererProps) {
  return (
    <div className={cn('prose prose-slate max-w-none', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeRaw]}
        components={{
          code({ className: codeClassName, children, ...props }) {
            const match = /language-(\w+)/.exec(codeClassName ?? '')
            const lang = match?.[1]
            const content = String(children ?? '').replace(/\n$/, '')
            if (lang === 'mermaid') {
              return <Mermaid code={content.trim()} />
            }
            return (
              <code className={codeClassName} {...props}>
                {children}
              </code>
            )
          },
        }}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  )
}

interface MermaidProps {
  code: string
}

function Mermaid({ code }: MermaidProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    ensureMermaidInit()
    let cancelled = false
    const id = `mermaid-${Math.random().toString(36).slice(2, 10)}`

    // R-16:LLM 输出在不同 mermaid 版本之间常有兼容差异,先 normalize。
    // 失败时再退回原始 code 试一次(防 normalize 误改)。
    const { code: normalized } = normalizeMermaidSource(code)

    const renderWith = (src: string) => mermaid.render(id, src)

    renderWith(normalized)
      .then(({ svg }) => {
        if (cancelled || !ref.current) return
        ref.current.innerHTML = svg
        setError(null)
      })
      .catch(async (firstErr: unknown) => {
        if (cancelled) return
        // 第二次:用原始 code(防 normalize 把本来 OK 的代码改坏)
        if (normalized !== code) {
          try {
            const { svg } = await renderWith(code)
            if (cancelled || !ref.current) return
            ref.current.innerHTML = svg
            setError(null)
            return
          } catch {
            // 落到下面统一 fallback
          }
        }
        const msg =
          firstErr instanceof Error ? firstErr.message : String(firstErr)
        setError(msg)
      })

    return () => {
      cancelled = true
    }
  }, [code])

  if (error) {
    // R-16 fallback:不让一张图表的渲染失败导致整个章节空白。
    // 展示原始源码 + 错误信息 + mermaid live editor 链接(用户可在线调试)。
    return (
      <div className="my-4 rounded-md border border-amber-200 bg-amber-50 p-3 text-xs">
        <div className="mb-2 flex items-start justify-between gap-3">
          <p className="font-medium text-amber-900">
            图表渲染失败,已显示源码
          </p>
          <a
            href={buildMermaidLiveUrl(code)}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-amber-800 underline underline-offset-2 hover:text-amber-900"
          >
            在 mermaid.live 打开
          </a>
        </div>
        <p className="mb-2 break-all text-amber-800">{error}</p>
        <pre className="overflow-x-auto rounded bg-white/60 p-2 text-slate-700">
          <code>{code}</code>
        </pre>
      </div>
    )
  }
  return <div ref={ref} className="my-4 overflow-x-auto" />
}
