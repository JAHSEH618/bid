import { Link, useNavigate } from 'react-router-dom'
import { Plus, Trash2, ExternalLink } from 'lucide-react'
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

export function ProjectListPage() {
  const projects = useProjects()
  const remove = useDeleteProject()
  const navigate = useNavigate()
  const { data: me } = useCurrentUser()
  const { toast } = useToast()

  const handleDelete = async (project: ProjectDTO) => {
    if (
      !window.confirm(
        `确认删除项目「${project.name}」?将连带磁盘文件、章节、token 消费记录一起清除。`,
      )
    ) {
      return
    }
    try {
      await remove.mutateAsync(project.id)
      toast({ title: '已删除', variant: 'success' })
    } catch {
      toast({ title: '删除失败', variant: 'destructive' })
    }
  }

  const goToProject = (p: ProjectDTO) => {
    if (p.status === 'init') navigate(`/projects/${p.id}/upload`)
    else if (p.status === 'outline_ready')
      navigate(`/projects/${p.id}/outline`)
    else if (p.status === 'done') navigate(`/projects/${p.id}/proposal`)
    else navigate(`/projects/${p.id}/review`)
  }

  return (
    <div className="container max-w-6xl space-y-6 py-8">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">项目列表</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            团队共享池:任何成员可审核任何项目;只有创建者与管理员能删除。
          </p>
        </div>
        <Button asChild>
          <Link to="/projects/new">
            <Plus className="mr-1 h-4 w-4" />
            新建项目
          </Link>
        </Button>
      </header>

      {projects.isLoading && (
        <p className="text-sm text-muted-foreground">加载中…</p>
      )}

      {projects.data && projects.data.length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            还没有项目。点击右上角「新建项目」开始。
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {projects.data?.map((p) => {
          const canDelete =
            me?.role === 'admin' || me?.id === p.created_by
          return (
            <Card
              key={p.id}
              className="cursor-pointer transition-shadow hover:shadow-md"
              onClick={() => goToProject(p)}
            >
              <CardHeader className="pb-3">
                <div className="flex items-start justify-between gap-3">
                  <CardTitle className="line-clamp-1 text-base">
                    {p.name}
                  </CardTitle>
                  <Badge variant={STATUS_VARIANT[p.status]}>
                    {STATUS_LABEL[p.status]}
                  </Badge>
                </div>
                {p.description && (
                  <p className="line-clamp-2 text-xs text-muted-foreground">
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
    </div>
  )
}
