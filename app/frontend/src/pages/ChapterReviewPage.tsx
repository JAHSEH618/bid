import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Bot, FileCheck, History, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChapterSidebar } from '@/components/ChapterSidebar'
import { ChapterPreview } from '@/components/ChapterPreview'
import { ReviewActions } from '@/components/ReviewActions'
import { useProject, useProjectOutline } from '@/api/projects'
import {
  useChapter,
  useReviewChapter,
  useRetryChapter,
  useSetChapterModel,
} from '@/api/chapters'
import { useModelConfig } from '@/api/me'
import { useQueryClient } from '@tanstack/react-query'
import { useProjectStream, type ProjectEvent } from '@/hooks/useSSE'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import type { ChapterStatus, ReviewDecision } from '@/lib/types'

export function ChapterReviewPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const project = useProject(projectId)
  const outline = useProjectOutline(projectId)
  const review = useReviewChapter()
  const retry = useRetryChapter()
  const setChapterModel = useSetChapterModel()
  const modelConfig = useModelConfig()
  const { toast } = useToast()
  const qc = useQueryClient()

  const chapters = outline.data?.chapters ?? []

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

  // R-15:active chapter 详情走 GET /api/projects/{id}/chapters/{idx}。
  // useChapter 内部 refetchInterval 智能策略:generating/retrying/reviewing
  // → 2s polling(R-14 backend 每 1s flush partial),终态停轮询。
  const chapterDetail = useChapter(projectId, activeIndex)
  const detail = chapterDetail.data
  const finalTextDb = detail?.final_text ?? ''

  // SSE 增量 buffer 与 awaiting_review 事件携带的完整版缓存。
  // 显示优先级单调增不变量:刷新/切页后看到的内容 ≥ 之前看到的内容。
  const [streaming, setStreaming] = useState<{ index: number; text: string }>({
    index: -1,
    text: '',
  })
  const [readyText, setReadyText] = useState<Record<number, string>>({})

  useProjectStream(projectId, (e: ProjectEvent) => {
    if (e.type === 'chapter_started' || e.type === 'chapter_picked') {
      // 流开始:buffer 用 DB 已有 partial 作种子(若已 hydrate)
      const idx = e.chapter_index ?? -1
      const seed = idx === activeIndex ? finalTextDb : ''
      setStreaming({ index: idx, text: seed })
      project.refetch()
      outline.refetch()
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', idx],
      })
    } else if (e.type === 'chapter_token' && e.chapter_index === activeIndex) {
      setStreaming((prev) =>
        prev.index === e.chapter_index
          ? { ...prev, text: prev.text + (e.delta ?? '') }
          : { index: e.chapter_index ?? -1, text: e.delta ?? '' },
      )
    } else if (e.type === 'awaiting_review') {
      const idx = e.chapter_index ?? -1
      if (e.chapter_text) {
        setReadyText((prev) => ({ ...prev, [idx]: e.chapter_text! }))
      }
      setStreaming({ index: -1, text: '' })
      outline.refetch()
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', idx],
      })
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
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters'],
      })
    } else if (e.type === 'chapter_failed') {
      setStreaming({ index: -1, text: '' })
      outline.refetch()
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters'],
      })
      toast({
        title: `第 ${(e.chapter_index ?? 0) + 1} 章生成失败`,
        variant: 'destructive',
      })
    } else if (e.type === 'chapter_visuals_ready') {
      // 可视化建议已就绪,这里不做特殊处理
    } else if (e.type === 'proposal_ready') {
      project.refetch()
      toast({ title: '全文已生成', variant: 'success' })
    } else if (e.type === 'error') {
      toast({ title: '工作流错误', variant: 'destructive' })
    }
  })

  const activeChapter = chapters.find((c) => c.index === activeIndex)
  const activeChapterModel =
    detail?.chapter_model ?? activeChapter?.chapter_model ?? ''
  const [selectedModel, setSelectedModel] = useState('')

  useEffect(() => {
    setSelectedModel(activeChapterModel)
  }, [activeChapterModel, activeIndex])

  const submitModelChange = async (model: string) => {
    if (!projectId || !activeChapter) return
    setSelectedModel(model)
    try {
      await setChapterModel.mutateAsync({
        projectId,
        index: activeChapter.index,
        body: { chapter_model: model || null },
      })
      toast({ title: '本章正文模型已保存', variant: 'success' })
    } catch (err) {
      setSelectedModel(activeChapterModel)
      toast({
        title: '模型保存失败',
        description: readApiError(err, '模型保存失败'),
        variant: 'destructive',
      })
    }
  }

  // 显示优先级(单调增):
  //   1. SSE 实时 buffer(active streaming 期长度持续上升)
  //   2. SSE awaiting_review 事件携带的 chapter_text 完整版
  //   3. DB persisted final_text(R-14 周期 flush;hydrate 路径)
  // 三者取最长,防 polling 中途 buffer 临时短于 DB 快照导致闪烁。
  const isStreaming =
    streaming.index === activeIndex && streaming.text.length > 0
  const candidates = [
    isStreaming ? streaming.text : '',
    readyText[activeIndex] ?? '',
    finalTextDb,
  ]
  const previewMarkdown = candidates.reduce(
    (longest, cur) => (cur.length > longest.length ? cur : longest),
    '',
  )

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
      toast({
        title: '提交失败',
        description: readApiError(err, '提交失败'),
        variant: 'destructive',
      })
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
      toast({
        title: '重试失败',
        description: readApiError(err, '重试失败'),
        variant: 'destructive',
      })
    }
  }

  if (project.isLoading || outline.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div
          role="status"
          aria-live="polite"
          className="flex flex-col items-center gap-2 text-sm text-muted-foreground"
        >
          <Loader2 aria-hidden="true" className="h-5 w-5 animate-spin" />
          加载中…
        </div>
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
    <div className="grid h-[calc(100vh-3.5rem)] grid-cols-1 md:grid-cols-[300px_1fr]">
      <div className="hidden md:block">
        <ChapterSidebar
          chapters={chapters}
          currentIndex={activeIndex}
          onSelect={setActiveIndex}
        />
      </div>
      <main className="flex min-h-0 flex-col">
        <div className="flex items-center justify-between gap-3 border-b bg-background/95 px-6 py-3 backdrop-blur">
          <div className="flex min-w-0 items-center gap-3">
            <Button variant="ghost" size="sm" asChild>
              <Link to="/">
                <ArrowLeft className="mr-1 h-4 w-4" />
                列表
              </Link>
            </Button>
            <div className="min-w-0">
              <h1 className="truncate text-base font-semibold leading-tight">
                {project.data.name}
              </h1>
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                第 {activeIndex + 1} 章 / {chapters.length}
                {activeChapter && (
                  <>
                    <span className="mx-1.5 text-muted-foreground/40">·</span>
                    <span className="font-medium text-foreground/80">
                      {activeChapter.title}
                    </span>
                  </>
                )}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {activeChapter && (
              <ChapterModelPicker
                value={selectedModel}
                currentModel={activeChapterModel}
                models={modelConfig.data?.available_models ?? []}
                fallback={modelConfig.data?.default_chapter_model ?? ''}
                status={activeChapter.status}
                loading={modelConfig.isLoading || setChapterModel.isPending}
                onChange={submitModelChange}
              />
            )}
            {project.data.status === 'done' && (
              <Button asChild size="sm" className="shadow-sm">
                <Link to={`/projects/${projectId}/proposal`}>
                  <FileCheck className="mr-1.5 h-4 w-4" />
                  查看全文
                </Link>
              </Button>
            )}
          </div>
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
                {previewMarkdown ? (
                  <ChapterPreview
                    markdown={previewMarkdown}
                    isStreaming={isStreaming}
                  />
                ) : (
                  <ChapterEmptyHint status={activeChapter.status} />
                )}
              </TabsContent>
              <TabsContent value="versions">
                <Card>
                  本期后端尚未提供历史版本端点。重写后请等待新内容自然涌入
                </Card>
              </TabsContent>
            </Tabs>
          ) : (
            <p className="text-sm text-muted-foreground">章节尚未就绪</p>
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

// 简化的占位 Card 标签(只在 versions tab 用)
function Card({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

function ChapterModelPicker({
  value,
  currentModel,
  models,
  fallback,
  status,
  loading,
  onChange,
}: {
  value: string
  currentModel: string
  models: string[]
  fallback: string
  status: ChapterStatus
  loading: boolean
  onChange: (model: string) => Promise<void>
}) {
  const canChange =
    status === 'pending' || status === 'awaiting_review' || status === 'failed'
  const options = uniqueModels([
    currentModel,
    ...models,
  ]).filter(Boolean)
  const selected =
    value && options.includes(value)
      ? value
      : currentModel && options.includes(currentModel)
        ? currentModel
        : fallback && options.includes(fallback)
          ? fallback
          : options[0] || ''

  return (
    <label className="hidden items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-xs shadow-sm lg:flex">
      <Bot className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
      <span className="whitespace-nowrap text-muted-foreground">正文模型</span>
      <select
        value={selected}
        onChange={(e) => void onChange(e.target.value)}
        disabled={!canChange || loading || options.length === 0}
        className="h-7 w-[260px] rounded border border-input bg-background px-2 font-mono text-[11px] text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
        aria-label="选择本章正文生成模型"
        title={
          canChange
            ? '选择本章正文生成模型'
            : '章节正在处理或已结束,不能修改模型'
        }
      >
        {options.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
    </label>
  )
}

function uniqueModels(models: string[]) {
  const seen = new Set<string>()
  return models.filter((model) => {
    const normalized = model.trim()
    if (!normalized || seen.has(normalized)) return false
    seen.add(normalized)
    return true
  })
}

// 当前 chapter 没有可显示的 markdown 时,根据状态给用户合适的提示。
function ChapterEmptyHint({ status }: { status: ChapterStatus }) {
  const base =
    'flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-muted/30 py-16 text-center'

  if (status === 'pending') {
    return (
      <div className={base}>
        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted">
          <span className="text-lg">⏳</span>
        </span>
        <p className="text-sm font-medium text-foreground">等待生成</p>
        <p className="max-w-xs text-xs text-muted-foreground">
          本章尚未轮到生成,请等待前序章节完成
        </p>
      </div>
    )
  }
  if (status === 'generating' || status === 'retrying') {
    return (
      <div role="status" aria-live="polite" className={base}>
        <Loader2 aria-hidden="true" className="h-6 w-6 animate-spin text-primary" />
        <p className="text-sm font-medium text-foreground">
          {status === 'retrying' ? 'AI 正在重新生成…' : 'AI 正在生成本章…'}
        </p>
        <p className="max-w-xs text-xs text-muted-foreground">
          长时间无内容涌入,可刷新页面重新建立 SSE 连接
        </p>
      </div>
    )
  }
  if (status === 'reviewing') {
    return (
      <div role="status" aria-live="polite" className={base}>
        <Loader2 aria-hidden="true" className="h-6 w-6 animate-spin text-primary" />
        <p className="text-sm font-medium text-foreground">
          审核已提交,后端处理中…
        </p>
      </div>
    )
  }
  if (status === 'approved' || status === 'skipped') {
    return (
      <div className={base}>
        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
          ✓
        </span>
        <p className="text-sm font-medium text-foreground">
          {status === 'approved' ? '已通过' : '已跳过'}
        </p>
        <p className="max-w-sm text-xs text-muted-foreground">
          章节正文已并入全文。本期后端不暴露已审章节的历史正文,可前往「全文」页查看完整结果
        </p>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className={base.replace('border-dashed', '').replace('bg-muted/30', 'bg-destructive/5 border-destructive/30')}>
        <span className="flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          ✕
        </span>
        <p className="text-sm font-medium text-destructive">本章生成失败</p>
        <p className="max-w-xs text-xs text-muted-foreground">
          请点击下方「重新生成本章」尝试重试
        </p>
      </div>
    )
  }
  return (
    <div className={base}>
      <p className="text-sm text-muted-foreground">章节尚未就绪</p>
    </div>
  )
}
