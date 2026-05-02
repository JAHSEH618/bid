import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { ChapterStatus, ReviewDecision } from '@/lib/types'

export interface ReviewActionsProps {
  status: ChapterStatus | undefined
  // 提交审核(pass/revise/skip)。具体 API 调用在 ChapterReviewPage 里接,这里只回调。
  onReview?: (decision: ReviewDecision, feedback?: string) => Promise<void>
  // failed 章节由 ChapterReviewPage 监听,点击触发 retry。
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
  const writing = status === 'writing' || status === 'pending'

  const handle = async (decision: ReviewDecision) => {
    if (!onReview || !canReview || busy) return
    if (decision === 'revise' && !feedback.trim()) {
      // 反馈不能空,UI 自检
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
          'flex items-center justify-between gap-3 border-t bg-destructive/5 px-4 py-3',
          className,
        )}
      >
        <p className="text-sm text-destructive">
          本章生成失败,可点击重试。
        </p>
        <Button
          variant="destructive"
          onClick={handleRetry}
          disabled={busy !== null}
        >
          {busy === 'retry' ? '重试中…' : '重新生成本章'}
        </Button>
      </div>
    )
  }

  return (
    <div className={cn('space-y-3 border-t bg-card px-4 py-3', className)}>
      <Textarea
        placeholder="不通过时填写修改建议(让 AI 知道哪里要改)"
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        disabled={!canReview || busy !== null}
        rows={3}
      />
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {writing
            ? '章节生成中,请等待…'
            : canReview
              ? '请审核后选择通过 / 不通过 / 跳过'
              : '当前章节无需审核'}
        </span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => handle('skip')}
            disabled={!canReview || busy !== null}
          >
            {busy === 'skip' ? '提交中…' : '跳过'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => handle('revise')}
            disabled={!canReview || busy !== null || !feedback.trim()}
          >
            {busy === 'revise' ? '提交中…' : '不通过(重写)'}
          </Button>
          <Button
            onClick={() => handle('approve')}
            disabled={!canReview || busy !== null}
          >
            {busy === 'approve' ? '提交中…' : '通过'}
          </Button>
        </div>
      </div>
    </div>
  )
}
