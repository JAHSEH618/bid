import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Loader2, Sparkles, Wand2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { useConfirmOutline, useProject, useProjectOutline } from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import { statusHref } from '@/lib/projectRoute'
import type { OutlineChapterDTO, OutlineChapterIn } from '@/lib/types'

// PR-M8-2 follow-up #2:单文本框 TOC 编辑。
//
// 视图:一个大 textarea 显示完整层级目录(最多 4 级,用 # / ## / ### / ####
// 标记)。用户可以直接编辑 / 增删行;或写一段反馈让模型重新生成。
//
// 提交时把 textarea 解析回 chapters[]:
//   - 每行匹配 ``^(#{1,4})\s+(\d+(?:\.\d+)*)\s+(.*)$`` 拿到 level / section / title
//   - 叶子(下一行 level <= 当前 level)进入 chapters,继承原 LLM-1 输出的
//     summary / key_points / target_pages(按 section 索引匹配),
//     新增节用默认值
//   - 没有 section 编号的行兜底自动按层级补编号

const SECTION_LINE_RE = /^(#{1,4})\s+(\d+(?:\.\d+){0,3})\s+(.*)$/
const DEFAULT_KEY_POINTS = ['核心要点 1', '核心要点 2', '核心要点 3']
const DEFAULT_TARGET_PAGES = 3

interface ParsedNode {
  level: number // 1 / 2 / 3 / 4
  section: string
  title: string
}

function parseTocText(text: string): ParsedNode[] {
  const lines = text.split('\n')
  const out: ParsedNode[] = []
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) continue
    const m = line.match(SECTION_LINE_RE)
    if (!m) continue
    const level = m[1].length
    const section = m[2]
    const title = m[3].trim()
    if (!title) continue
    out.push({ level, section, title })
  }
  return out
}

/** 没有显式 ## / ### 时,允许用户用纯 "1.2 标题" / "1.2.3 标题" 写;按 . 数推断 level。 */
function parseTocTextLenient(text: string): ParsedNode[] {
  const strict = parseTocText(text)
  if (strict.length > 0) return strict
  const out: ParsedNode[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    // 容忍 "## 1.2 X" 与 "1.2 X" 两种;后者按 dot 数推 level
    const sectionMatch = line.match(/^(\d+(?:\.\d+){0,3})\s+(.*)$/)
    if (!sectionMatch) continue
    const section = sectionMatch[1]
    const title = sectionMatch[2].trim()
    if (!title) continue
    const level = section.split('.').length
    out.push({ level: Math.min(4, level), section, title })
  }
  return out
}

/** 把节点列表展平成 chapters[](只取叶子)。 */
function nodesToChapters(
  nodes: ParsedNode[],
  metaBySection: Map<string, OutlineChapterDTO>,
): OutlineChapterIn[] {
  const chapters: OutlineChapterIn[] = []
  for (let i = 0; i < nodes.length; i++) {
    const cur = nodes[i]
    const next = nodes[i + 1]
    // 下一个节点 level 更深 → 当前是分组,不进 chapters
    if (next && next.level > cur.level) continue
    const meta = metaBySection.get(cur.section)
    chapters.push({
      id: meta?.id ?? null,
      section: cur.section,
      title: cur.title,
      summary: meta?.summary ?? null,
      key_points: meta?.key_points.length ? meta.key_points : DEFAULT_KEY_POINTS,
      target_pages: meta?.target_pages ?? DEFAULT_TARGET_PAGES,
    })
  }
  return chapters
}

/** chapters[] → markdown 文本(供 textarea 初始化和重写后回灌)。 */
function chaptersToTocText(chapters: OutlineChapterDTO[]): string {
  // 把扁平叶子重建出非叶子分组行:遍历 section 前缀,首次见到的前缀作为分组发一行
  const lines: string[] = []
  const seenPrefixes = new Set<string>()

  for (const c of chapters) {
    const section = c.section ?? ''
    if (!section) {
      // 老项目 section=null:用 index+1 当 section,单层
      lines.push(`# ${c.index + 1} ${c.title}`)
      continue
    }
    const parts = section.split('.')
    // 先发分组行(level=1..parts.length-1),title 用空("项目目录")
    for (let i = 1; i < parts.length; i++) {
      const prefix = parts.slice(0, i).join('.')
      if (!seenPrefixes.has(prefix)) {
        seenPrefixes.add(prefix)
        lines.push(`${'#'.repeat(i)} ${prefix} 章节分组`)
      }
    }
    // 叶子行(深度 = parts.length)
    const level = Math.min(4, parts.length)
    lines.push(`${'#'.repeat(level)} ${section} ${c.title}`)
  }
  return lines.join('\n')
}

