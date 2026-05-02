import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, GripVertical, Plus, X } from 'lucide-react'
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
    // outline.data 第一次加载后初始化,后续编辑由用户控制
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outline.data?.run_id, outline.data?.chapters.length])

  const isReady = project.data?.status === 'outline_ready'

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
    // 校验:title 非空、key_points 至少一个非空字符串、target_pages 1..10
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
        title: edited ? '已确认编辑后的提纲' : '已沿用 LLM 提纲',
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
      <div className="container py-12 text-sm text-muted-foreground">
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
    <div className="container max-w-4xl space-y-6 py-8">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.data.name} · 确认提纲
        </h1>
        <p className="text-sm text-muted-foreground">
          AI 已根据技术需求生成提纲。可直接确认沿用,也可编辑后再启动。共{' '}
          {chapters.length} 章。
        </p>
      </header>

      {!isReady && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-4 text-sm text-amber-900">
            提纲尚未就绪(当前状态:{project.data.status})。请稍后刷新。
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {chapters.map((c, i) => (
          <Card key={c.id ?? `new-${i}`}>
            <CardHeader className="flex flex-row items-center gap-2 pb-2">
              <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground" />
              <span className="text-xs text-muted-foreground">
                第 {i + 1} 章
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() => removeChapter(i)}
              >
                <X className="h-4 w-4" />
              </Button>
            </CardHeader>
            <CardContent className="space-y-2">
              <Input
                value={c.title}
                placeholder="章节标题"
                onChange={(e) => updateChapter(i, { title: e.target.value })}
              />
              <Textarea
                value={c.summary}
                placeholder="章节简介(可选)"
                rows={2}
                onChange={(e) =>
                  updateChapter(i, { summary: e.target.value })
                }
              />
              <div className="flex items-start gap-2">
                <div className="flex-1">
                  <label className="text-xs text-muted-foreground">
                    关键要点(每行一条)
                  </label>
                  <Textarea
                    value={c.key_points.join('\n')}
                    placeholder="一行一条关键要点"
                    rows={3}
                    onChange={(e) =>
                      updateChapter(i, {
                        key_points: e.target.value.split('\n'),
                      })
                    }
                  />
                </div>
                <div className="w-28">
                  <label className="text-xs text-muted-foreground">
                    目标页数
                  </label>
                  <Input
                    type="number"
                    min={1}
                    max={10}
                    value={c.target_pages}
                    onChange={(e) =>
                      updateChapter(i, {
                        target_pages: Math.max(
                          1,
                          Math.min(10, Number(e.target.value) || 1),
                        ),
                      })
                    }
                  />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}

        <Button
          variant="outline"
          className="w-full border-dashed"
          onClick={addChapter}
        >
          <Plus className="mr-1 h-4 w-4" />
          添加章节
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">确认提纲</CardTitle>
          <CardDescription>
            {edited
              ? '已编辑,确认后将以编辑后的版本启动章节生成。'
              : '未编辑,确认后将沿用 LLM 提纲(发送空 chapters 数组)。'}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex justify-end gap-2">
          <Button asChild variant="outline">
            <Link to={`/projects/${projectId}/upload`}>返回上传</Link>
          </Button>
          <Button onClick={handleConfirm} disabled={confirm.isPending}>
            {confirm.isPending ? '提交中…' : '确认并开始生成章节'}
            <ArrowRight className="ml-1 h-4 w-4" />
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
