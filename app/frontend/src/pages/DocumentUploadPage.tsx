import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  Loader2,
  Play,
  Trash2,
  Upload,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Label } from '@/components/ui/label'
import {
  useDeleteDocument,
  useProject,
  useProjectDocuments,
  useStartProject,
  useUploadDocument,
} from '@/api/projects'
import { useModelConfig } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import { cn } from '@/lib/utils'
import type { DocumentDTO, DocumentKind } from '@/lib/types'

const KIND_META: Record<
  DocumentKind,
  { label: string; description: string; required: boolean }
> = {
  tech_spec: {
    label: '技术需求书',
    description: '招标方发布的技术需求文档',
    required: true,
  },
  scoring: {
    label: '评分细则',
    description: '评标办法 / 商务+技术分布',
    required: true,
  },
  template: {
    label: '方案模板 / 历史方案',
    description: '本公司历史中标方案或参考模板',
    required: false,
  },
}

const ACCEPT = '.docx,.doc,.md,.txt'
const ACCEPT_MIME =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/markdown,text/plain'
// PR-M7-2 / D5:与后端 settings.max_file_upload_bytes (200MB) 对齐。
const MAX_BYTES = 200 * 1024 * 1024
const MAX_MB_LABEL = '200MB'
const ALLOWED_EXTS = ['docx', 'doc', 'md', 'txt']

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

