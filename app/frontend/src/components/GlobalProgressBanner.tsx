import { Link } from 'react-router-dom'
import { Activity, ChevronRight } from 'lucide-react'
import { useProjects } from '@/api/projects'
import { Badge } from '@/components/ui/badge'
import type { ProjectDTO, ProjectStatus } from '@/lib/types'

// R-12 全局进度条:在 AppShell 顶部展示当前用户/团队所有 in-flight 项目的状态摘要。
// 用户切到 Settings / Admin 等其他页面也能看到「某个项目还在跑」/「某个项目等审核」。

const ACTIVE_STATUSES: ReadonlySet<ProjectStatus> = new Set([
  'queued',
  'extracting',
  'outlining',
  'outline_ready',
  'running',
  'awaiting_review',
])

const STATUS_HINT: Record<ProjectStatus, string> = {
  init: '草稿',
  queued: '排队中',
  extracting: '解析文档',
  outlining: '生成提纲',
  outline_ready: '提纲待确认',
  running: '生成章节',
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

function pickActive(projects: ProjectDTO[] | undefined): ProjectDTO[] {
  if (!projects) return []
  return projects
    .filter((p) => ACTIVE_STATUSES.has(p.status))
    .slice(0, 3) // 最多展示 3 个,避免顶部 banner 过长
}

export function GlobalProgressBanner() {
  const { data } = useProjects()
  const active = pickActive(data)
  if (active.length === 0) return null

  return (
    <div className="flex items-center gap-3 border-b border-sky-100 bg-sky-50/60 px-6 py-2 text-xs">
      <Activity className="h-3.5 w-3.5 shrink-0 text-sky-700" />
      <span className="shrink-0 text-sky-900">进行中:</span>
      <ul className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-1">
        {active.map((p) => (
          <li key={p.id} className="flex items-center gap-1.5">
            <Link
              to={routeFor(p)}
              className="line-clamp-1 max-w-[200px] font-medium text-sky-900 underline-offset-2 hover:underline"
            >
              {p.name}
            </Link>
            <Badge
              variant={STATUS_VARIANT[p.status]}
              className="text-[10px]"
            >
              {STATUS_HINT[p.status]}
            </Badge>
          </li>
        ))}
      </ul>
      <Link
        to="/"
        className="ml-auto flex shrink-0 items-center gap-0.5 text-sky-800 hover:underline"
      >
        全部 <ChevronRight className="h-3 w-3" />
      </Link>
    </div>
  )
}

function routeFor(p: ProjectDTO): string {
  if (p.status === 'outline_ready' || p.status === 'extracting' || p.status === 'outlining' || p.status === 'queued') {
    return `/projects/${p.id}/outline`
  }
  if (p.status === 'done') return `/projects/${p.id}/proposal`
  return `/projects/${p.id}/review`
}
