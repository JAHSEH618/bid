import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, History } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { ChapterSidebar } from '@/components/ChapterSidebar'
import { ChapterPreview } from '@/components/ChapterPreview'
import { ReviewActions } from '@/components/ReviewActions'
import { MarkdownRenderer } from '@/lib/markdown'
import { useProjectDetail } from '@/api/projects'
import { useChapterVersions, useReviewChapter, useRetryChapter } from '@/api/chapters'
import { useProjectStream, type ProjectEvent } from '@/hooks/useSSE'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'
import type { ReviewDecision } from '@/lib/types'

export function ChapterReviewPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const detail = useProjectDetail(projectId)
  const review = useReviewChapter()
  const retry = useRetryChapter()
  const { toast } = useToast()

  const project = detail.data?.project
  const chapters = detail.data?.chapters ?? []

  // 当前章节:优先看后端 current_index;允许用户在侧栏点击切换。
  const [activeIndex, setActiveIndex] = useState<number>(0)
  useEffect(() => {
    if (detail.data) {
      setActiveIndex(detail.data.current_index)
    }
  }, [detail.data])

  // 流式 token 缓冲。chapter_index !== activeIndex 时不显示。
  const [streaming, setStreaming] = useState<{ index: number; text: string }>({
    index: -1,
    text: '',
  })

  useProjectStream(projectId, (e: ProjectEvent) => {
    if (e.type === 'chapter_started') {
      setStreaming({ index: e.chapter_index ?? -1, text: '' })
      detail.refetch()
    } else if (e.type === 'chapter_token' && e.chapter_index === activeIndex) {
      setStreaming((prev) =>
        prev.index === e.chapter_index
          ? { ...prev, text: prev.text + (e.delta ?? '') }
          : { index: e.chapter_index ?? -1, text: e.delta ?? '' },
      )
    } else if (
      e.type === 'chapter_ready' ||
      e.type === 'awaiting_review' ||
      e.type === 'chapter_approved' ||
      e.type === 'chapter_skipped' ||
      e.type === 'chapter_failed'
    ) {
      setStreaming({ index: -1, text: '' })
      detail.refetch()
      if (e.type === 'awaiting_review') {
        toast({ title: `第 ${(e.chapter_index ?? 0) + 1} 章待审核`, variant: 'info' })
      }
      if (e.type === 'chapter_failed') {
        toast({
          title: `第 ${(e.chapter_index ?? 0) + 1} 章生成失败`,
          variant: 'destructive',
        })
      }
    } else if (e.type === 'proposal_ready') {
      detail.refetch()
      toast({ title: '全文已生成', variant: 'success' })
    } else if (e.type === 'error') {
      toast({ title: '工作流错误', variant: 'destructive' })
    }
  })

  const activeChapter = chapters.find((c) => c.index === activeIndex)
  const isStreaming = streaming.index === activeIndex && streaming.text.length > 0
  const previewMarkdown =
    isStreaming && streaming.index === activeIndex
      ? streaming.text
      : (activeChapter?.final_text ?? '')

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

  if (detail.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (!project) {
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
                {project.name}
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
          {project.status === 'done' && (
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
                <VersionList
                  projectId={projectId}
                  chapterIndex={activeChapter.index}
                />
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

function VersionList({
  projectId,
  chapterIndex,
}: {
  projectId: number
  chapterIndex: number
}) {
  const versions = useChapterVersions(projectId, chapterIndex)

  if (versions.isLoading) {
    return (
      <p className="text-sm text-muted-foreground">加载历史版本…</p>
    )
  }
  if (!versions.data || versions.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">暂无历史版本。</p>
    )
  }
  return (
    <div className="space-y-3">
      {versions.data.map((v) => (
        <Card key={v.id}>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm">第 {v.version} 版</CardTitle>
            <Badge variant="outline" className="text-[10px]">
              {new Date(v.created_at).toLocaleString('zh-CN')}
            </Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {v.feedback && (
              <p className="rounded-md bg-amber-50 p-3 text-xs text-amber-900">
                <strong className="mr-2">审核反馈:</strong>
                {v.feedback}
              </p>
            )}
            <MarkdownRenderer markdown={v.text} className="text-sm" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
