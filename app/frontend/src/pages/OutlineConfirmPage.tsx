import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  GripVertical,
  Loader2,
  Plus,
  X,
} from 'lucide-react'
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Card, CardContent } from '@/components/ui/card'
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
import { cn } from '@/lib/utils'
import type { OutlineChapterIn } from '@/lib/types'

// PR-M8-2:editorial 提纲编辑 — 拖拽排序 + 增删改 + 锁定目录。
// MVP 保留扁平结构(chapters[]),不引入树形嵌套;@dnd-kit 提供拖拽体验。
// 业务逻辑、确认端点契约 (PUT /api/projects/{id}/outline) 不变。

interface EditableChapter {
  id: string
  serverId: string | null
  title: string
  summary: string
  key_points: string[]
  target_pages: number
  // PR-M9-1:用户是否勾选生成本章;默认 true。
  selected: boolean
}

let _localIdCounter = 0
function localId(): string {
  _localIdCounter += 1
  return `local-${Date.now()}-${_localIdCounter}`
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
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 4 },
    }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )

  useEffect(() => {
    if (!outline.data) return
    setChapters(
      outline.data.chapters.map((c) => ({
        id: localId(),
        serverId: c.id,
        title: c.title,
        summary: c.summary ?? '',
        key_points: c.key_points,
        target_pages: c.target_pages,
        selected: true,
      })),
    )
    setEdited(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outline.data?.run_id, outline.data?.chapters.length])

  const isReady = project.data?.status === 'outline_ready'
  const totalPages = chapters.reduce(
    (s, c) => (c.selected ? s + (c.target_pages || 0) : s),
    0,
  )
  const selectedCount = chapters.filter((c) => c.selected).length

  const updateChapter = (id: string, patch: Partial<EditableChapter>) => {
    setEdited(true)
    setChapters((prev) =>
      prev.map((c) => (c.id === id ? { ...c, ...patch } : c)),
    )
  }
  const removeChapter = (id: string) => {
    setEdited(true)
    setChapters((prev) => prev.filter((c) => c.id !== id))
  }
  const addChapter = () => {
    setEdited(true)
    setChapters((prev) => [
      ...prev,
      {
        id: localId(),
        serverId: null,
        title: '新章节',
        summary: '',
        key_points: ['要点 1'],
        target_pages: 3,
        selected: true,
      },
    ])
  }
  const toggleAllSelected = (nextSelected: boolean) => {
    setEdited(true)
    setChapters((prev) =>
      prev.map((c) => ({ ...c, selected: nextSelected })),
    )
  }
  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    setEdited(true)
    setChapters((prev) => {
      const fromIdx = prev.findIndex((c) => c.id === active.id)
      const toIdx = prev.findIndex((c) => c.id === over.id)
      if (fromIdx < 0 || toIdx < 0) return prev
      return arrayMove(prev, fromIdx, toIdx)
    })
  }

  const handleConfirm = async () => {
    if (!projectId) return
    if (selectedCount === 0) {
      toast({
        title: '至少要勾选一个章节',
        variant: 'warning',
      })
      return
    }
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
          // PR-M9-1 fix:新章节没有 serverId 时退回 local id,
          // 让后端 pick_chapter 能在 selected_chapter_ids 里匹配到。
          id: c.serverId ?? c.id,
          title: c.title.trim(),
          summary: c.summary.trim() || null,
          key_points: c.key_points.map((p) => p.trim()).filter(Boolean),
          target_pages: c.target_pages,
        }))
      : []
    // PR-M9-1:勾选状态非全选时,把选中章节 id 一并发给后端。
    // 用与 payload 同一套 id 策略 (serverId ?? local id),保证新章节也能被选中。
    const allSelected = selectedCount === chapters.length
    const selected_chapter_ids = allSelected
      ? null
      : chapters
          .filter((c) => c.selected)
          .map((c) => (c.serverId ?? c.id) as string)
    try {
      await confirm.mutateAsync({
        projectId,
        chapters: payload,
        selected_chapter_ids,
      })
      toast({
        title: allSelected
          ? edited
            ? '已锁定编辑后的目录'
            : '已沿用 AI 生成的目录'
          : `已锁定目录,仅生成 ${selectedCount} / ${chapters.length} 章`,
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
      <div className="mx-auto max-w-4xl px-gutter py-12 space-y-4">
        <div className="skeleton h-8 w-1/3" />
        <div className="skeleton h-4 w-1/2" />
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="skeleton h-32" />
        ))}
      </div>
    )
  }
  if (!project.data) {
    return (
      <div className="mx-auto max-w-4xl px-gutter py-12 text-sm text-destructive">
        项目不存在或无访问权限
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-5xl px-gutter py-12 page-enter">
      <Button variant="subtle" size="sm" asChild className="mb-8">
        <Link to="/">
          <ArrowLeft aria-hidden="true" className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="mb-12 border-b border-rule pb-8">
        <p className="text-meta text-mute mb-3">
          Outline · Step 3 / 3
        </p>
        <h1 className="font-display text-h1 leading-tight text-ink">
          {project.data.name} · 目录确认
        </h1>
        <p className="mt-4 max-w-prose text-sm text-mute">
          拖拽 <GripVertical aria-hidden="true" className="inline h-3 w-3 align-middle" /> 调整顺序;直接编辑标题与关键要点;
          锁定后进入章节生成阶段 (锁定后目录不可逆调整)。
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Badge variant="outline">{chapters.length} 章</Badge>
          <Badge variant={selectedCount === chapters.length ? 'outline' : 'warn'}>
            勾选 {selectedCount} / {chapters.length}
          </Badge>
          <Badge variant="outline">目标 {totalPages} 页</Badge>
          {edited && <Badge variant="warn">已编辑</Badge>}
          {!isReady && (
            <span className="text-meta text-mute">
              status · {project.data.status}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => toggleAllSelected(true)}
              disabled={selectedCount === chapters.length}
            >
              全选
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => toggleAllSelected(false)}
              disabled={selectedCount === 0}
            >
              全不选
            </Button>
          </div>
        </div>
      </header>

      {!isReady && (
        <div
          role="status"
          aria-live="polite"
          className="mb-10 relative flex items-start gap-3 border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn before:absolute before:left-0 before:right-0 before:top-0 before:h-px before:bg-warn"
        >
          <Loader2 aria-hidden="true" className="mt-0.5 h-4 w-4 animate-spin motion-reduce:animate-none" />
          <span className="flex-1 leading-relaxed">
            目录尚未就绪 (当前状态:{project.data.status})。LLM-1 仍在生成,稍后自动刷新。
          </span>
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={handleDragEnd}
      >
        <SortableContext
          items={chapters.map((c) => c.id)}
          strategy={verticalListSortingStrategy}
        >
          <ul className="space-y-4">
            {chapters.map((c, i) => (
              <SortableChapter
                key={c.id}
                chapter={c}
                index={i}
                onChange={(patch) => updateChapter(c.id, patch)}
                onRemove={() => removeChapter(c.id)}
                onToggleSelected={(next) =>
                  updateChapter(c.id, { selected: next })
                }
              />
            ))}
          </ul>
        </SortableContext>
      </DndContext>

      <Button
        variant="secondary"
        className="mt-6 w-full border-dashed py-5"
        onClick={addChapter}
      >
        <Plus aria-hidden="true" className="mr-1 h-4 w-4" />
        添加章节
      </Button>

      <div className="mt-16 border-t border-rule pt-8 flex flex-col items-stretch gap-3 sm:flex-row sm:justify-end">
        <Button asChild variant="ghost">
          <Link to={`/projects/${projectId}/upload`}>返回上传</Link>
        </Button>
        <Button
          onClick={handleConfirm}
          disabled={confirm.isPending}
          size="lg"
          variant="accent"
        >
          {confirm.isPending ? (
            <>
              <Loader2 aria-hidden="true" className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" />
              锁定中…
            </>
          ) : (
            <>
              锁定目录 · 开始生成章节
              <ArrowRight aria-hidden="true" className="ml-2 h-4 w-4" />
            </>
          )}
        </Button>
      </div>
    </div>
  )
}

function SortableChapter({
  chapter,
  index,
  onChange,
  onRemove,
  onToggleSelected,
}: {
  chapter: EditableChapter
  index: number
  onChange: (patch: Partial<EditableChapter>) => void
  onRemove: () => void
  onToggleSelected: (next: boolean) => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: chapter.id })

  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : chapter.selected ? 1 : 0.55,
  }

  return (
    <li ref={setNodeRef} style={style}>
      <Card
        className={cn(
          isDragging && 'ring-2 ring-accent',
          !chapter.selected && 'bg-paper-2',
        )}
      >
        <CardContent className="grid grid-cols-12 gap-4 p-6">
          <div className="col-span-1 flex flex-col items-center gap-2">
            <input
              type="checkbox"
              checked={chapter.selected}
              onChange={(e) => onToggleSelected(e.target.checked)}
              aria-label={`勾选生成第 ${index + 1} 章`}
              className="h-4 w-4 cursor-pointer accent-accent"
            />
            <button
              type="button"
              className="cursor-grab text-mute hover:text-ink"
              aria-label={`拖拽第 ${index + 1} 章重排`}
              {...attributes}
              {...listeners}
            >
              <GripVertical className="h-5 w-5" />
            </button>
            <span className="font-display text-h3 tabular-nums leading-none text-mute">
              {String(index + 1).padStart(2, '0')}
            </span>
          </div>
          <div className="col-span-10 space-y-3">
            <Input
              value={chapter.title}
              placeholder="章节标题"
              aria-label={`第 ${index + 1} 章标题`}
              className="font-display text-h3 px-0"
              onChange={(e) => onChange({ title: e.target.value })}
            />
            <Textarea
              value={chapter.summary}
              placeholder="章节简介(可选,会作为本章生成上下文)"
              aria-label={`第 ${index + 1} 章简介`}
              rows={2}
              onChange={(e) => onChange({ summary: e.target.value })}
            />
            <div className="grid grid-cols-12 gap-3">
              <div className="col-span-9">
                <label
                  htmlFor={`chapter-keypoints-${chapter.id}`}
                  className="text-meta text-mute"
                >
                  关键要点 · 每行一条
                </label>
                <Textarea
                  id={`chapter-keypoints-${chapter.id}`}
                  value={chapter.key_points.join('\n')}
                  rows={3}
                  className="mt-1"
                  onChange={(e) =>
                    onChange({ key_points: e.target.value.split('\n') })
                  }
                />
              </div>
              <div className="col-span-3">
                <label
                  htmlFor={`chapter-pages-${chapter.id}`}
                  className="text-meta text-mute"
                >
                  目标页数
                </label>
                <Input
                  id={`chapter-pages-${chapter.id}`}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={10}
                  value={chapter.target_pages}
                  className="mt-1 text-center"
                  onChange={(e) =>
                    onChange({
                      target_pages: Math.max(
                        1,
                        Math.min(10, Number(e.target.value) || 1),
                      ),
                    })
                  }
                />
                <p className="text-meta text-mute mt-1">1–10 页</p>
              </div>
            </div>
          </div>
          <div className="col-span-1 flex justify-end">
            <Button
              variant="ghost"
              size="iconSm"
              className="text-mute hover:text-destructive"
              onClick={onRemove}
              aria-label={`移除第 ${index + 1} 章`}
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </li>
  )
}
