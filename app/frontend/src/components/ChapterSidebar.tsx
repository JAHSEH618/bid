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
  not_generated: '未生成',
}

const STATUS_VARIANT: Record<
  ChapterStatus,
  'secondary' | 'info' | 'warning' | 'success' | 'outline' | 'destructive' | 'muted'
> = {
  pending: 'muted',
  generating: 'info',
  awaiting_review: 'warning',
  reviewing: 'info',
  approved: 'success',
  skipped: 'outline',
  failed: 'destructive',
  retrying: 'info',
  not_generated: 'outline',
}

const ACTIVE_STATUSES = new Set<ChapterStatus>([
  'generating',
  'reviewing',
  'retrying',
  'awaiting_review',
])

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
      <p className="mt-1 text-xs text-muted-foreground">提纲未生成</p>
    )
  }
  const approved = chapters.filter((c) => c.status === 'approved').length
  const skipped = chapters.filter((c) => c.status === 'skipped').length
  const failed = chapters.filter((c) => c.status === 'failed').length
  const done = approved + skipped
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <div className="mt-2 space-y-2">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">
          <span className="font-semibold text-foreground tabular-nums">
            {done}
          </span>
          <span className="text-muted-foreground">/{total}</span> 章已审
        </span>
        <span className="font-semibold tabular-nums text-foreground">
          {pct}%
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-primary to-primary/80 transition-[width] duration-300 ease-out motion-reduce:transition-none"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex flex-wrap gap-1">
        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-1.5 py-0.5 text-[10px] text-emerald-700 ring-1 ring-inset ring-emerald-200">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          通过 {approved}
        </span>
        {skipped > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60" />
            跳过 {skipped}
          </span>
        )}
        {failed > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-1.5 py-0.5 text-[10px] text-destructive ring-1 ring-inset ring-destructive/20">
            <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-destructive" />
            失败 {failed}
          </span>
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
    <aside className="flex h-full flex-col border-r border-border/70 bg-muted/30">
      <div className="border-b border-border/70 bg-background px-4 py-3">
        <h2 className="text-sm font-semibold tracking-tight">章节列表</h2>
        <ChapterSummary chapters={chapters} />
      </div>
      <ul className="flex-1 overflow-y-auto py-1">
        {chapters.map((ch) => {
          const active = ch.index === currentIndex
          const isActive = ACTIVE_STATUSES.has(ch.status)
          const statusLabel =
            ch.status === 'pending' && ch.final_text
              ? '正文已生成'
              : STATUS_LABEL[ch.status]
          return (
            <li key={ch.id}>
              <button
                type="button"
                onClick={() => onSelect?.(ch.index)}
                className={cn(
                  'group relative flex w-full items-start gap-3 px-4 py-2.5 text-left text-sm transition-[background-color,box-shadow,color] duration-150',
                  'hover:bg-accent/60',
                  active &&
                    'bg-background shadow-sm before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-primary',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded text-xs font-medium tabular-nums transition-colors',
                    active
                      ? 'bg-primary text-primary-foreground'
                      : ch.status === 'approved'
                        ? 'bg-emerald-100 text-emerald-700'
                        : ch.status === 'failed'
                          ? 'bg-destructive/10 text-destructive'
                          : 'bg-muted text-muted-foreground',
                  )}
                >
                  {ch.index + 1}
                </span>
                <span className="min-w-0 flex-1 leading-snug">
                  <span
                    className={cn(
                      'line-clamp-2',
                      active
                        ? 'font-semibold text-foreground'
                        : 'font-medium text-foreground/90',
                    )}
                  >
                    {ch.title || `第 ${ch.index + 1} 章`}
                  </span>
                  <span className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant={STATUS_VARIANT[ch.status]}
                      className="text-[10px]"
                    >
                      {isActive && (
                        <span
                          aria-hidden
                          className="inline-block h-1 w-1 animate-pulse-soft rounded-full bg-current"
                        />
                      )}
                      {statusLabel}
                    </Badge>
                    {ch.retry_count != null && ch.retry_count > 0 && (
                      <span className="text-[10px] text-muted-foreground">
                        × {ch.retry_count}
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
