// 项目相关 API hooks。端点契约对齐 backend api/projects.py(M1-7 commit 2a189d1)+
// chapters.py(M1-9 commit 44e974c)。
//
// 端点:
//   - GET    /api/projects                       list[Project]
//   - POST   /api/projects                       Project(201)
//   - GET    /api/projects/{id}                  Project
//   - DELETE /api/projects/{id}                  {ok: true}
//   - POST   /api/projects/{id}/start            {run_id, queued}
//   - GET    /api/projects/{id}/documents        list[Document](M1+ commit 73d51ec)
//   - POST   /api/projects/{id}/documents        Document(multipart kind=tech_spec/scoring/template + file)
//   - GET    /api/projects/{id}/outline          OutlineResponse(含 chapters[] 完整状态)
//   - PUT    /api/projects/{id}/outline          {ok: true}
//   - GET    /api/projects/{id}/proposal         ProposalResponse
//   - GET    /api/projects/{id}/proposal.md      file response
//
// 注意:后端无独立 GET /chapters 列表;chapter 列表来自 /outline.chapters。
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type {
  DocumentDTO,
  DocumentKind,
  OutlineChapterIn,
  OutlineResponseDTO,
  ProjectDTO,
  ProposalResponseDTO,
  StartResponseDTO,
} from '@/lib/types'

const PROJECTS_KEY = ['projects'] as const

// 项目处于运行 / 等待用户决策态时需要前端持续轮询;命中即让 useProjects /
// useProject / useProjectOutline 共用同一组 polling 触发条件,避免「列表里
// 显示 awaiting_review 但详情页一直停在 extracting」之类的漂移。
const ACTIVE_PROJECT_STATUSES = new Set<string>([
  'queued',
  'extracting',
  'awaiting_material_understanding',
  'outlining',
  'outline_ready',
  'running',
  'awaiting_review',
])

export function useProjects() {
  return useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => apiFetch<ProjectDTO[]>('/api/projects'),
    // R-12 全局进度感知:有 in-flight 项目时 5s 一次轮询,让 GlobalProgressBanner
    // 跨页能看到 status 变迁。无 in-flight 时停止 polling,避免空转。
    refetchInterval: (query) => {
      const data = query.state.data
      const hasActive = (data ?? []).some((p) =>
        ACTIVE_PROJECT_STATUSES.has(p.status),
      )
      return hasActive ? 5_000 : false
    },
  })
}

export function useProject(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId],
    queryFn: () => apiFetch<ProjectDTO>(`/api/projects/${projectId}`),
    enabled: projectId != null,
    // 项目处运行 / 等待态时 5s 轮询。`OutlineConfirmPage` /
    // `MaterialUnderstandingPage` / `DocumentUploadPage` 都以 useProject 派生
    // 按钮可用性;没有 polling 时拿到一次 extracting 后 LLM-0 完成 / 提纲
    // 完成都不会刷新,UI 卡死。
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return false
      return ACTIVE_PROJECT_STATUSES.has(status) ? 5_000 : false
    },
  })
}

export function useProjectOutline(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'outline'],
    queryFn: () =>
      apiFetch<OutlineResponseDTO>(`/api/projects/${projectId}/outline`),
    enabled: projectId != null,
    // 提纲在 outlining → outline_ready → running 期间会从空 → 完整;章节
    // generating 时 partial final_text 也持续 flush。无 polling 时 P4/P5
    // 永远拿不到后续 chapter 状态。终态(done/failed/aborted)停。
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (!status) return 3_000 // 还没拿到 status,3s 重试
      return ACTIVE_PROJECT_STATUSES.has(status) ? 3_000 : false
    },
  })
}

// Phase 1C (2026-05-16):实体黑板 10 桶 JSON。404 = categorize_blackboard
// 还没跑或失败,前端展示「等 LLM 拆桶完成」占位。轮询逻辑:项目在
// extracting / outlining / queued 这几个还没看到桶的阶段每 3s 轮询,
// 有 payload 就停。
interface BlackboardEntitiesResponseDTO {
  project_id: number
  blackboard_entities: Record<string, BlackboardEntryDTO[]>
}
export interface BlackboardEntryDTO {
  tags: string[]
  content: string
  source_doc?: string
  section?: string
}
export function useBlackboardEntities(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'blackboard-entities'],
    queryFn: () =>
      apiFetch<BlackboardEntitiesResponseDTO>(
        `/api/projects/${projectId}/blackboard-entities`,
      ),
    enabled: projectId != null,
    retry: false, // 404 是预期态(还没生成),不做指数退避
    refetchInterval: (q) => {
      if (q.state.data) return false
      const err = q.state.error
      if (!err) return false
      const msg = err instanceof Error ? err.message : String(err)
      // 只在「暂未就绪」的 404 上继续轮询;其它错(401/403/500)停
      return msg.includes('404') || msg.includes('暂未就绪')
        ? 3_000
        : false
    },
  })
}

