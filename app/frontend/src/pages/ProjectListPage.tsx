import { Link, useNavigate } from 'react-router-dom'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useDeleteProject, useProjects } from '@/api/projects'
import { useCurrentUser } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { confirmDialog } from '@/components/ConfirmDialog'
import { cn } from '@/lib/utils'
import type { ProjectDTO, ProjectStatus } from '@/lib/types'

const STATUS_LABEL: Record<ProjectStatus, string> = {
  init: '草稿',
  queued: '排队中',
  extracting: '解析文档',
  awaiting_material_understanding: '待确认材料理解',
  outlining: '生成提纲',
  outline_ready: '提纲待确认',
  running: '生成中',
  awaiting_review: '待审核',
  done: '已完成',
  failed: '失败',
  aborted: '已中止',
  aborted_v1: 'v1 项目已废弃',
  aborted_schema_v1: 'v1 schema 已弃用',
}

const STATUS_VARIANT: Record<
  ProjectStatus,
  'secondary' | 'warning' | 'success' | 'destructive' | 'outline' | 'muted'
> = {
  init: 'outline',
  queued: 'muted',
  extracting: 'muted',
  awaiting_material_understanding: 'warning',
  outlining: 'muted',
  outline_ready: 'warning',
  running: 'muted',
  awaiting_review: 'warning',
  done: 'success',
  failed: 'destructive',
  aborted: 'outline',
  aborted_v1: 'outline',
  aborted_schema_v1: 'outline',
}

// R-12 进度感知:点击卡片前用户能看到「下一步该做什么」的简短提示。
const STAGE_HINT: Record<ProjectStatus, string> = {
  init: '点击进入,上传招标文档',
  queued: '排队中,等待并发名额释放后自动启动',
  extracting: 'AI 正在解析上传的招标文档',
  awaiting_material_understanding: 'LLM 已读完材料,点击进入确认理解',
  outlining: 'AI 正在生成方案提纲',
  outline_ready: '提纲已就绪,点击进入确认',
  running: '正在生成章节,可进入查看进度',
  awaiting_review: '有章节等你审核,点击进入',
  done: '方案已完成,点击查看全文与下载',
  failed: '工作流失败,点击进入查看失败章节',
  aborted: '项目已中止',
  aborted_v1: 'v1 旧项目,v2 升级后已废弃 — 请重建',
  aborted_schema_v1: 'v1 checkpoint 无法在 v2 graph 上 resume,请重建',
}

const ACTIVE_STATUSES = new Set<ProjectStatus>([
  'queued',
  'extracting',
  'outlining',
  'running',
])

