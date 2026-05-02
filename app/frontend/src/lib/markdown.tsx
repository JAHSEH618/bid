import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import mermaid from 'mermaid'
import { cn } from './utils'

// mermaid 全局只 init 一次。'loose' 用于允许 D2 中文等 token,与后端导出保持口径一致。
let mermaidInitialised = false
function ensureMermaidInit() {
  if (mermaidInitialised) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'default',
    securityLevel: 'loose',
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
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

    mermaid
      .render(id, code)
      .then(({ svg }) => {
        if (cancelled || !ref.current) return
        ref.current.innerHTML = svg
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        // 渲染失败回退成代码块,不阻塞预览。
        setError(err instanceof Error ? err.message : String(err))
      })

    return () => {
      cancelled = true
    }
  }, [code])

  if (error) {
    return (
      <pre className="my-4 overflow-x-auto rounded-md bg-slate-100 p-3 text-xs text-slate-700">
        <code>{code}</code>
      </pre>
    )
  }
  return <div ref={ref} className="my-4 overflow-x-auto" />
}
