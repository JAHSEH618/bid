import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, History } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChapterSidebar } from '@/components/ChapterSidebar'
import { ChapterPreview } from '@/components/ChapterPreview'
import { ReviewActions } from '@/components/ReviewActions'
import { useProject, useProjectOutline } from '@/api/projects'
import { useReviewChapter, useRetryChapter } from '@/api/chapters'
import { useProjectStream, type ProjectEvent } from '@/hooks/useSSE'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'
import type { ReviewDecision } from '@/lib/types'

export function ChapterReviewPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const project = useProject(projectId)
  const outline = useProjectOutline(projectId)
  const review = useReviewChapter()
  const retry = useRetryChapter()
  const { toast } = useToast()

  const chapters = outline.data?.chapters ?? []

  // 当前章节:首选第一个 awaiting_review;否则首个非 approved/skipped;否则 0。
  const [activeIndex, setActiveIndex] = useState<number>(0)
  useEffect(() => {
    if (!outline.data) return
    const awaiting = chapters.find((c) => c.status === 'awaiting_review')
    if (awaiting) {
      setActiveIndex(awaiting.index)
      return
    }
    const inflight = chapters.find(
      (c) =>
        c.status !== 'approved' && c.status !== 'skipped' && c.status !== 'failed',
    )
    if (inflight) setActiveIndex(inflight.index)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outline.data?.run_id])

  // 流式 token 缓冲。chapter_index !== activeIndex 时不显示。
  // awaiting_review 事件携带 chapter_text(完整正文),用作落地 markdown。
  const [streaming, setStreaming] = useState<{ index: number; text: string }>({
    index: -1,
    text: '',
  })
  const [readyText, setReadyText] = useState<Record<number, string>>({})

  useProjectStream(projectId, (e: ProjectEvent) => {
    if (e.type === 'chapter_started' || e.type === 'chapter_picked') {
      setStreaming({ index: e.chapter_index ?? -1, text: '' })
      project.refetch()
      outline.refetch()
    } else if (e.type === 'chapter_token' && e.chapter_index === activeIndex) {
      setStreaming((prev) =>
        prev.index === e.chapter_index
          ? { ...prev, text: prev.text + (e.delta ?? '') }
          : { index: e.chapter_index ?? -1, text: e.delta ?? '' },
      )
    } else if (e.type === 'awaiting_review') {
      // backend 在 awaiting_review payload 里附 chapter_text(M1-8 spec)
      const idx = e.chapter_index ?? -1
      if (e.chapter_text) {
        setReadyText((prev) => ({ ...prev, [idx]: e.chapter_text! }))
      }
      setStreaming({ index: -1, text: '' })
      outline.refetch()
      toast({
        title: `第 ${idx + 1} 章待审核`,
        variant: 'info',
      })
    } else if (
      e.type === 'chapter_approved' ||
      e.type === 'chapter_skipped' ||
      e.type === 'chapter_max_retry_skip'
    ) {
      setStreaming({ index: -1, text: '' })
      outline.refetch()
    } else if (e.type === 'chapter_failed') {
      setStreaming({ index: -1, text: '' })
      outline.refetch()
      toast({
        title: `第 ${(e.chapter_index ?? 0) + 1} 章生成失败`,
        variant: 'destructive',
      })
    } else if (e.type === 'chapter_visuals_ready') {
      // 可视化建议已就绪,这里不做特殊处理(章节正文流仍由 awaiting_review 触发)
    } else if (e.type === 'proposal_ready') {
      project.refetch()
      toast({ title: '全文已生成', variant: 'success' })
    } else if (e.type === 'error') {
      toast({ title: '工作流错误', variant: 'destructive' })
    }
  })

  const activeChapter = chapters.find((c) => c.index === activeIndex)
  const isStreaming =
    streaming.index === activeIndex && streaming.text.length > 0
  const previewMarkdown =
    isStreaming && streaming.index === activeIndex
      ? streaming.text
      : (readyText[activeIndex] ?? '')

  const submitReview = async (
    decision: ReviewDecision,
    feedback?: string,
  ) => {
    if (!projectId || !activeChapter) return
    try {
      await review.mutateAsync({
        projectId,
        index: activeChapter.index,
        body: { decision, feedback },
      })
      toast({
        title:
          decision === 'approve'
            ? '已通过,继续下一章'
            : decision === 'skip'
              ? '已跳过'
              : '已提交修改建议',
        variant: 'success',
      })
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '提交失败')
          : '提交失败'
      toast({ title: '提交失败', description: msg, variant: 'destructive' })
    }
  }

  const submitRetry = async () => {
    if (!projectId || !activeChapter) return
    try {
      await retry.mutateAsync({
        projectId,
        index: activeChapter.index,
      })
      toast({ title: '已触发重试', variant: 'success' })
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '重试失败')
          : '重试失败'
      toast({ title: '重试失败', description: msg, variant: 'destructive' })
    }
  }

  if (project.isLoading || outline.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (!project.data) {
    return (
      <div className="container py-12 text-sm text-destructive">
        项目不存在或无访问权限
      </div>
    )
  }

  return (
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-[300px_1fr]">
      <ChapterSidebar
        chapters={chapters}
        currentIndex={activeIndex}
        onSelect={setActiveIndex}
      />
      <main className="flex min-h-0 flex-col">
        <div className="flex items-center justify-between border-b bg-background px-6 py-3">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/">
                <ArrowLeft className="mr-1 h-4 w-4" />
                列表
              </Link>
            </Button>
            <div>
              <h1 className="text-base font-semibold leading-tight">
                {project.data.name}
              </h1>
              <p className="text-xs text-muted-foreground">
                第 {activeIndex + 1} 章 / {chapters.length}
                {activeChapter && (
                  <>
                    {' · '}
                    {activeChapter.title}
                  </>
                )}
              </p>
            </div>
          </div>
          {project.data.status === 'done' && (
            <Button asChild size="sm">
              <Link to={`/projects/${projectId}/proposal`}>查看全文</Link>
            </Button>
          )}
        </div>

        <div className="flex-1 overflow-auto px-6 py-6">
          {activeChapter ? (
            <Tabs defaultValue="current" className="space-y-4">
              <TabsList>
                <TabsTrigger value="current">当前内容</TabsTrigger>
                <TabsTrigger value="versions">
                  <History className="mr-1 h-3.5 w-3.5" />
                  历史版本
                </TabsTrigger>
              </TabsList>
              <TabsContent value="current">
                <ChapterPreview
                  markdown={previewMarkdown}
                  isStreaming={isStreaming}
                />
              </TabsContent>
              <TabsContent value="versions">
                <p className="text-sm text-muted-foreground">
                  本期后端尚未提供历史版本端点。重写后请等待新内容自然涌入。
                </p>
              </TabsContent>
            </Tabs>
          ) : (
            <p className="text-sm text-muted-foreground">章节尚未就绪。</p>
          )}
        </div>

        <ReviewActions
          status={activeChapter?.status}
          onReview={submitReview}
          onRetry={submitRetry}
        />
      </main>
    </div>
  )
}
