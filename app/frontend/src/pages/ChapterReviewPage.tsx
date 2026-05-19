import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Bot,
  Download,
  FileCheck,
  History,
  Loader2,
  Play,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ChapterSidebar } from '@/components/ChapterSidebar'
import { ChapterPreview } from '@/components/ChapterPreview'
import { ReviewActions } from '@/components/ReviewActions'
import { useProject, useProjectOutline } from '@/api/projects'
import {
  useChapter,
  useChapterVersions,
  useGenerateChapter,
  useReviewChapter,
  useRetryChapter,
  useSetChapterModel,
} from '@/api/chapters'
import {
  downloadChapterDocxUrl,
  useDocxJob,
  useTriggerChapterDocx,
} from '@/api/docx'
import { useModelConfig } from '@/api/me'
import { useQueryClient } from '@tanstack/react-query'
import { useProjectStream, type ProjectEvent } from '@/hooks/useSSE'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import type {
  ChapterReferenceItem,
  ChapterStatus,
  ChapterVersionDTO,
  ReviewDecision,
} from '@/lib/types'

export function ChapterReviewPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const project = useProject(projectId)
  const outline = useProjectOutline(projectId)
  const review = useReviewChapter()
  const retry = useRetryChapter()
  const generate = useGenerateChapter()
  const setChapterModel = useSetChapterModel()
  const modelConfig = useModelConfig()
  const { toast } = useToast()
  const qc = useQueryClient()

  const chapters = outline.data?.chapters ?? []
  const maxChapterGenerations =
    outline.data?.max_concurrent_chapter_generations ?? 3
  const generatingCount = chapters.filter(
    (c) => c.status === 'generating' || c.status === 'retrying',
  ).length

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
  const chapterVersions = useChapterVersions(projectId, activeIndex)
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
      if (idx >= 0) setActiveIndex(idx)
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
      if (idx >= 0) setActiveIndex(idx)
      if (e.chapter_text) {
        setReadyText((prev) => ({ ...prev, [idx]: e.chapter_text! }))
      }
      setStreaming({ index: -1, text: '' })
      outline.refetch()
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', idx],
      })
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', idx, 'versions'],
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
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', e.chapter_index, 'versions'],
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
    } else if (e.type === 'chapter_prefetched') {
      const idx = e.chapter_index ?? -1
      outline.refetch()
      if (idx >= 0) {
        qc.invalidateQueries({
          queryKey: ['projects', projectId, 'chapters', idx],
        })
        qc.invalidateQueries({
          queryKey: ['projects', projectId, 'chapters', idx, 'versions'],
        })
        toast({
          title: `第 ${idx + 1} 章正文已生成`,
          variant: 'success',
        })
      }
    } else if (e.type === 'chapter_ready_to_generate') {
      const idx = e.chapter_index ?? -1
      if (idx >= 0) setActiveIndex(idx)
      outline.refetch()
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'chapters', idx],
      })
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
  const workflowCurrent = chapters.find(
    (c) => c.status !== 'approved' && c.status !== 'skipped',
  )
  const isWorkflowCurrent = workflowCurrent?.index === activeChapter?.index
  const hasGeneratedBody = Boolean(activeChapter?.final_text || finalTextDb)
  const generationLimitReached = generatingCount >= maxChapterGenerations
  const generateButtonLabel = hasGeneratedBody
    ? isWorkflowCurrent
      ? '进入审核'
      : '重新生成正文'
    : '生成正文'
  const generateButtonTitle = generationLimitReached
    ? `生成中章节已达上限 ${generatingCount}/${maxChapterGenerations}`
    : isWorkflowCurrent
      ? '生成当前章节正文并进入审核'
      : '生成所选章节正文缓存'
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

  const submitGenerate = async () => {
    if (!projectId || !activeChapter) return
    try {
      await generate.mutateAsync({
        projectId,
        index: activeChapter.index,
      })
      toast({
        title: '已开始生成正文',
        variant: 'success',
      })
    } catch (err) {
      toast({
        title: '触发生成失败',
        description: readApiError(err, '触发生成失败'),
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
    activeChapter?.final_text ?? '',
    finalTextDb,
  ]
  const previewMarkdown = candidates.reduce(
    (longest, cur) => (cur.length > longest.length ? cur : longest),
    '',
  )

  const submitReview = async (
    decision: ReviewDecision,
    feedback?: string,
    finalizeEarly?: boolean,
  ) => {
    if (!projectId || !activeChapter) return
    try {
      await review.mutateAsync({
        projectId,
        index: activeChapter.index,
        body: {
          decision,
          feedback,
          finalize_early: finalizeEarly,
        },
      })
      toast({
        title: finalizeEarly
          ? '已通过本章,开始合并文档'
          : decision === 'approve'
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
            <span className="hidden rounded-md border border-border bg-muted/40 px-2 py-1 text-xs text-muted-foreground sm:inline-flex">
              生成中 {generatingCount}/{maxChapterGenerations}
            </span>
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
            {activeChapter?.status === 'pending' && (
              <Button
                size="sm"
                onClick={() => void submitGenerate()}
                disabled={
                  generate.isPending ||
                  setChapterModel.isPending ||
                  generationLimitReached
                }
                className="shadow-sm"
                title={generateButtonTitle}
              >
                {generate.isPending ? (
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-1.5 h-4 w-4" />
                )}
                {generateButtonLabel}
              </Button>
            )}
            {/* 只要本章有 final_text 就允许导出 .docx —— 不止 approved。
                后端 _load_chapter_for_export 同步放开,prefetch / awaiting_review
                状态都能导一份留底。approved/skipped 章节正文不再回前端,所以由
                ChapterEmptyHint 的兜底分支负责显示按钮。 */}
            {hasGeneratedBody && chapterDetail.data?.id != null && (
              <ChapterExportButton chapterId={chapterDetail.data.id} />
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
                <TabsTrigger value="references">
                  本章参考资料
                  {chapterDetail.data?.references && chapterDetail.data.references.length > 0 && (
                    <span className="ml-1 text-xs text-muted-foreground">
                      ({chapterDetail.data.references.length})
                    </span>
                  )}
                </TabsTrigger>
              </TabsList>
              <TabsContent value="current">
                {previewMarkdown ? (
                  <ChapterPreview
                    markdown={previewMarkdown}
                    isStreaming={isStreaming}
                  />
                ) : (
                  <ChapterEmptyHint
                    status={activeChapter.status}
                    chapterId={chapterDetail.data?.id ?? null}
                  />
                )}
              </TabsContent>
              <TabsContent value="versions">
                <ChapterVersionsPanel
                  versions={chapterVersions.data ?? []}
                  loading={chapterVersions.isLoading}
                />
              </TabsContent>
              <TabsContent value="references">
                <ChapterReferencesPanel
                  references={chapterDetail.data?.references ?? null}
                />
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
          remainingNotGenerated={chapters.filter(
            (c) =>
              c.index > (activeChapter?.index ?? -1) &&
              (c.status === 'pending' ||
                c.status === 'generating' ||
                c.status === 'awaiting_review'),
          ).length}
        />
      </main>
    </div>
  )
}

function ChapterVersionsPanel({
  versions,
  loading,
}: {
  versions: ChapterVersionDTO[]
  loading: boolean
}) {
  const [selectedId, setSelectedId] = useState<number | null>(null)

  useEffect(() => {
    if (versions.length === 0) {
      setSelectedId(null)
      return
    }
    if (!versions.some((item) => item.id === selectedId)) {
      setSelectedId(versions[0].id)
    }
  }, [selectedId, versions])

  if (loading) {
    return (
      <div
        role="status"
        className="flex min-h-40 items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 text-sm text-muted-foreground"
      >
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        加载历史版本…
      </div>
    )
  }

  if (versions.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
        本章还没有可回溯的历史版本
      </div>
    )
  }

  const selected = versions.find((item) => item.id === selectedId) ?? versions[0]

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      <div className="overflow-hidden rounded-lg border border-border bg-background">
        {versions.map((version) => (
          <button
            key={version.id}
            type="button"
            onClick={() => setSelectedId(version.id)}
            className={`flex w-full flex-col items-start gap-1 border-b border-border px-4 py-3 text-left text-sm last:border-b-0 hover:bg-muted/50 ${
              version.id === selected.id ? 'bg-muted' : ''
            }`}
          >
            <span className="flex w-full items-center justify-between gap-2">
              <span className="font-medium text-foreground">
                版本 {version.version}
              </span>
              {version.abandoned && (
                <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[11px] text-amber-800">
                  已废弃
                </span>
              )}
            </span>
            <span className="text-xs text-muted-foreground">
              {formatVersionTime(version.created_at)}
            </span>
            {version.feedback_in && (
              <span className="line-clamp-2 text-xs text-muted-foreground">
                {version.feedback_in}
              </span>
            )}
          </button>
        ))}
      </div>
      <ChapterPreview markdown={selected.body_markdown} />
    </div>
  )
}

function formatVersionTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
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
    <label className="flex items-center gap-2 rounded-md border border-border bg-background px-2 py-1.5 text-xs shadow-sm">
      <Bot className="h-3.5 w-3.5 text-primary" aria-hidden="true" />
      <span className="whitespace-nowrap text-muted-foreground">正文模型</span>
      <select
        value={selected}
        onChange={(e) => void onChange(e.target.value)}
        disabled={!canChange || loading || options.length === 0}
        className="h-7 w-[180px] rounded border border-input bg-background px-2 font-mono text-[11px] text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 sm:w-[260px]"
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
function ChapterEmptyHint({
  status,
  chapterId,
}: {
  status: ChapterStatus
  chapterId: number | null
}) {
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
          选择或沿用当前正文模型后,点击右上角「生成正文」
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
        {/* PR-M6-2:approved 章节提供单章 .docx 导出。skipped 不显示按钮。 */}
        {status === 'approved' && chapterId != null && (
          <ChapterExportButton chapterId={chapterId} />
        )}
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

// PR-M6-2:approved 章节单击触发后端单章 .docx 生成,完成后弹出下载链接。
// 复用现有 useDocxJob 轮询（chapter scope 共享 docx_jobs 表,job_id 全局唯一）。
function ChapterExportButton({ chapterId }: { chapterId: number }) {
  const trigger = useTriggerChapterDocx()
  const { toast } = useToast()
  const [pollState, setPollState] = useState<{
    projectId: number | null
    jobId: number | null
  }>({ projectId: null, jobId: null })

  const job = useDocxJob(pollState.projectId, pollState.jobId, {
    refetchInterval: (q) => {
      const s = q.state.data?.status
      if (s === 'done' || s === 'failed' || s === 'invalidated') return false
      return 3_000
    },
  })

  useEffect(() => {
    const data = job.data
    if (!data) return
    if (data.status === 'failed') {
      toast({
        variant: 'destructive',
        title: '导出失败',
        description: data.error ?? '后端报告未知错误',
      })
      setPollState({ projectId: null, jobId: null })
    } else if (data.status === 'done') {
      toast({
        variant: 'success',
        title: '本章 .docx 已就绪',
        description: '正在打开下载…',
      })
      window.location.href = downloadChapterDocxUrl(chapterId)
      setPollState({ projectId: null, jobId: null })
    }
  }, [job.data, toast, chapterId])

  const handleClick = async () => {
    try {
      const res = await trigger.mutateAsync(chapterId)
      if (res.cached) {
        toast({
          variant: 'info',
          title: '使用已生成的 .docx',
          description: '正在打开下载…',
        })
        window.location.href = downloadChapterDocxUrl(chapterId)
        return
      }
      setPollState({
        projectId: res.project_id ?? null,
        jobId: res.docx_job_id,
      })
    } catch (err) {
      toast({
        variant: 'destructive',
        title: '触发失败',
        description: readApiError(err, '请重试'),
      })
    }
  }

  const polling = pollState.jobId !== null
  const stageLabel =
    job.data?.stage ?? (trigger.isPending ? '排队中' : '导出本章')

  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      onClick={handleClick}
      disabled={trigger.isPending || polling}
      className="mt-4"
    >
      {polling || trigger.isPending ? (
        <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <Download className="mr-1 h-4 w-4" aria-hidden="true" />
      )}
      {polling ? stageLabel : '导出本章 .docx'}
    </Button>
  )
}

// D-EL:本章参考资料面板。展示 LLM-2 生成正文期间看过的实体黑板条目
// (BM25 + 向量首轮召回 + LLM 主动 search_blackboard 工具调用)。
// 文案明确「LLM 看过的资料」而非「引用」,避免误导:有些条目模型看了但未在
// 正文中直接出现。
function ChapterReferencesPanel({
  references,
}: {
  references: ChapterReferenceItem[] | null
}) {
  if (!references || references.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-muted/30 p-6 text-center text-sm text-muted-foreground">
        本章生成时未记录参考资料。
        <p className="mt-1 text-xs">
          老项目 / 未启用混合召回的章节会留空。
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">
        LLM 在生成本章正文时看过下列 {references.length} 条来自上传材料的条目。展开
        的内容是被检索到的原文片段,有些会出现在正文里,有些只是模型用作背景判断。
      </p>
      <ul className="space-y-2">
        {references.map((item, idx) => (
          <li
            key={idx}
            className="rounded-md border border-border bg-background p-3 text-sm"
          >
            <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {item.bucket && (
                <span className="rounded-sm bg-muted px-1.5 py-0.5 font-mono">
                  {item.bucket}
                </span>
              )}
              {item.source_doc && <span>{item.source_doc}</span>}
              {item.section && <span>· {item.section}</span>}
              {item.retrieval_method && (
                <span className="ml-auto rounded-sm bg-muted/60 px-1.5 py-0.5 text-[10px] uppercase">
                  {item.retrieval_method}
                </span>
              )}
            </div>
            <p className="whitespace-pre-wrap leading-relaxed text-foreground">
              {item.content}
            </p>
          </li>
        ))}
      </ul>
    </div>
  )
}