export function DocumentUploadPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const project = useProject(projectId)
  const documents = useProjectDocuments(projectId)
  const start = useStartProject()
  const modelConfig = useModelConfig()
  const [outlineModel, setOutlineModel] = useState('')
  const [visualsModel, setVisualsModel] = useState('')

  // PR-M7-2 多文件:每个 kind 保留全部文档(按上传时间从旧到新)。
  const documentsByKind = useMemo<Record<DocumentKind, DocumentDTO[]>>(
    () => {
      const acc: Record<DocumentKind, DocumentDTO[]> = {
        tech_spec: [],
        scoring: [],
        template: [],
      }
      for (const d of documents.data ?? []) {
        if (d.kind && d.kind in acc) {
          acc[d.kind].push(d)
        }
      }
      return acc
    },
    [documents.data],
  )

  useEffect(() => {
    if (project.data && project.data.status !== 'init') {
      navigate(`/projects/${projectId}/outline`, { replace: true })
    }
  }, [project.data, navigate, projectId])

  useEffect(() => {
    const data = modelConfig.data
    if (!data) return
    setOutlineModel((prev) =>
      pickModel(data.available_models, data.default_outline_model, prev),
    )
    setVisualsModel((prev) =>
      pickModel(data.available_models, data.default_visuals_model, prev),
    )
  }, [modelConfig.data])

  const hasTechSpec = documentsByKind.tech_spec.length > 0
  const hasScoring = documentsByKind.scoring.length > 0
  const requiredCount = (hasTechSpec ? 1 : 0) + (hasScoring ? 1 : 0)

  const canStart = Boolean(
    hasTechSpec &&
      hasScoring &&
      project.data &&
      project.data.status === 'init' &&
      (modelConfig.data?.available_models.length ?? 0) > 0,
  )

  const handleStart = async () => {
    if (!projectId || !canStart || !project.data) return
    try {
      const res = await start.mutateAsync({
        projectId,
        body: {
          pages_per_chapter: project.data.pages_per_chapter,
          max_retry_per_chapter: project.data.max_retry_per_chapter,
          outline_model: outlineModel || null,
          chapter_model: pickModel(
            modelConfig.data?.available_models ?? [],
            modelConfig.data?.default_chapter_model ?? '',
          ) || null,
          visuals_model: visualsModel || null,
        },
      })
      if (res.queued) {
        toast({
          title: '已加入排队',
          description: '系统繁忙,前面有项目在跑,稍后自动开始',
          variant: 'warning',
        })
      } else {
        toast({ title: '工作流已启动', variant: 'success' })
      }
      navigate(`/projects/${projectId}/outline`)
    } catch (err) {
      const msg = readApiError(err, '启动失败')
      toast({ title: '启动失败', description: msg, variant: 'destructive' })
    }
  }

  if (project.isLoading) {
    return (
      <div className="container py-12">
        <div className="skeleton h-8 w-1/3" />
        <div className="skeleton mt-2 h-4 w-1/2" />
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="skeleton h-48 rounded-xl" />
          ))}
        </div>
      </div>
    )
  }
  if (project.isError || !project.data) {
    return (
      <div className="container space-y-3 py-12 text-sm text-destructive">
        <p>项目不存在或无访问权限</p>
        <Button asChild variant="outline" size="sm">
          <Link to="/">返回项目列表</Link>
        </Button>
      </div>
    )
  }

  return (
    <div className="container max-w-5xl space-y-6 py-8 page-enter">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft aria-hidden="true" className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.data.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          上传招标文档以启动 AI 工作流。支持 .docx / .doc / .md / .txt,单文件&nbsp;≤&nbsp;{MAX_MB_LABEL},每个模块可上传多个文件
        </p>
      </header>

      {/* 进度条:必传文档完成度 */}
      <div className="rounded-xl border border-border/70 bg-card p-4 shadow-sm">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">必传文档准备进度</span>
          <span className="tabular-nums text-muted-foreground">
            {requiredCount} / 2
          </span>
        </div>
        <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-300 ease-out motion-reduce:transition-none"
            style={{ width: `${(requiredCount / 2) * 100}%` }}
          />
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {canStart
            ? '所有必传文档已就绪,可以启动工作流'
            : '上传「技术需求书」与「评分细则」各至少一份后即可启动'}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {(['tech_spec', 'scoring', 'template'] as DocumentKind[]).map((kind) => (
          <UploadSlot
            key={kind}
            kind={kind}
            projectId={projectId}
            existing={documentsByKind[kind]}
          />
        ))}
      </div>

      <Card className="border-border/70 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">本次生成模型</CardTitle>
          <CardDescription className="text-xs">
            正文模型在章节审核页按章节选择
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <ModelSelect
            id="outline-model"
            label="提纲生成"
            value={outlineModel}
            onChange={setOutlineModel}
            models={modelConfig.data?.available_models ?? []}
            fallback={modelConfig.data?.default_outline_model ?? ''}
            loading={modelConfig.isLoading}
          />
          <ModelSelect
            id="visuals-model"
            label="配图生成"
            value={visualsModel}
            onChange={setVisualsModel}
            models={modelConfig.data?.available_models ?? []}
            fallback={modelConfig.data?.default_visuals_model ?? ''}
            loading={modelConfig.isLoading}
          />
        </CardContent>
      </Card>

      <div className="flex items-center justify-end gap-3 rounded-xl border border-border/70 bg-card p-4 shadow-sm">
        {!canStart && (
          <p className="flex-1 text-xs text-muted-foreground">
            上传「技术需求书」与「评分细则」各至少一份并配置模型后可启动 AI 工作流
          </p>
        )}
        <Button
          size="lg"
          onClick={handleStart}
          disabled={!canStart || start.isPending || modelConfig.isLoading}
          className={cn(canStart && 'shadow-md shadow-primary/15')}
        >
          {start.isPending ? (
            <>
              <Loader2 aria-hidden="true" className="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" />
              启动中…
            </>
          ) : (
            <>
              <Play aria-hidden="true" className="mr-1.5 h-4 w-4" />
              启动 AI 工作流
              <ArrowRight aria-hidden="true" className="ml-1 h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

function ModelSelect({
  id,
  label,
  value,
  onChange,
  models,
  fallback,
  loading,
}: {
  id: string
  label: string
  value: string
  onChange: (v: string) => void
  models: string[]
  fallback: string
  loading: boolean
}) {
  const options = models
  const selected = pickModel(options, fallback, value)
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs font-medium">
        {label}
      </Label>
      <select
        id={id}
        value={selected}
        onChange={(e) => onChange(e.target.value)}
        disabled={loading || options.length === 0}
        style={{ colorScheme: 'light dark' }}
        className="flex h-9 w-full rounded-md border border-input bg-background text-foreground px-3 py-1 font-mono text-xs shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        {options.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
    </div>
  )
}

function pickModel(models: string[], preferred: string, current = '') {
  if (current && models.includes(current)) return current
  if (preferred && models.includes(preferred)) return preferred
  return models[0] ?? ''
}

function UploadSlot({
  kind,
  projectId,
  existing,
}: {
  kind: DocumentKind
  projectId: number
  existing: DocumentDTO[]
}) {
  const meta = KIND_META[kind]
  const fileRef = useRef<HTMLInputElement>(null)
  const upload = useUploadDocument()
  const remove = useDeleteDocument()
  const [error, setError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const { toast } = useToast()

  const handlePick = () => fileRef.current?.click()

  const validate = (file: File): string | null => {
    if (file.size > MAX_BYTES) {
      return `文件 ≤ ${MAX_MB_LABEL},当前 ${(file.size / 1024 / 1024).toFixed(1)} MB`
    }
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!ALLOWED_EXTS.includes(ext ?? '')) {
      return '仅支持 .docx / .doc / .md / .txt'
    }
    return null
  }

  const submitFile = async (file: File) => {
    const v = validate(file)
    if (v) {
      setError(v)
      return
    }
    setError(null)
    try {
      const doc = await upload.mutateAsync({ projectId, kind, file })
      toast({ title: `${meta.label} 已上传`, variant: 'success' })
      if (doc.extract_error) {
        toast({
          title: '抽取失败',
          description: `已落盘但 markitdown 提取失败:${doc.extract_error}`,
          variant: 'warning',
        })
      }
    } catch (err) {
      const msg = readApiError(err, '上传失败')
      setError(msg)
      toast({ title: '上传失败', description: msg, variant: 'destructive' })
    }
  }

  const submitFiles = async (files: File[]) => {
    for (const file of files) {
      await submitFile(file)
    }
  }

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    e.target.value = ''
    if (files.length === 0) return
    await submitFiles(files)
  }

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragActive(false)
    const files = Array.from(e.dataTransfer.files ?? [])
    if (files.length > 0) await submitFiles(files)
  }

  const handleRemove = async (doc: DocumentDTO) => {
    setError(null)
    try {
      await remove.mutateAsync({ projectId, documentId: doc.id })
      toast({
        title: `${doc.original_filename} 已删除`,
        variant: 'success',
      })
    } catch (err) {
      const msg = readApiError(err, '删除失败')
      toast({ title: '删除失败', description: msg, variant: 'destructive' })
    }
  }

  const hasFiles = existing.length > 0

  return (
    <Card
      className={cn(
        'flex flex-col border-border/70 transition-shadow',
        hasFiles && 'bg-emerald-50/40',
      )}
    >
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">{meta.label}</CardTitle>
          {meta.required ? (
            <Badge variant="destructive" className="text-[10px]">
              必传
            </Badge>
          ) : (
            <Badge variant="muted" className="text-[10px]">
              可选
            </Badge>
          )}
        </div>
        <CardDescription className="text-xs">
          {meta.description}
          {hasFiles && (
            <span className="ml-1 text-muted-foreground">· 已 {existing.length} 份</span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex-1 space-y-2">
        {hasFiles && (
          <ul className="space-y-2">
            {existing.map((doc) => (
              <li
                key={doc.id}
                className="flex items-start gap-2 rounded-md border border-emerald-200/70 bg-white p-2.5"
              >
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-emerald-100">
                  <CheckCircle2 aria-hidden="true" className="h-4 w-4 text-emerald-700" />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-1 text-sm font-medium text-foreground">
                    {doc.original_filename}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {formatBytes(doc.byte_size ?? doc.file_size)}
                    {doc.extract_error && (
                      <span className="ml-2 text-destructive">· 抽取失败</span>
                    )}
                  </p>
                </div>
                <Button
                  variant="ghost"
                  size="iconSm"
                  className="shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={() => handleRemove(doc)}
                  disabled={remove.isPending}
                  aria-label={`删除 ${doc.original_filename}`}
                >
                  {remove.isPending ? (
                    <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                  ) : (
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  )}
                </Button>
              </li>
            ))}
          </ul>
        )}
        <div
          role="button"
          tabIndex={0}
          onClick={handlePick}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              handlePick()
            }
          }}
          onDragOver={(e) => {
            e.preventDefault()
            setDragActive(true)
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={handleDrop}
          aria-label={`${meta.label}:点击或拖拽文件上传,单文件最大 ${MAX_MB_LABEL}`}
          className={cn(
            'flex min-h-[110px] cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-border bg-background/80 px-3 py-4 text-center transition-[background-color,border-color,transform] duration-150',
            'hover:border-primary/50 hover:bg-primary/5',
            'focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring/30',
            dragActive && 'border-primary bg-primary/10 scale-[1.01]',
            upload.isPending && 'pointer-events-none opacity-70',
          )}
        >
          {upload.isPending ? (
            <>
              <Loader2 aria-hidden="true" className="h-6 w-6 animate-spin motion-reduce:animate-none text-primary" />
              <p className="text-xs font-medium text-muted-foreground">
                上传中…
              </p>
            </>
          ) : (
            <>
              <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                <Upload aria-hidden="true" className="h-4 w-4 text-primary" />
              </div>
              <p className="text-xs font-medium text-foreground">
                {hasFiles ? '继续添加文件' : '点击或拖拽文件到此'}
              </p>
              <p className="text-[10px] text-muted-foreground">
                .docx / .doc / .md / .txt · 单文件&nbsp;≤&nbsp;{MAX_MB_LABEL} · 可多选
              </p>
            </>
          )}
        </div>
        {error && (
          <p
            role="alert"
            aria-live="polite"
            className="flex items-start gap-1 text-xs text-destructive"
          >
            <FileText aria-hidden="true" className="mt-0.5 h-3 w-3 shrink-0" />
            {error}
          </p>
        )}
        <input
          ref={fileRef}
          type="file"
          accept={`${ACCEPT},${ACCEPT_MIME}`}
          multiple
          onChange={handleChange}
          className="hidden"
        />
      </CardContent>
    </Card>
  )
}
