// 项目状态 → 目标路由的单一信源。
//
// 工作流跨多个页面交接,每个 status 都对应一个用户该看到的页面。把映射
// 抽出来,避免 ProjectListPage / GlobalProgressBanner / DocumentUploadPage /
// OutlineConfirmPage 等地方各写一份还互相不一致(已经踩过一次,导致
// awaiting_material_understanding 项目被错路由到 /outline 卡死)。
import type { ProjectDTO, ProjectStatus } from './types'

export function projectHref(p: Pick<ProjectDTO, 'id' | 'status'>): string {
  return statusHref(p.id, p.status)
}

export function statusHref(projectId: number, status: ProjectStatus): string {
  switch (status) {
    case 'init':
      return `/projects/${projectId}/upload`
    case 'awaiting_material_understanding':
      return `/projects/${projectId}/understanding`
    case 'queued':
    case 'extracting':
    case 'outlining':
    case 'outline_ready':
      return `/projects/${projectId}/outline`
    case 'done':
      return `/projects/${projectId}/proposal`
    case 'running':
    case 'awaiting_review':
    case 'failed':
      return `/projects/${projectId}/review`
    case 'aborted':
    case 'aborted_v1':
    case 'aborted_schema_v1':
      // 终态:停在列表页;调用方可以选不导航。这里给个无害的默认。
      return '/'
  }
}
