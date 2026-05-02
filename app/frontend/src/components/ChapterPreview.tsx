import { MarkdownRenderer } from '@/lib/markdown'

export interface ChapterPreviewProps {
  markdown: string
  isStreaming?: boolean
}

// 单章预览,接 ChapterReviewPage 主区。
// IMPLEMENTATION_SPEC §16.4。流式期间末尾加一个光标提示用户内容仍在涌入。
export function ChapterPreview({ markdown, isStreaming }: ChapterPreviewProps) {
  if (!markdown) {
    return (
      <div className="flex h-full min-h-[200px] items-center justify-center text-sm text-muted-foreground">
        {isStreaming ? '正在生成…' : '暂无内容'}
      </div>
    )
  }
  return (
    <div className="relative">
      <MarkdownRenderer markdown={markdown} />
      {isStreaming && (
        <span
          aria-hidden
          className="ml-1 inline-block h-4 w-2 animate-pulse bg-primary align-middle"
        />
      )}
    </div>
  )
}