// PR-UI-2 retrofit:editorial 列表 — 左侧大数字 + 中间 serif 标题 + 右侧 meta。
export function ProjectListPage() {
  const projects = useProjects()
  const remove = useDeleteProject()
  const navigate = useNavigate()
  const { data: me } = useCurrentUser()
  const { toast } = useToast()

  const handleDelete = async (project: ProjectDTO) => {
    const ok = await confirmDialog({
      title: `删除项目「${project.name}」?`,
      description: (
        <span>
          将连带磁盘文件、章节、token 消费记录一起清除,且
          <strong className="text-destructive"> 不可恢复</strong>。
        </span>
      ),
      confirmText: '删除',
      destructive: true,
    })
    if (!ok) return
    try {
      await remove.mutateAsync(project.id)
      toast({ title: '已删除', variant: 'success' })
    } catch {
      toast({ title: '删除失败', variant: 'destructive' })
    }
  }

  const goToProject = (p: ProjectDTO) => {
    if (p.status === 'init') navigate(`/projects/${p.id}/upload`)
    else if (
      p.status === 'queued' ||
      p.status === 'extracting' ||
      p.status === 'outlining' ||
      p.status === 'outline_ready'
    )
      navigate(`/projects/${p.id}/outline`)
    else if (p.status === 'done') navigate(`/projects/${p.id}/proposal`)
    else navigate(`/projects/${p.id}/review`)
  }

  return (
    <div className="mx-auto max-w-6xl px-gutter py-12 page-enter">
      <header className="mb-12 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-8">
        <div>
          <p className="text-meta text-mute mb-3">Projects · 团队共享池</p>
          <h1 className="font-display text-hero leading-tight text-ink">
            项目
          </h1>
          <p className="mt-4 max-w-prose text-sm text-mute">
            任何成员可审核任何项目;只有创建者与管理员能删除。
          </p>
        </div>
        <Button asChild size="lg" variant="default">
          <Link to="/projects/new">
            <Plus className="mr-2 h-4 w-4" />
            新建项目
          </Link>
        </Button>
      </header>

      {projects.isLoading && <ProjectListSkeleton />}

      {projects.data && projects.data.length === 0 && (
        <div className="border border-rule bg-paper-2 px-gutter py-24 text-center">
          <p className="text-meta text-mute mb-3">空空如也</p>
          <p className="font-display text-h2 text-ink">还没有项目</p>
          <p className="mx-auto mt-4 max-w-prose text-sm text-mute">
            创建你的第一个投标方案,跟着工作流一步步生成全文。
          </p>
          <Button asChild className="mt-8">
            <Link to="/projects/new">
              <Plus className="mr-2 h-4 w-4" />
              新建项目
            </Link>
          </Button>
        </div>
      )}

      {projects.data && projects.data.length > 0 && (
        <ul className="divide-y divide-rule border-y border-rule">
          {projects.data.map((p, idx) => {
            const canDelete = me?.role === 'admin' || me?.id === p.created_by
            const isActive = ACTIVE_STATUSES.has(p.status)
            return (
              <li key={p.id}>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => goToProject(p)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      goToProject(p)
                    }
                  }}
                  className={cn(
                    'group grid grid-cols-12 gap-6 px-2 py-8 cursor-pointer',
                    'transition-colors duration-150 hover:bg-paper-2',
                    'focus-visible:outline-none focus-visible:bg-paper-2',
                  )}
                >
                  {/* 左侧大数字 — editorial 信号 */}
                  <div className="col-span-2 md:col-span-1">
                    <p className="font-display text-h1 tabular-nums leading-none text-mute">
                      {String(idx + 1).padStart(2, '0')}
                    </p>
                  </div>
                  {/* 中间 serif 标题 + 描述 */}
                  <div className="col-span-7 md:col-span-7 min-w-0">
                    <div className="flex items-center gap-3 mb-2">
                      <Badge variant={STATUS_VARIANT[p.status]}>
                        {isActive && (
                          <span
                            aria-hidden
                            className="inline-block h-1.5 w-1.5 animate-pulse-soft rounded-full bg-current"
                          />
                        )}
                        {STATUS_LABEL[p.status]}
                      </Badge>
                      <span className="text-meta text-mute">
                        #{p.id}
                      </span>
                    </div>
                    <p className="font-display text-h3 leading-snug text-ink line-clamp-2">
                      {p.name}
                    </p>
                    {p.description && (
                      <p className="mt-2 max-w-prose text-sm text-mute line-clamp-2">
                        {p.description}
                      </p>
                    )}
                    <p className="mt-3 text-meta text-mute">
                      {STAGE_HINT[p.status]}
                    </p>
                  </div>
                  {/* 右侧 meta + 操作 */}
                  <div className="col-span-3 md:col-span-4 flex flex-col items-end justify-between gap-3 text-right">
                    <div className="space-y-1">
                      <p className="text-meta text-mute">
                        {new Date(p.created_at).toLocaleDateString('zh-CN', {
                          year: 'numeric',
                          month: '2-digit',
                          day: '2-digit',
                        })}
                      </p>
                      <p className="text-meta text-mute">
                        创建者 {p.created_by_username ?? `#${p.created_by}`}
                      </p>
                    </div>
                    <div
                      className="flex items-center gap-1"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {canDelete && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDelete(p)}
                          disabled={remove.isPending}
                          className="text-mute hover:text-destructive"
                        >
                          <Trash2 className="mr-1 h-3.5 w-3.5" />
                          删除
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function ProjectListSkeleton() {
  return (
    <ul className="divide-y divide-rule border-y border-rule">
      {Array.from({ length: 4 }).map((_, i) => (
        <li
          key={i}
          className="grid grid-cols-12 gap-6 px-2 py-8 animate-pulse"
        >
          <div className="col-span-1 skeleton h-10 w-12" />
          <div className="col-span-7 space-y-3">
            <div className="skeleton h-4 w-1/4" />
            <div className="skeleton h-6 w-3/5" />
            <div className="skeleton h-3 w-4/5" />
          </div>
          <div className="col-span-4 flex flex-col items-end gap-2">
            <div className="skeleton h-3 w-20" />
            <div className="skeleton h-3 w-16" />
          </div>
        </li>
      ))}
    </ul>
  )
}
