import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Loader2,
  Plus,
  Sparkles,
  X,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { useConfirmOutline, useProject, useProjectOutline } from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import { cn } from '@/lib/utils'
import { statusHref } from '@/lib/projectRoute'
import type { OutlineChapterIn } from '@/lib/types'

// PR-M8-2 follow-up:层级目录视图。
//
// 后端 chapters[] 是展平后的叶子,每条带 ``section`` ("1.1" / "2.3.1");
// 前端按 section 前缀重建一级分组,默认折叠每节的 summary/key_points/页数,
// 点击展开才编辑。一级分组(``1`` / ``2``)的标题是 LLM-1 出的同级前缀,
// 但展开形态下不直接编辑(节点不参与 write_chapter,只是目录骨架)。

interface EditableChapter {
  id: string
  serverId: string | null
  section: string
  title: string
  summary: string
  key_points: string[]
  target_pages: number
  selected: boolean
}

let _localIdCounter = 0
function localId(): string {
  _localIdCounter += 1
  return `local-${Date.now()}-${_localIdCounter}`
}

/** 取一级章节编号("1.2.3" → "1");用来分组。 */
function topLevelKey(section: string): string {
  return section.split('.')[0]
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
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!outline.data) return
    setChapters(
      outline.data.chapters.map((c, i) => ({
        id: localId(),
        serverId: c.id,
        // 老项目可能 section=null;按 index+1 兜底
        section: c.section ?? String(i + 1),
        title: c.title,
        summary: c.summary ?? '',
        key_points: c.key_points,
        target_pages: c.target_pages,
        selected: true,
      })),
    )
    setEdited(false)
    setExpanded(new Set())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outline.data?.run_id, outline.data?.chapters.length])

  // 状态跑到目录阶段以外(典型:awaiting_material_understanding 还没确认理解)
  // 时把用户送去对应页面;状态枚举里只有 outline 相关的几个真正属于本页。
  useEffect(() => {
    const status = project.data?.status
    if (!status) return
    const ownStatuses = new Set(['extracting', 'outlining', 'outline_ready', 'queued'])
    if (!ownStatuses.has(status)) {
      const target = statusHref(projectId, status)
      if (target !== `/projects/${projectId}/outline`) {
        navigate(target, { replace: true })
      }
    }
  }, [project.data?.status, navigate, projectId])

  const status = project.data?.status
  const isReady = status === 'outline_ready'
  const isGenerating = status === 'extracting' || status === 'outlining' || status === 'queued'

  const totalPages = chapters.reduce(
    (s, c) => (c.selected ? s + (c.target_pages || 0) : s),
    0,
  )
  const selectedCount = chapters.filter((c) => c.selected).length

  // 按一级 section ("1" / "2" / ...) 分组;每组下挂该前缀的全部叶子。
  const groups = useMemo(() => {
    const byTop = new Map<string, EditableChapter[]>()
    for (const c of chapters) {
      const k = topLevelKey(c.section)
      if (!byTop.has(k)) byTop.set(k, [])
      byTop.get(k)!.push(c)
    }
    // 按 section 自然顺序排:数值升序("1" < "2" < "10"),保留单字符简化
    return [...byTop.entries()].sort((a, b) => {
      const na = Number(a[0])
      const nb = Number(b[0])
      if (Number.isNaN(na) || Number.isNaN(nb)) return a[0].localeCompare(b[0])
      return na - nb
    })
  }, [chapters])

  const updateChapter = (id: string, patch: Partial<EditableChapter>) => {
    setEdited(true)
    setChapters((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)))
  }
  const removeChapter = (id: string) => {
    setEdited(true)
    setChapters((prev) => prev.filter((c) => c.id !== id))
  }
  const addChapter = () => {
    setEdited(true)
    // 新节挂到最末一级章下作 N.M;若空目录则起步 "1.1"
    const lastTop = chapters.length
      ? topLevelKey(chapters[chapters.length - 1].section)
      : '1'
    const siblings = chapters.filter((c) => topLevelKey(c.section) === lastTop)
    const nextSub = siblings.length + 1
    const newSection = `${lastTop}.${nextSub}`
    const newId = localId()
    setChapters((prev) => [
      ...prev,
      {
        id: newId,
        serverId: null,
        section: newSection,
        title: '新节',
        summary: '',
        key_points: ['要点 1'],
        target_pages: 3,
        selected: true,
      },
    ])
    setExpanded((prev) => new Set(prev).add(newId))
  }
  const toggleAllSelected = (nextSelected: boolean) => {
    setEdited(true)
    setChapters((prev) => prev.map((c) => ({ ...c, selected: nextSelected })))
  }
  const toggleExpanded = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleConfirm = async () => {
    if (!projectId) return
    if (selectedCount === 0) {
      toast({ title: '至少要勾选一个章节', variant: 'warning' })
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
          title: `「${c.title}」目标页数需在 1–10`,
          variant: 'warning',
        })
        return
      }
    }
    const payload: OutlineChapterIn[] = edited
      ? chapters.map((c) => ({
          id: c.serverId ?? c.id,
          section: c.section,
          title: c.title.trim(),
          summary: c.summary.trim() || null,
          key_points: c.key_points.map((p) => p.trim()).filter(Boolean),
          target_pages: c.target_pages,
        }))
      : []
    const allSelected = selectedCount === chapters.length
    const selected_chapter_ids = allSelected
      ? null
      : chapters.filter((c) => c.selected).map((c) => (c.serverId ?? c.id) as string)
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
          : `已锁定目录,仅生成 ${selectedCount} / ${chapters.length} 节`,
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
          <div key={i} className="skeleton h-24" />
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

  // ⭐ 目录还没生成完(extracting / outlining / queued):全屏 hero 状态,
  // 让用户清楚知道在等什么,而不是看到一个空的"目录确认"页。
  if (isGenerating || chapters.length === 0) {
    return (
      <div className="mx-auto max-w-3xl px-gutter py-12 page-enter">
        <Button variant="subtle" size="sm" asChild className="mb-8">
          <Link to="/">
            <ArrowLeft aria-hidden="true" className="mr-1 h-4 w-4" />
            返回项目列表
          </Link>
        </Button>
        <div
          role="status"
          aria-live="polite"
          className="border border-rule bg-paper-2 px-8 py-20 text-center"
        >
          <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-accent/10">
            {isGenerating ? (
              <Loader2
                aria-hidden="true"
                className="h-7 w-7 animate-spin motion-reduce:animate-none text-accent"
              />
            ) : (
              <Sparkles aria-hidden="true" className="h-7 w-7 text-accent" />
            )}
          </div>
          <p className="text-meta text-mute mb-3">Outline · Step 3 / 3</p>
          <h1 className="font-display text-h2 text-ink mb-3">
            {isGenerating ? 'AI 正在生成完整目录…' : '目录准备中'}
          </h1>
          <p className="mx-auto max-w-prose text-sm text-mute leading-relaxed">
            根据你的招标文档与确认过的材料理解,LLM-1 正在为本次投标方案输出一份
            层级化的章节目录。生成完成后本页会自动呈现目录,你可以编辑标题、
            勾选要生成的章节,然后锁定进入正文撰写。
          </p>
          <p className="mt-6 text-meta text-mute">
            当前状态 · {status ?? 'unknown'}
          </p>
        </div>
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
        <p className="text-meta text-mute mb-3">Outline · Step 3 / 3</p>
        <h1 className="font-display text-h1 leading-tight text-ink">
          {project.data.name} · 目录确认
        </h1>
        <p className="mt-4 max-w-prose text-sm text-mute">
          层级目录按章 / 节展开,默认折叠详情;展开任一节可编辑标题、要点与
          页数。锁定后进入章节生成阶段(锁定后目录不可逆调整)。
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Badge variant="outline">{groups.length} 章</Badge>
          <Badge variant="outline">{chapters.length} 节</Badge>
          <Badge variant={selectedCount === chapters.length ? 'outline' : 'warn'}>
            勾选 {selectedCount} / {chapters.length}
          </Badge>
          <Badge variant="outline">目标 {totalPages} 页</Badge>
          {edited && <Badge variant="warn">已编辑</Badge>}
          {!isReady && (
            <span className="text-meta text-mute">status · {status}</span>
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

      <ol className="space-y-10">
        {groups.map(([topKey, leaves]) => (
          <li key={topKey} className="border-l border-rule pl-6">
            <p className="font-display text-h3 text-ink mb-4 -ml-7">
              <span className="text-mute mr-3 tabular-nums">{topKey}</span>
              {/* 一级章节没有独立标题字段,用第一个叶子的 section 路径作锚定 */}
              <span className="text-meta text-mute">第 {topKey} 章</span>
            </p>
            <ul className="space-y-2">
              {leaves.map((c) => (
                <TocRow
                  key={c.id}
                  chapter={c}
                  expanded={expanded.has(c.id)}
                  onToggleExpand={() => toggleExpanded(c.id)}
                  onChange={(patch) => updateChapter(c.id, patch)}
                  onRemove={() => removeChapter(c.id)}
                  onToggleSelected={(next) => updateChapter(c.id, { selected: next })}
                />
              ))}
            </ul>
          </li>
        ))}
      </ol>

      <Button
        variant="secondary"
        className="mt-8 w-full border-dashed py-5"
        onClick={addChapter}
      >
        <Plus aria-hidden="true" className="mr-1 h-4 w-4" />
        添加一节
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
              <Loader2
                aria-hidden="true"
                className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
              />
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

function TocRow({
  chapter,
  expanded,
  onToggleExpand,
  onChange,
  onRemove,
  onToggleSelected,
}: {
  chapter: EditableChapter
  expanded: boolean
  onToggleExpand: () => void
  onChange: (patch: Partial<EditableChapter>) => void
  onRemove: () => void
  onToggleSelected: (next: boolean) => void
}) {
  return (
    <li>
      <Card className={cn(!chapter.selected && 'bg-paper-2')}>
        <CardContent className="p-0">
          <div
            className={cn(
              'flex items-center gap-3 px-4 py-3',
              !chapter.selected && 'opacity-60',
            )}
          >
            <input
              type="checkbox"
              checked={chapter.selected}
              onChange={(e) => onToggleSelected(e.target.checked)}
              aria-label={`勾选生成 ${chapter.section}`}
              className="h-4 w-4 cursor-pointer accent-accent"
            />
            <button
              type="button"
              onClick={onToggleExpand}
              className="flex flex-1 items-center gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-sm"
              aria-expanded={expanded}
              aria-label={`${expanded ? '收起' : '展开'} ${chapter.section} ${chapter.title}`}
            >
              {expanded ? (
                <ChevronDown aria-hidden="true" className="h-4 w-4 text-mute shrink-0" />
              ) : (
                <ChevronRight aria-hidden="true" className="h-4 w-4 text-mute shrink-0" />
              )}
              <span className="font-mono text-sm tabular-nums text-mute shrink-0 w-16">
                {chapter.section}
              </span>
              <span className="text-ink font-medium truncate">{chapter.title}</span>
              <span className="ml-auto text-meta text-mute shrink-0">
                {chapter.target_pages} 页
              </span>
            </button>
            <Button
              variant="ghost"
              size="iconSm"
              className="text-mute hover:text-destructive"
              onClick={onRemove}
              aria-label={`移除 ${chapter.section}`}
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </Button>
          </div>
          {expanded && (
            <div className="border-t border-rule px-4 py-4 space-y-3 bg-paper-2/30">
              <Input
                value={chapter.title}
                placeholder="节标题"
                aria-label={`${chapter.section} 标题`}
                onChange={(e) => onChange({ title: e.target.value })}
              />
              <Textarea
                value={chapter.summary}
                placeholder="节简介(可选,会作为本节生成上下文)"
                aria-label={`${chapter.section} 简介`}
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
          )}
        </CardContent>
      </Card>
    </li>
  )
}
