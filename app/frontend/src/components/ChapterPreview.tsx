import { Sparkles } from 'lucide-react'
import { MarkdownRenderer } from '@/lib/markdown'
import { cn } from '@/lib/utils'

export interface ChapterPreviewProps {
  markdown: string
  isStreaming?: boolean
}

// 单章预览,接 ChapterReviewPage 主区。
// 流式期间末尾加一个细长光标 + 顶部"AI 正在生成"标识。
export function ChapterPreview({ markdown, isStreaming }: ChapterPreviewProps) {
  if (!markdown) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex h-full min-h-[200px] items-center justify-center rounded-xl border border-dashed bg-muted/30 text-sm text-muted-foreground"
      >
        {isStreaming ? '正在生成…' : '暂无内容'}
      </div>
    )
  }
  return (
    <div className="space-y-3">
      {isStreaming && (
        <div
          role="status"
          aria-live="polite"
          className="sticky top-0 z-10 flex items-center gap-2 rounded-md border border-primary/20 bg-primary/[0.04] px-3 py-1.5 text-xs font-medium text-primary backdrop-blur"
        >
          <Sparkles
            aria-hidden="true"
            className="h-3.5 w-3.5 animate-pulse-soft"
          />
          AI 正在生成本章内容…
        </div>
      )}
      <div className="rounded-xl border border-border/70 bg-card px-6 py-5 shadow-sm">
        <div className="relative">
          <MarkdownRenderer markdown={markdown} />
          {isStreaming && (
            <span
              aria-hidden="true"
              className={cn(
                'caret-blink ml-0.5 inline-block h-[1.1em] w-[2px] translate-y-1 align-middle bg-primary',
              )}
            />
          )}
        </div>
      </div>
    </div>
  )
}
