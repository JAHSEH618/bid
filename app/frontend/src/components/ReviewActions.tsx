import { useState } from 'react'
import {
  AlertCircle,
  Check,
  Loader2,
  RefreshCw,
  RotateCcw,
  SkipForward,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { ChapterStatus, ReviewDecision } from '@/lib/types'

export interface ReviewActionsProps {
  status: ChapterStatus | undefined
  onReview?: (decision: ReviewDecision, feedback?: string) => Promise<void>
  onRetry?: () => Promise<void>
  className?: string
}

// REQUIREMENTS P5 + FR-4.7:
// awaiting_review → 三按钮启用;writing/reviewing/retrying → 禁用;failed → retry 按钮单独显示。
export function ReviewActions({
  status,
  onReview,
  onRetry,
  className,
}: ReviewActionsProps) {
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState<ReviewDecision | 'retry' | null>(null)

  const canReview = status === 'awaiting_review'
  const canRetry = status === 'failed'
  const writing =
    status === 'generating' ||
    status === 'pending' ||
    status === 'reviewing' ||
    status === 'retrying'

  const handle = async (decision: ReviewDecision) => {
    if (!onReview || !canReview || busy) return
    if (decision === 'revise' && !feedback.trim()) {
      return
    }
    setBusy(decision)
    try {
      await onReview(decision, decision === 'revise' ? feedback.trim() : undefined)
      if (decision === 'revise') setFeedback('')
    } finally {
      setBusy(null)
    }
  }

  const handleRetry = async () => {
    if (!onRetry || !canRetry || busy) return
    setBusy('retry')
    try {
      await onRetry()
    } finally {
      setBusy(null)
    }
  }

  if (canRetry) {
    return (
      <div
        className={cn(
          'flex items-center justify-between gap-3 border-t border-destructive/20 bg-destructive/5 px-6 py-3.5',
          className,
        )}
      >
        <p className="flex items-center gap-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4" />
          本章生成失败,可点击重试
        </p>
        <Button
          variant="destructive"
          onClick={handleRetry}
          disabled={busy !== null}
        >
          {busy === 'retry' ? (
            <>
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              重试中…
            </>
          ) : (
            <>
              <RotateCcw className="mr-1.5 h-4 w-4" />
              重新生成本章
            </>
          )}
        </Button>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'space-y-3 border-t bg-card px-6 py-4 shadow-[0_-4px_12px_-8px_rgba(15,23,42,0.08)]',
        className,
      )}
    >
      {/* 反馈输入框,审核态可用 */}
      <div className="space-y-1.5">
        <label
          htmlFor="review-feedback"
          className="flex items-center justify-between text-xs"
        >
          <span className="font-medium text-foreground/80">
            修改建议 <span className="text-muted-foreground">(选「不通过」时必填)</span>
          </span>
          <span className="tabular-nums text-muted-foreground">
            {feedback.length} 字
          </span>
        </label>
        <Textarea
          id="review-feedback"
          name="review-feedback"
          placeholder="例:第三段技术架构描述过于宽泛,请补充具体技术栈与版本号…"
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          disabled={!canReview || busy !== null}
          rows={2}
        />
      </div>

      <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span className="text-xs text-muted-foreground">
          {status === 'pending'
            ? '章节等待生成,请先确认正文模型'
            : writing
            ? '章节生成中,请等待…'
            : canReview
              ? '请审核内容后选择 通过 / 不通过 / 跳过'
              : '当前章节无需审核'}
        </span>
        <div className="grid grid-cols-3 gap-2 sm:flex sm:items-center sm:gap-2">
          <Button
            variant="outline"
            onClick={() => handle('skip')}
            disabled={!canReview || busy !== null}
          >
            <SkipForward className="mr-1.5 h-4 w-4" />
            {busy === 'skip' ? '提交中…' : '跳过'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => handle('revise')}
            disabled={!canReview || busy !== null || !feedback.trim()}
            title={!feedback.trim() ? '需要先填写修改建议' : undefined}
          >
            <RefreshCw className="mr-1.5 h-4 w-4" />
            {busy === 'revise' ? '提交中…' : '不通过 · 重写'}
          </Button>
          <Button
            variant="success"
            onClick={() => handle('approve')}
            disabled={!canReview || busy !== null}
            className="shadow-md shadow-emerald-600/15"
          >
            <Check className="mr-1.5 h-4 w-4" />
            {busy === 'approve' ? '提交中…' : '通过'}
          </Button>
        </div>
      </div>
    </div>
  )
}
