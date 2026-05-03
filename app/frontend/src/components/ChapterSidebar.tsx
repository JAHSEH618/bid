import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { ChapterStatus, OutlineChapterDTO } from '@/lib/types'

const STATUS_LABEL: Record<ChapterStatus, string> = {
  pending: '待生成',
  generating: '生成中',
  awaiting_review: '待审核',
  reviewing: '审核中',
  approved: '已通过',
  skipped: '已跳过',
  failed: '失败',
  retrying: '重试中',
}

const STATUS_VARIANT: Record<
  ChapterStatus,
  'secondary' | 'info' | 'warning' | 'success' | 'outline' | 'destructive'
> = {
  pending: 'secondary',
  generating: 'info',
  awaiting_review: 'warning',
  reviewing: 'info',
  approved: 'success',
  skipped: 'outline',
  failed: 'destructive',
  retrying: 'info',
}

// retry_count 不在 OutlineChapterDTO 中,通过 derivedRetry 可选透传(M1 后端
// /outline 不返回 retry_count;前端只在 mock 或 future schema 拿到时显示)。
export interface ChapterSidebarItem extends OutlineChapterDTO {
  retry_count?: number
}

export interface ChapterSidebarProps {
  chapters: ChapterSidebarItem[]
  currentIndex: number
  onSelect?: (index: number) => void
}

// R-12 进度感知:侧栏顶部展示「已通过 / 跳过 / 失败 / 总数」+ 进度条。
function ChapterSummary({ chapters }: { chapters: ChapterSidebarItem[] }) {
  const total = chapters.length
  if (total === 0) {
    return (
      <p className="mt-0.5 text-xs text-muted-foreground">
        提纲未生成
      </p>
    )
  }
  const approved = chapters.filter((c) => c.status === 'approved').length
  const skipped = chapters.filter((c) => c.status === 'skipped').length
  const failed = chapters.filter((c) => c.status === 'failed').length
  const done = approved + skipped
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div className="mt-1.5 space-y-1.5">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>
          {done}/{total} 章已审
        </span>
        <span className="tabular-nums">{pct}%</span>
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex flex-wrap gap-1.5 text-[10px]">
        <span className="text-emerald-700">通过 {approved}</span>
        {skipped > 0 && (
          <span className="text-muted-foreground">跳过 {skipped}</span>
        )}
        {failed > 0 && (
          <span className="text-destructive">失败 {failed}</span>
        )}
      </div>
    </div>
  )
}

// 章节列表侧栏。REQUIREMENTS P5:章节列表 + 状态徽章(含 failed 红标)。
export function ChapterSidebar({
  chapters,
  currentIndex,
  onSelect,
}: ChapterSidebarProps) {
  return (
    <aside className="flex h-full flex-col border-r bg-muted/30">
      <div className="border-b px-4 py-3">
        <h2 className="text-sm font-semibold">章节列表</h2>
        <ChapterSummary chapters={chapters} />
      </div>
      <ul className="flex-1 overflow-y-auto py-1">
        {chapters.map((ch) => {
          const active = ch.index === currentIndex
          return (
            <li key={ch.id}>
              <button
                type="button"
                onClick={() => onSelect?.(ch.index)}
                className={cn(
                  'group flex w-full items-start gap-3 px-4 py-2.5 text-left text-sm transition-colors',
                  'hover:bg-accent/60',
                  active && 'bg-accent',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded text-xs font-medium',
                    active
                      ? 'bg-primary text-primary-foreground'
                      : 'bg-muted text-muted-foreground',
                  )}
                >
                  {ch.index + 1}
                </span>
                <span className="flex-1 leading-snug">
                  <span className="line-clamp-2 font-medium text-foreground">
                    {ch.title || `第 ${ch.index + 1} 章`}
                  </span>
                  <span className="mt-1 flex items-center gap-1.5">
                    <Badge
                      variant={STATUS_VARIANT[ch.status]}
                      className="text-[10px]"
                    >
                      {STATUS_LABEL[ch.status]}
                    </Badge>
                    {ch.retry_count != null && ch.retry_count > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        × {ch.retry_count} 次
                      </span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
