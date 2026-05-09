import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeRaw from 'rehype-raw'
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize'
import DOMPurify, { type Config as DOMPurifyConfig } from 'isomorphic-dompurify'
import { cn } from './utils'
import { buildMermaidLiveUrl, normalizeMermaidSource } from './mermaid-utils'

// ──────────────────────────────────────────────────────────────────────────
// 安全:LLM 输出可能含恶意 HTML(<script>、<iframe>、on* 事件、javascript: URL),
// 上传文档解析路径也可能引入未知 HTML。两道防线:
//   1) react-markdown 链 rehype-raw → rehype-sanitize:解析 raw HTML 后做白名单清洗。
//   2) mermaid 渲染产物的 svg 字符串再过一次 DOMPurify(SVG profile),
//      去掉 <script>/<foreignObject>/on* 属性后才 innerHTML 注入。
// 备注:mermaid securityLevel: 'loose' 仍保留(中文 label / click event 需要),
// 但下游 svg 已经被 DOMPurify 清洗,等价于把"信任 mermaid 内部"换成"信任白名单清洗"。
// ──────────────────────────────────────────────────────────────────────────

// rehype-sanitize 默认 schema 太严:会丢掉 ```code``` 的 className(我们靠
// className=language-mermaid 识别 mermaid block),也会丢 props/style 等。
// 这里在 defaultSchema 之上扩展 code/pre/span 的 className 白名单。
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...(defaultSchema.attributes?.code ?? []), ['className']],
    pre: [...(defaultSchema.attributes?.pre ?? []), ['className']],
    span: [...(defaultSchema.attributes?.span ?? []), ['className']],
    div: [...(defaultSchema.attributes?.div ?? []), ['className']],
  },
  // 显式禁止 raw HTML 里夹带的 script/iframe/object/embed/form 等(默认已禁,这里冗余表达意图)
  tagNames: (defaultSchema.tagNames ?? []).filter(
    (t) => !['script', 'iframe', 'object', 'embed', 'form'].includes(t)
  ),
}

// DOMPurify 用 SVG profile 清洗 mermaid 渲染产物。FORBID 重点 vector:
// - <script> / <foreignObject>(可塞 HTML / JS)
// - on* 事件属性(SVG 也会触发)
// - javascript: / data: URI(href / xlink:href)
const SVG_PURIFY_CONFIG: DOMPurifyConfig = {
  USE_PROFILES: { svg: true, svgFilters: true },
  FORBID_TAGS: ['script', 'foreignObject'],
  FORBID_ATTR: [
    'onload',
    'onerror',
    'onclick',
    'onmouseover',
    'onmouseout',
    'onfocus',
    'onblur',
  ],
}

function sanitizeMermaidSvg(svg: string): string {
  // returnDOMFragment 默认 false,会返回字符串。USE_PROFILES.svg 已限定 SVG 白名单。
  return DOMPurify.sanitize(svg, SVG_PURIFY_CONFIG) as unknown as string
}

// ──────────────────────────────────────────────────────────────────────────
// Mermaid 动态加载:mermaid 11.x ≈ 1MB+,顶层 import 把主 chunk 撑到 1.4MB。
// 改为首次 Mermaid 组件 mount 时 dynamic import,vite 自动拆 lazy chunk。
// ──────────────────────────────────────────────────────────────────────────
type MermaidModule = typeof import('mermaid')['default']
let _mermaidModule: MermaidModule | null = null
let _mermaidPromise: Promise<MermaidModule> | null = null

function ensureMermaidInit(m: MermaidModule) {
  m.initialize({
    startOnLoad: false,
    theme: 'base',
    securityLevel: 'loose',
    // PingFang SC / Microsoft YaHei:中文 label 优先字体;后端 mermaid-cli
    // 渲染 png 时也用同款字体(docker/mermaid-config.json 一致)。
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    // 用户反馈:mermaid 图底色应该是白色(theme:base + 显式 background 白)。
    themeVariables: {
      background: '#ffffff',
      primaryColor: '#ffffff',
      primaryTextColor: '#0f172a',
      primaryBorderColor: '#1f2937',
      lineColor: '#475569',
      secondaryColor: '#f1f5f9',
      tertiaryColor: '#e2e8f0',
      edgeLabelBackground: '#ffffff',
      clusterBkg: '#ffffff',
      clusterBorder: '#cbd5e1',
      noteBkgColor: '#fef9c3',
      noteTextColor: '#0f172a',
      noteBorderColor: '#facc15',
    },
  })
}

