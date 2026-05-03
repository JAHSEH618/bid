import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileText,
  Loader2,
  Play,
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
import {
  useProject,
  useProjectDocuments,
  useStartProject,
  useUploadDocument,
} from '@/api/projects'
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
const MAX_BYTES = 50 * 1024 * 1024
const ALLOWED_EXTS = ['docx', 'doc', 'md', 'txt']

export function DocumentUploadPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const project = useProject(projectId)
  const documents = useProjectDocuments(projectId)
  const start = useStartProject()

  const uploadedByKind = useMemo<Partial<Record<DocumentKind, DocumentDTO>>>(
    () => {
      const acc: Partial<Record<DocumentKind, DocumentDTO>> = {}
      for (const d of documents.data ?? []) {
        acc[d.kind] = d
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

  const techSpec = uploadedByKind.tech_spec
  const scoring = uploadedByKind.scoring
  const requiredCount = (techSpec ? 1 : 0) + (scoring ? 1 : 0)

  const canStart = Boolean(
    techSpec && scoring && project.data && project.data.status === 'init',
  )

  const handleStart = async () => {
    if (!projectId || !canStart || !project.data) return
    try {
      const res = await start.mutateAsync({
        projectId,
        body: {
          pages_per_chapter: project.data.pages_per_chapter,
          max_retry_per_chapter: project.data.max_retry_per_chapter,
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
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.data.name}
        </h1>
        <p className="text-sm text-muted-foreground">
          上传招标文档以启动 AI 工作流。支持 .docx / .doc / .md / .txt,单文件 ≤ 50MB
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
            className="h-full rounded-full bg-primary transition-all duration-300 ease-out"
            style={{ width: `${(requiredCount / 2) * 100}%` }}
          />
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          {canStart
            ? '所有必传文档已就绪,可以启动工作流'
            : '上传「技术需求书」与「评分细则」后即可启动'}
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {(['tech_spec', 'scoring', 'template'] as DocumentKind[]).map((kind) => (
          <UploadSlot
            key={kind}
            kind={kind}
            projectId={projectId}
            existing={uploadedByKind[kind]}
          />
        ))}
      </div>

      <div className="flex items-center justify-end gap-3 rounded-xl border border-border/70 bg-card p-4 shadow-sm">
        {!canStart && (
          <p className="flex-1 text-xs text-muted-foreground">
            上传「技术需求书」与「评分细则」后可启动 AI 工作流
          </p>
        )}
        <Button
          size="lg"
          onClick={handleStart}
          disabled={!canStart || start.isPending}
          className={cn(canStart && 'shadow-md shadow-primary/15')}
        >
          {start.isPending ? (
            <>
              <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              启动中…
            </>
          ) : (
            <>
              <Play className="mr-1.5 h-4 w-4" />
              启动 AI 工作流
              <ArrowRight className="ml-1 h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

function UploadSlot({
  kind,
  projectId,
  existing,
}: {
  kind: DocumentKind
  projectId: number
  existing: DocumentDTO | undefined
}) {
  const meta = KIND_META[kind]
  const fileRef = useRef<HTMLInputElement>(null)
  const upload = useUploadDocument()
  const [error, setError] = useState<string | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const { toast } = useToast()

  const handlePick = () => fileRef.current?.click()

  const validate = (file: File): string | null => {
    if (file.size > MAX_BYTES) {
      return `文件 ≤ 50MB,当前 ${(file.size / 1024 / 1024).toFixed(1)}MB`
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

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    await submitFile(file)
  }

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setDragActive(false)
    const file = e.dataTransfer.files?.[0]
    if (file) await submitFile(file)
  }

  return (
    <Card
      className={cn(
        'flex flex-col border-border/70 transition-shadow',
        existing && 'bg-emerald-50/40',
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
        <CardDescription className="text-xs">{meta.description}</CardDescription>
      </CardHeader>
      <CardContent className="flex-1 space-y-2">
        {existing ? (
          <div className="space-y-2.5">
            <div className="flex items-start gap-2 rounded-md border border-emerald-200/70 bg-white p-2.5">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-emerald-100">
                <CheckCircle2 className="h-4 w-4 text-emerald-700" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="line-clamp-1 text-sm font-medium text-foreground">
                  {existing.original_filename}
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {(existing.file_size / 1024).toFixed(1)} KB
                  {existing.extract_error && (
                    <span className="ml-2 text-destructive">· 抽取失败</span>
                  )}
                </p>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              onClick={handlePick}
              disabled={upload.isPending}
            >
              {upload.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  替换中…
                </>
              ) : (
                <>
                  <Upload className="mr-1.5 h-3.5 w-3.5" />
                  替换文件
                </>
              )}
            </Button>
          </div>
        ) : (
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
            className={cn(
              'flex min-h-[120px] cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-border bg-background/80 px-3 py-4 text-center transition-all duration-150',
              'hover:border-primary/50 hover:bg-primary/5',
              'focus-visible:outline-none focus-visible:border-primary focus-visible:ring-2 focus-visible:ring-ring/30',
              dragActive && 'border-primary bg-primary/10 scale-[1.01]',
              upload.isPending && 'pointer-events-none opacity-70',
            )}
          >
            {upload.isPending ? (
              <>
                <Loader2 className="h-6 w-6 animate-spin text-primary" />
                <p className="text-xs font-medium text-muted-foreground">
                  上传中…
                </p>
              </>
            ) : (
              <>
                <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary/10">
                  <Upload className="h-4 w-4 text-primary" />
                </div>
                <p className="text-xs font-medium text-foreground">
                  点击或拖拽文件到此
                </p>
                <p className="text-[10px] text-muted-foreground">
                  .docx / .doc / .md / .txt · ≤50MB
                </p>
              </>
            )}
          </div>
        )}
        {error && (
          <p className="flex items-start gap-1 text-xs text-destructive">
            <FileText className="mt-0.5 h-3 w-3 shrink-0" />
            {error}
          </p>
        )}
        <input
          ref={fileRef}
          type="file"
          accept={`${ACCEPT},${ACCEPT_MIME}`}
          onChange={handleChange}
          className="hidden"
        />
      </CardContent>
    </Card>
  )
}
