import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  GripVertical,
  ListOrdered,
  Loader2,
  Pencil,
  Plus,
  Sparkles,
  X,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import {
  useConfirmOutline,
  useProject,
  useProjectOutline,
} from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import type { OutlineChapterIn } from '@/lib/types'

interface EditableChapter {
  id: string | null
  title: string
  summary: string
  key_points: string[]
  target_pages: number
}

export function OutlineConfirmPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const project = useProject(projectId)
  const outline = useProjectOutline(projectId)
  const confirm = useConfirmOutline()
  const [chapters, setChapters] = useState<EditableChapter[]>([])
  const [edited, setEdited] = useState(false)

  useEffect(() => {
    if (!outline.data) return
    setChapters(
      outline.data.chapters.map((c) => ({
        id: c.id,
        title: c.title,
        summary: c.summary ?? '',
        key_points: c.key_points,
        target_pages: c.target_pages,
      })),
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outline.data?.run_id, outline.data?.chapters.length])

  const isReady = project.data?.status === 'outline_ready'
  const totalPages = chapters.reduce((s, c) => s + (c.target_pages || 0), 0)

  const updateChapter = (i: number, patch: Partial<EditableChapter>) => {
    setEdited(true)
    setChapters((prev) =>
      prev.map((c, idx) => (idx === i ? { ...c, ...patch } : c)),
    )
  }
  const removeChapter = (i: number) => {
    setEdited(true)
    setChapters((prev) => prev.filter((_, idx) => idx !== i))
  }
  const addChapter = () => {
    setEdited(true)
    setChapters((prev) => [
      ...prev,
      {
        id: null,
        title: '新章节',
        summary: '',
        key_points: ['要点 1'],
        target_pages: 3,
      },
    ])
  }

  const handleConfirm = async () => {
    if (!projectId) return
    for (const c of chapters) {
      if (!c.title.trim()) {
        toast({ title: '每个章节都必须有标题', variant: 'warning' })
        return
      }
      if (c.key_points.length === 0 || c.key_points.every((p) => !p.trim())) {
        toast({
          title: `「${c.title}」需要至少一个关键要点`,
          variant: 'warning',
        })
        return
      }
      if (c.target_pages < 1 || c.target_pages > 10) {
        toast({
          title: `「${c.title}」目标页数需在 1-10`,
          variant: 'warning',
        })
        return
      }
    }
    const payload: OutlineChapterIn[] = edited
      ? chapters.map((c) => ({
          id: c.id,
          title: c.title.trim(),
          summary: c.summary.trim() || null,
          key_points: c.key_points.map((p) => p.trim()).filter(Boolean),
          target_pages: c.target_pages,
        }))
      : []
    try {
      await confirm.mutateAsync({ projectId, chapters: payload })
      toast({
        title: edited ? '已确认编辑后的提纲' : '已沿用 AI 生成的提纲',
        variant: 'success',
      })
      navigate(`/projects/${projectId}/review`)
    } catch (err) {
      toast({
        title: '确认失败',
        description: readApiError(err, '确认失败'),
        variant: 'destructive',
      })
    }
  }

  if (project.isLoading || outline.isLoading) {
    return (
      <div className="container max-w-4xl space-y-4 py-8">
        <div className="skeleton h-8 w-1/3" />
        <div className="skeleton h-4 w-1/2" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="skeleton h-36 rounded-xl" />
        ))}
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
    <div className="container max-w-4xl space-y-6 py-8 page-enter">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-2">
        <div className="flex items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {project.data.name}
          </h1>
          <Badge variant="muted" className="text-[11px]">
            确认提纲
          </Badge>
        </div>
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Sparkles className="h-4 w-4 text-primary/70" />
          AI 已根据技术需求与评分细则生成{' '}
          <span className="font-medium text-foreground">{chapters.length}</span>{' '}
          章提纲,目标总页数约{' '}
          <span className="font-medium text-foreground">{totalPages}</span> 页
          {edited && (
            <Badge variant="warning" className="ml-1 text-[10px]">
              <Pencil className="h-2.5 w-2.5" />
              已编辑
            </Badge>
          )}
        </p>
      </header>

      {!isReady && (
        <Card className="border-amber-200 bg-amber-50/70">
          <CardContent className="flex items-center gap-2 py-4 text-sm text-amber-900">
            <Loader2 className="h-4 w-4 animate-spin" />
            提纲尚未就绪(当前状态:{project.data.status})。请稍后刷新
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {chapters.map((c, i) => (
          <Card
            key={c.id ?? `new-${i}`}
            className="border-border/70 transition-shadow hover:shadow-sm"
          >
            <CardHeader className="flex flex-row items-center gap-2 pb-3">
              <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold text-primary">
                {i + 1}
              </span>
              <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground/60" />
              <span className="text-xs text-muted-foreground">
                第 {i + 1} 章
              </span>
              <Button
                variant="ghost"
                size="iconSm"
                className="ml-auto text-muted-foreground hover:text-destructive"
                onClick={() => removeChapter(i)}
                title="移除本章"
              >
                <X className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-3">
              <Input
                value={c.title}
                placeholder="章节标题"
                className="text-[15px] font-medium"
                onChange={(e) => updateChapter(i, { title: e.target.value })}
              />
              <Textarea
                value={c.summary}
                placeholder="章节简介(可选,会作为本章生成上下文)"
                rows={2}
                onChange={(e) => updateChapter(i, { summary: e.target.value })}
              />
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
                <div className="flex-1">
                  <label className="text-xs font-medium text-muted-foreground">
                    关键要点 · 每行一条
                  </label>
                  <Textarea
                    value={c.key_points.join('\n')}
                    placeholder="一行一条关键要点"
                    rows={3}
                    className="mt-1"
                    onChange={(e) =>
                      updateChapter(i, {
                        key_points: e.target.value.split('\n'),
                      })
                    }
                  />
                </div>
                <div className="sm:w-32">
                  <label className="text-xs font-medium text-muted-foreground">
                    目标页数
                  </label>
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={c.target_pages}
                    className="mt-1 text-center"
                    onChange={(e) =>
                      updateChapter(i, {
                        target_pages: Math.max(
                          1,
                          Math.min(10, Number(e.target.value) || 1),
                        ),
                      })
                    }
                  />
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    1 ~ 10 页
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}

        <Button
          variant="outline"
          className="w-full border-dashed py-6 hover:border-primary/40 hover:bg-primary/5"
          onClick={addChapter}
        >
          <Plus className="mr-1 h-4 w-4" />
          添加章节
        </Button>
      </div>

      {/* 确认区:用户友好文案,不再暴露"发送空数组"等技术细节 */}
      <Card className="border-primary/30 bg-primary/[0.04] shadow-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ListOrdered className="h-4 w-4 text-primary" />
            确认提纲并启动章节生成
          </CardTitle>
          <CardDescription>
            {edited ? (
              <span className="flex items-center gap-1.5">
                <Pencil className="h-3.5 w-3.5" />
                已编辑提纲,确认后将以编辑后的版本启动章节生成
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" />
                提纲已生成,可直接确认或编辑后再启动
              </span>
            )}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col items-stretch gap-2 sm:flex-row sm:justify-end">
          <Button asChild variant="outline">
            <Link to={`/projects/${projectId}/upload`}>返回上传</Link>
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={confirm.isPending}
            size="lg"
            className="shadow-md shadow-primary/15"
          >
            {confirm.isPending ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                提交中…
              </>
            ) : (
              <>
                确认并开始生成章节
                <ArrowRight className="ml-1 h-4 w-4" />
              </>
            )}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