async function getMermaid(): Promise<MermaidModule> {
  if (_mermaidModule) return _mermaidModule
  if (!_mermaidPromise) {
    _mermaidPromise = import('mermaid').then((mod) => {
      _mermaidModule = mod.default
      ensureMermaidInit(_mermaidModule)
      return _mermaidModule
    })
  }
  return _mermaidPromise
}

export interface MarkdownRendererProps {
  markdown: string
  className?: string
  renderMermaid?: boolean
}

// react-markdown wrapper:GFM 表格 + 原始 HTML(rehype-raw,经 rehype-sanitize 白名单清洗) +
// mermaid 自渲(svg 经 DOMPurify SVG profile 清洗)。
// IMPLEMENTATION_SPEC §16.4。
export function MarkdownRenderer({
  markdown,
  className,
  renderMermaid = true,
}: MarkdownRendererProps) {
  return (
    <div className={cn('prose prose-slate max-w-none', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 顺序关键:rehype-raw 先把 HTML string 解析成 hast,rehype-sanitize 再
        // 按白名单 schema 清洗(去 <script>、<iframe>、on* 等)。倒过来无效。
        rehypePlugins={[rehypeRaw, [rehypeSanitize, sanitizeSchema]]}
        components={{
          code({ className: codeClassName, children, ...props }) {
            const match = /language-(\w+)/.exec(codeClassName ?? '')
            const lang = match?.[1]
            const content = String(children ?? '').replace(/\n$/, '')
            if (lang === 'mermaid') {
              if (!renderMermaid) {
                return <DeferredMermaid />
              }
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

function DeferredMermaid() {
  return (
    <div
      role="status"
      className="my-4 rounded-md border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600"
    >
      图表正在生成,完整输出后自动渲染
    </div>
  )
}

interface MermaidProps {
  code: string
}

function Mermaid({ code }: MermaidProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)
  // 首次加载 mermaid 模块时 loading=true,显 skeleton(避免空白闪烁)
  const [loading, setLoading] = useState<boolean>(_mermaidModule === null)

  useEffect(() => {
    let cancelled = false
    const id = `mermaid-${Math.random().toString(36).slice(2, 10)}`

    // R-16:LLM 输出在不同 mermaid 版本之间常有兼容差异,先 normalize。
    // 失败时再退回原始 code 试一次(防 normalize 误改)。
    const { code: normalized } = normalizeMermaidSource(code)

    ;(async () => {
      try {
        const m = await getMermaid()
        if (cancelled) return
        setLoading(false)

        const renderWith = (src: string) => m.render(id, src)

        try {
          const { svg } = await renderWith(normalized)
          if (cancelled || !ref.current) return
          ref.current.innerHTML = sanitizeMermaidSvg(svg)
          setError(null)
        } catch (firstErr: unknown) {
          if (cancelled) return
          // 第二次:用原始 code(防 normalize 把本来 OK 的代码改坏)
          if (normalized !== code) {
            try {
              const { svg } = await renderWith(code)
              if (cancelled || !ref.current) return
              ref.current.innerHTML = sanitizeMermaidSvg(svg)
              setError(null)
              return
            } catch {
              // 落到下面统一 fallback
            }
          }
          const msg =
            firstErr instanceof Error ? firstErr.message : String(firstErr)
          setError(msg)
        }
      } catch (loadErr: unknown) {
        if (cancelled) return
        setLoading(false)
        const msg =
          loadErr instanceof Error ? loadErr.message : String(loadErr)
        setError(`mermaid 模块加载失败: ${msg}`)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [code])

  if (loading) {
    // 首次 mermaid lazy chunk 加载期间的 skeleton。同步 mount 时(已加载完)
    // 直接进 useEffect 渲染,不会闪。
    return (
      <div
        className="my-4 flex h-32 animate-pulse items-center justify-center rounded-md border border-slate-200 bg-slate-50 text-xs text-slate-400"
        role="status"
        aria-label="加载 mermaid 渲染器"
      >
        加载图表渲染器…
      </div>
    )
  }

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
