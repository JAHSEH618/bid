import { Link, useNavigate } from 'react-router-dom'
import { Plus, Trash2, ExternalLink, FolderPlus, Sparkles } from 'lucide-react'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useDeleteProject, useProjects } from '@/api/projects'
import { useCurrentUser } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { confirmDialog } from '@/components/ConfirmDialog'
import type { ProjectDTO, ProjectStatus } from '@/lib/types'

const STATUS_LABEL: Record<ProjectStatus, string> = {
  init: '草稿',
  queued: '排队中',
  extracting: '解析文档',
  outlining: '生成提纲',
  outline_ready: '提纲待确认',
  running: '生成中',
  awaiting_review: '待审核',
  done: '已完成',
  failed: '失败',
  aborted: '已中止',
}

const STATUS_VARIANT: Record<
  ProjectStatus,
  'secondary' | 'info' | 'warning' | 'success' | 'destructive' | 'outline'
> = {
  init: 'secondary',
  queued: 'info',
  extracting: 'info',
  outlining: 'info',
  outline_ready: 'warning',
  running: 'info',
  awaiting_review: 'warning',
  done: 'success',
  failed: 'destructive',
  aborted: 'outline',
}

// R-12 进度感知:点击卡片前用户能看到「下一步该做什么」的简短提示。
const STAGE_HINT: Record<ProjectStatus, string> = {
  init: '点击进入,上传 3 份招标文档',
  queued: '排队中,等待并发名额释放后自动启动',
  extracting: 'AI 正在解析上传的招标文档',
  outlining: 'AI 正在生成方案提纲',
  outline_ready: '提纲已就绪,点击进入确认',
  running: '正在生成章节,可进入查看进度',
  awaiting_review: '有章节等你审核,点击进入',
  done: '方案已完成,点击查看全文与下载',
  failed: '工作流失败,点击进入查看失败章节',
  aborted: '项目已中止',
}

const ACTIVE_STATUSES = new Set<ProjectStatus>([
  'queued',
  'extracting',
  'outlining',
  'running',
])

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
    <div className="container max-w-6xl space-y-6 py-8 page-enter">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">项目列表</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            团队共享池:任何成员可审核任何项目;只有创建者与管理员能删除
          </p>
        </div>
        <Button asChild size="lg">
          <Link to="/projects/new">
            <Plus className="mr-1.5 h-4 w-4" />
            新建项目
          </Link>
        </Button>
      </header>

      {projects.isLoading && <ProjectGridSkeleton />}

      {projects.data && projects.data.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center gap-3 py-16 text-center">
            <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-muted">
              <FolderPlus className="h-7 w-7 text-muted-foreground" />
            </div>
            <div className="space-y-1">
              <p className="text-base font-medium text-foreground">
                还没有项目
              </p>
              <p className="text-sm text-muted-foreground">
                创建你的第一个投标方案,跟着工作流一步步生成全文
              </p>
            </div>
            <Button asChild className="mt-2">
              <Link to="/projects/new">
                <Plus className="mr-1.5 h-4 w-4" />
                新建项目
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {projects.data && projects.data.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {projects.data.map((p) => {
            const canDelete = me?.role === 'admin' || me?.id === p.created_by
            const isActive = ACTIVE_STATUSES.has(p.status)
            return (
              <Card
                key={p.id}
                role="button"
                tabIndex={0}
                onClick={() => goToProject(p)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    goToProject(p)
                  }
                }}
                className="group relative cursor-pointer overflow-hidden border-border/70 transition-[transform,border-color,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
              >
                {/* 顶部色条:状态视觉标识 */}
                <div
                  className={
                    p.status === 'done'
                      ? 'absolute inset-x-0 top-0 h-1 bg-emerald-400'
                      : p.status === 'awaiting_review' ||
                          p.status === 'outline_ready'
                        ? 'absolute inset-x-0 top-0 h-1 bg-amber-400'
                        : p.status === 'failed'
                          ? 'absolute inset-x-0 top-0 h-1 bg-destructive'
                          : isActive
                            ? 'absolute inset-x-0 top-0 h-1 bg-sky-400'
                            : 'absolute inset-x-0 top-0 h-1 bg-transparent'
                  }
                />
                <CardHeader className="pb-3 pt-5">
                  <div className="flex items-start justify-between gap-3">
                    <CardTitle className="line-clamp-1 text-[15px] leading-snug">
                      {p.name}
                    </CardTitle>
                    <Badge variant={STATUS_VARIANT[p.status]}>
                      {isActive && (
                        <span
                          aria-hidden
                          className="inline-block h-1.5 w-1.5 animate-pulse-soft rounded-full bg-current"
                        />
                      )}
                      {STATUS_LABEL[p.status]}
                    </Badge>
                  </div>
                  {p.description && (
                    <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                      {p.description}
                    </p>
                  )}
                </CardHeader>
                <CardContent className="space-y-3 text-xs text-muted-foreground">
                  <div className="flex items-center justify-between">
                    <span>创建者 #{p.created_by}</span>
                    <span>
                      {new Date(p.created_at).toLocaleDateString('zh-CN')}
                    </span>
                  </div>
                  <p className="flex items-start gap-1.5 rounded-md bg-muted/60 px-2.5 py-2 text-[12px] leading-relaxed text-muted-foreground/95">
                    <Sparkles className="mt-0.5 h-3 w-3 shrink-0 text-primary/70" />
                    {STAGE_HINT[p.status]}
                  </p>
                  <div
                    className="flex items-center justify-end gap-1"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => goToProject(p)}
                    >
                      <ExternalLink className="mr-1 h-3.5 w-3.5" />
                      打开
                    </Button>
                    {canDelete && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(p)}
                        disabled={remove.isPending}
                        className="text-muted-foreground hover:text-destructive"
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        删除
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ProjectGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i} className="border-border/70">
          <CardHeader className="pb-3">
            <div className="flex items-start justify-between gap-3">
              <div className="skeleton h-5 w-2/3" />
              <div className="skeleton h-5 w-16 rounded-full" />
            </div>
            <div className="skeleton mt-2 h-3 w-full" />
            <div className="skeleton mt-1 h-3 w-3/4" />
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex items-center justify-between text-xs">
              <div className="skeleton h-3 w-20" />
              <div className="skeleton h-3 w-16" />
            </div>
            <div className="skeleton h-9 w-full rounded-md" />
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
