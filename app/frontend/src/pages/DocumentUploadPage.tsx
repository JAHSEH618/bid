import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, FileText, Upload, Trash2, Play } from 'lucide-react'
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
  useProjectDetail,
  useStartProject,
  useUploadDocument,
} from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'
import type { DocumentDTO, DocumentKind } from '@/lib/types'

const KIND_META: Record<
  DocumentKind,
  { label: string; description: string; required: boolean }
> = {
  tech_spec: {
    label: '技术需求书',
    description: '招标方发布的技术需求文档(必传)',
    required: true,
  },
  scoring_rules: {
    label: '评分细则',
    description: '评标办法 / 商务+技术分布(必传)',
    required: true,
  },
  reference_doc: {
    label: '方案模板 / 历史方案',
    description: '本公司历史中标方案或参考模板(可选)',
    required: false,
  },
}

const ACCEPT = '.docx,.doc,.md,.txt'
const ACCEPT_MIME =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/markdown,text/plain'
const MAX_BYTES = 50 * 1024 * 1024

export function DocumentUploadPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const detail = useProjectDetail(projectId)
  const upload = useUploadDocument()
  const start = useStartProject()

  const docs = detail.data?.documents ?? []
  const project = detail.data?.project

  const techSpec = docs.find((d) => d.kind === 'tech_spec')
  const scoring = docs.find((d) => d.kind === 'scoring_rules')

  const canStart = Boolean(
    techSpec && scoring && project && project.status === 'init',
  )

  const handleStart = async () => {
    if (!projectId || !canStart) return
    try {
      const res = await start.mutateAsync({
        projectId,
        body: { pages_per_chapter: 3, max_retry_per_chapter: 3 },
      })
      if (res.queued) {
        toast({
          title: '已加入排队',
          description: '系统繁忙,前面有项目在跑,稍后自动开始。',
          variant: 'warning',
        })
      } else {
        toast({ title: '工作流已启动', variant: 'success' })
      }
      navigate(`/projects/${projectId}/outline`)
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '启动失败')
          : '启动失败'
      toast({ title: '启动失败', description: msg, variant: 'destructive' })
    }
  }

  if (detail.isLoading) {
    return (
      <div className="container py-12 text-sm text-muted-foreground">
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
    <div className="container max-w-4xl space-y-6 py-8">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
        <p className="text-sm text-muted-foreground">
          上传招标文档。仅支持 .docx / .doc / .md / .txt,单文件 ≤ 50MB。PDF 不支持。
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {(['tech_spec', 'scoring_rules', 'reference_doc'] as DocumentKind[]).map(
          (kind) => (
            <UploadSlot
              key={kind}
              kind={kind}
              projectId={projectId}
              existing={docs.find((d) => d.kind === kind)}
              onUploaded={() => {
                upload.reset()
                detail.refetch()
              }}
            />
          ),
        )}
      </div>

      <div className="flex items-center justify-end">
        <Button onClick={handleStart} disabled={!canStart || start.isPending}>
          <Play className="mr-1 h-4 w-4" />
          {start.isPending ? '启动中…' : '启动工作流'}
        </Button>
      </div>
    </div>
  )
}

function UploadSlot({
  kind,
  projectId,
  existing,
  onUploaded,
}: {
  kind: DocumentKind
  projectId: number
  existing: DocumentDTO | undefined
  onUploaded: () => void
}) {
  const meta = KIND_META[kind]
  const fileRef = useRef<HTMLInputElement>(null)
  const upload = useUploadDocument()
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()

  const handlePick = () => fileRef.current?.click()

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (file.size > MAX_BYTES) {
      setError(`文件 ≤ 50MB,当前 ${(file.size / 1024 / 1024).toFixed(1)}MB`)
      return
    }
    const ext = file.name.split('.').pop()?.toLowerCase()
    if (!['docx', 'doc', 'md', 'txt'].includes(ext ?? '')) {
      setError('仅支持 .docx / .doc / .md / .txt')
      return
    }
    setError(null)
    try {
      await upload.mutateAsync({ projectId, kind, file })
      toast({ title: `${meta.label} 已上传`, variant: 'success' })
      onUploaded()
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '上传失败')
          : '上传失败'
      setError(msg)
      toast({ title: '上传失败', description: msg, variant: 'destructive' })
    }
  }

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">{meta.label}</CardTitle>
          {meta.required ? (
            <Badge variant="destructive" className="text-[10px]">
              必传
            </Badge>
          ) : (
            <Badge variant="outline" className="text-[10px]">
              可选
            </Badge>
          )}
        </div>
        <CardDescription className="text-xs">
          {meta.description}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {existing ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-sm">
              <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="line-clamp-1 flex-1">{existing.filename}</span>
            </div>
            <p className="text-xs text-muted-foreground">
              {(existing.size_bytes / 1024).toFixed(1)} KB ·{' '}
              {new Date(existing.uploaded_at).toLocaleString('zh-CN')}
            </p>
            <div className="flex gap-1.5">
              <Button
                variant="outline"
                size="sm"
                className="flex-1"
                onClick={handlePick}
                disabled={upload.isPending}
              >
                <Upload className="mr-1 h-3.5 w-3.5" />
                替换
              </Button>
              <Button variant="ghost" size="sm" disabled>
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        ) : (
          <Button
            variant="outline"
            className="h-24 w-full border-dashed"
            onClick={handlePick}
            disabled={upload.isPending}
          >
            <Upload className="mr-1 h-4 w-4" />
            {upload.isPending ? '上传中…' : '选择文件'}
          </Button>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
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