export interface CreateProjectPayload {
  name: string
  description?: string | null
  pages_per_chapter?: number
  max_retry_per_chapter?: number
}

export function useCreateProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateProjectPayload) =>
      apiFetch<ProjectDTO>('/api/projects', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    },
  })
}

export function useDeleteProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<{ ok: boolean }>(`/api/projects/${projectId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    },
  })
}

export interface StartProjectPayload {
  pages_per_chapter?: number
  max_retry_per_chapter?: number
  outline_model?: string | null
  chapter_model?: string | null
  visuals_model?: string | null
}

export function useStartProject() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      body,
    }: {
      projectId: number
      body: StartProjectPayload
    }) =>
      apiFetch<StartResponseDTO>(`/api/projects/${projectId}/start`, {
        method: 'POST',
        body: JSON.stringify({
          pages_per_chapter: body.pages_per_chapter ?? 3,
          max_retry_per_chapter: body.max_retry_per_chapter ?? 3,
          outline_model: body.outline_model ?? null,
          chapter_model: body.chapter_model ?? null,
          visuals_model: body.visuals_model ?? null,
        }),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
      // 让全局列表 / banner 立刻看到 init → extracting 的状态切换,而不是
      // 等下一个 5s 周期
      qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    },
  })
}

export function useProjectDocuments(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'documents'],
    queryFn: () =>
      apiFetch<DocumentDTO[]>(`/api/projects/${projectId}/documents`),
    enabled: projectId != null,
    // 抽取是后台 arq 任务,.doc 走 LibreOffice 可能 5s+。任一文档 extract_status=pending
    // 时 2s 轮询,抽完后停止;让 DocumentUploadPage 的开始按钮能自动从 disabled 翻到可用。
    refetchInterval: (query) => {
      const data = query.state.data
      const hasPending = (data ?? []).some(
        (d) => d.extract_status === 'pending',
      )
      return hasPending ? 2_000 : false
    },
  })
}

export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      kind,
      tags,
      file,
    }: {
      projectId: number
      // PR-M7-2:kind 可选,旧 UI 传值则保留;新 UI 用 tags
      kind?: DocumentKind | null
      tags?: string[]
      file: File
    }) => {
      const fd = new FormData()
      if (kind) fd.append('kind', kind)
      if (tags && tags.length > 0) fd.append('tags', tags.join(','))
      fd.append('file', file)
      return apiFetch<DocumentDTO>(`/api/projects/${projectId}/documents`, {
        method: 'POST',
        body: fd,
      })
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'documents'],
      })
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

// PR-M7-2 多文件:支持删除单个上传文档,后端约束同 upload(项目仍处可编辑态)。
export function useDeleteDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      documentId,
    }: {
      projectId: number
      documentId: number
    }) =>
      apiFetch<{ ok: boolean }>(
        `/api/projects/${projectId}/documents/${documentId}`,
        { method: 'DELETE' },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'documents'],
      })
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export interface ConfirmOutlinePayload {
  chapters: OutlineChapterIn[]
  // PR-M9-1:用户勾选的章节 id 列表。空 / null → 全选
  selected_chapter_ids?: string[] | null
  // textarea TOC + revise:默认 'confirm';'revise' 时只发 feedback
  decision?: 'confirm' | 'revise'
  feedback?: string | null
}

export function useConfirmOutline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      chapters,
      selected_chapter_ids,
      decision,
      feedback,
    }: {
      projectId: number
      chapters: OutlineChapterIn[]
      selected_chapter_ids?: string[] | null
      decision?: 'confirm' | 'revise'
      feedback?: string | null
    }) =>
      apiFetch<{ ok: boolean }>(`/api/projects/${projectId}/outline`, {
        method: 'PUT',
        body: JSON.stringify({
          decision: decision ?? 'confirm',
          feedback: feedback ?? null,
          chapters,
          selected_chapter_ids: selected_chapter_ids ?? null,
        }),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
      // outline_ready → outlining(revise)/ running(confirm)的过渡也要让
      // 列表 banner 立刻刷一次
      qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    },
  })
}

export function useProposalMarkdown(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'proposal'],
    queryFn: () =>
      apiFetch<ProposalResponseDTO>(`/api/projects/${projectId}/proposal`),
    enabled: projectId != null,
  })
}