export function OutlineConfirmPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const project = useProject(projectId)
  const outline = useProjectOutline(projectId)
  const confirm = useConfirmOutline()

  const [tocText, setTocText] = useState('')
  const [feedback, setFeedback] = useState('')
  const [revising, setRevising] = useState(false)
  // 把上一次从 /outline 拉到的 chapters 镜像存下来,用 section → meta 索引
  // 把 key_points / target_pages / summary 带回 submit。
  const lastChaptersRef = useRef<OutlineChapterDTO[]>([])

  useEffect(() => {
    const chs = outline.data?.chapters ?? []
    if (chs.length === 0) return
    lastChaptersRef.current = chs
    // 用户在 textarea 改过但还没提交:避免覆盖,只在第一次 / chapters 真换了
    // 才重置。判定:if textarea 跟旧重建一致或为空。
    const rebuilt = chaptersToTocText(chs)
    setTocText((cur) => {
      if (!cur.trim()) return rebuilt
      const prevRebuilt = chaptersToTocText(lastChaptersRef.current)
      return cur === prevRebuilt ? rebuilt : cur
    })
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

  // 实时解析当前 textarea,显示节点统计(用户写错时也能看到节数)
  const parsedSummary = useMemo(() => {
    const nodes = parseTocTextLenient(tocText)
    const leaves: ParsedNode[] = []
    const groups: ParsedNode[] = []
    for (let i = 0; i < nodes.length; i++) {
      const next = nodes[i + 1]
      if (next && next.level > nodes[i].level) groups.push(nodes[i])
      else leaves.push(nodes[i])
    }
    return { leaves: leaves.length, groups: groups.length }
  }, [tocText])

  const handleConfirm = async () => {
    if (!projectId) return
    const nodes = parseTocTextLenient(tocText)
    if (nodes.length === 0) {
      toast({
        title: '目录为空',
        description: '请至少填一行 "# 1 标题",或让模型重新生成',
        variant: 'warning',
      })
      return
    }
    const metaBySection = new Map<string, OutlineChapterDTO>()
    for (const c of lastChaptersRef.current) {
      if (c.section) metaBySection.set(c.section, c)
    }
    const chapters = nodesToChapters(nodes, metaBySection)
    if (chapters.length === 0) {
      toast({
        title: '没有可生成的章节',
        description: '至少需要一个叶子节点(没有下级的最深一行)',
        variant: 'warning',
      })
      return
    }
    try {
      await confirm.mutateAsync({
        projectId,
        decision: 'confirm',
        chapters,
        selected_chapter_ids: null,
      })
      toast({
        title: `已锁定目录(${chapters.length} 节)`,
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

  const handleRevise = async () => {
    if (!projectId) return
    const fb = feedback.trim()
    if (!fb) {
      toast({
        title: '请填写修改意见',
        description: '告诉模型哪里要改 / 加 / 减,它会重新出一版完整目录',
        variant: 'warning',
      })
      return
    }
    setRevising(true)
    try {
      await confirm.mutateAsync({
        projectId,
        decision: 'revise',
        chapters: [],
        feedback: fb,
      })
      toast({
        title: 'LLM-1 正在按反馈重新生成目录',
        description: '本页会自动刷新',
        variant: 'success',
      })
      setFeedback('')
    } catch (err) {
      toast({
        title: '提交失败',
        description: readApiError(err, '提交失败'),
        variant: 'destructive',
      })
    } finally {
      setRevising(false)
    }
  }

  if (project.isLoading || outline.isLoading) {
    return (
      <div className="mx-auto max-w-4xl px-gutter py-12 space-y-4">
        <div className="skeleton h-8 w-1/3" />
        <div className="skeleton h-4 w-1/2" />
        <div className="skeleton h-64" />
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

  // 目录还没生成完(extracting / outlining / queued):全屏 hero 状态,
  // 让用户清楚知道在等什么。
  const noChaptersYet = (outline.data?.chapters ?? []).length === 0
  if (isGenerating || noChaptersYet) {
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
            最多 4 级的层级目录。生成完成后本页会自动呈现完整目录,可直接在
            文本框内编辑,或写一段反馈让模型重新生成。
          </p>
          <p className="mt-6 text-meta text-mute">
            当前状态 · {status ?? 'unknown'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl px-gutter py-12 page-enter">
      <Button variant="subtle" size="sm" asChild className="mb-8">
        <Link to="/">
          <ArrowLeft aria-hidden="true" className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="mb-10 border-b border-rule pb-8">
        <p className="text-meta text-mute mb-3">Outline · Step 3 / 3</p>
        <h1 className="font-display text-h1 leading-tight text-ink">
          {project.data.name} · 目录确认
        </h1>
        <p className="mt-4 max-w-prose text-sm text-mute">
          下面是 LLM-1 生成的完整层级目录(最多 4 级)。每行格式为
          ``# 1 标题`` / ``## 1.1 标题`` / ``### 1.1.1 标题`` /
          ``#### 1.1.1.1 标题``。可直接在文本框里编辑、增删、调换顺序;
          也可以写一段反馈让模型整体重写。锁定后进入章节生成阶段(锁定后
          目录不可逆调整)。
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Badge variant="outline">{parsedSummary.leaves} 节</Badge>
          <Badge variant="outline">{parsedSummary.groups} 组</Badge>
          {!isReady && (
            <span className="text-meta text-mute">status · {status}</span>
          )}
        </div>
      </header>

      <label
        htmlFor="toc-textarea"
        className="text-meta text-mute mb-2 block"
      >
        完整目录(每行一项,# 数量决定层级)
      </label>
      <Textarea
        id="toc-textarea"
        value={tocText}
        onChange={(e) => setTocText(e.target.value)}
        rows={22}
        spellCheck={false}
        className="font-mono text-sm leading-7"
        placeholder="# 1 项目背景&#10;## 1.1 招标方现状&#10;### 1.1.1 公司规模&#10;#### 1.1.1.1 总部与分支&#10;## 1.2 项目需求理解&#10;# 2 技术方案&#10;## 2.1 总体架构"
      />

      <section className="mt-10 border border-rule p-6">
        <div className="flex items-start gap-3 mb-4">
          <Wand2 aria-hidden="true" className="h-5 w-5 mt-0.5 text-accent shrink-0" />
          <div>
            <p className="font-display text-h3 text-ink">让模型修改</p>
            <p className="mt-1 text-sm text-mute leading-relaxed">
              给 LLM-1 一段反馈,它会**整体**重新生成一版完整目录(覆盖
              当前文本框内容)。例如:「第二章拆得太细,合并 2.3 和 2.4」
              / 「补一节风险管控」。
            </p>
          </div>
        </div>
        <Textarea
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          rows={4}
          placeholder="写下要 LLM-1 调整的方向 / 缺漏 / 重点……"
        />
        <div className="mt-3 flex justify-end">
          <Button
            type="button"
            variant="secondary"
            onClick={handleRevise}
            disabled={revising || !feedback.trim()}
          >
            {revising ? (
              <>
                <Loader2
                  aria-hidden="true"
                  className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none"
                />
                提交中…
              </>
            ) : (
              <>
                <Wand2 aria-hidden="true" className="mr-2 h-4 w-4" />
                请模型重写
              </>
            )}
          </Button>
        </div>
      </section>

      <div className="mt-12 border-t border-rule pt-8 flex flex-col items-stretch gap-3 sm:flex-row sm:justify-end">
        <Button asChild variant="ghost">
          <Link to={`/projects/${projectId}/upload`}>返回上传</Link>
        </Button>
        <Button
          onClick={handleConfirm}
          disabled={confirm.isPending || revising}
          size="lg"
          variant="accent"
        >
          {confirm.isPending && !revising ? (
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
