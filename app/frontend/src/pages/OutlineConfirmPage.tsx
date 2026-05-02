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
import { useConfirmOutline, useProjectDetail } from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'
import type { OutlineChapter } from '@/lib/types'

export function OutlineConfirmPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const detail = useProjectDetail(projectId)
  const confirm = useConfirmOutline()
  const [chapters, setChapters] = useState<OutlineChapter[]>([])
  const [edited, setEdited] = useState(false)

  useEffect(() => {
    if (detail.data?.outline?.chapters) {
      setChapters(detail.data.outline.chapters)
    }
  }, [detail.data?.outline])

  const project = detail.data?.project
  const isReady =
    project?.status === 'outline_ready' || project?.status === 'outlining'

  const updateChapter = (index: number, patch: Partial<OutlineChapter>) => {
    setEdited(true)
    setChapters((prev) =>
      prev.map((c, i) => (i === index ? { ...c, ...patch } : c)),
    )
  }
  const removeChapter = (index: number) => {
    setEdited(true)
    setChapters((prev) =>
      prev
        .filter((_, i) => i !== index)
        .map((c, i) => ({ ...c, index: i })),
    )
  }
  const addChapter = () => {
    setEdited(true)
    setChapters((prev) => [
      ...prev,
      { index: prev.length, title: '新章节', description: '' },
    ])
  }

  const handleConfirm = async () => {
    if (!projectId) return
    if (chapters.some((c) => !c.title.trim())) {
      toast({ title: '每个章节都必须有标题', variant: 'warning' })
      return
    }
    try {
      await confirm.mutateAsync({
        projectId,
        chapters: edited ? chapters : null,
      })
      toast({ title: edited ? '已确认编辑后的提纲' : '已沿用 LLM 提纲', variant: 'success' })
      navigate(`/projects/${projectId}/review`)
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '确认失败')
          : '确认失败'
      toast({ title: '确认失败', description: msg, variant: 'destructive' })
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
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.name} · 确认提纲
        </h1>
        <p className="text-sm text-muted-foreground">
          AI 已根据技术需求生成提纲。可直接确认沿用,也可编辑后再启动。共{' '}
          {chapters.length} 章。
        </p>
      </header>

      {!isReady && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-4 text-sm text-amber-900">
            提纲尚未就绪(当前状态:{project.status})。请稍后刷新。
          </CardContent>
        </Card>
      )}

      <div className="space-y-3">
        {chapters.map((c, i) => (
          <Card key={i}>
            <CardHeader className="flex flex-row items-center gap-2 pb-2">
              <GripVertical className="h-4 w-4 cursor-grab text-muted-foreground" />
              <span className="text-xs text-muted-foreground">第 {i + 1} 章</span>
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
                onChange={(e) =>
                  updateChapter(i, { title: e.target.value })
                }
              />
              <Textarea
                value={c.description ?? ''}
                placeholder="章节简介 / 关键要点(可选)"
                rows={2}
                onChange={(e) =>
                  updateChapter(i, { description: e.target.value })
                }
              />
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
              : '未编辑,确认后将沿用 LLM 提纲。'}
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
