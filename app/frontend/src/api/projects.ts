// 项目相关 API hooks。具体 body schema 等 #14 / #13 落地后回填。
//
// 端点参考 IMPLEMENTATION_SPEC §15.1 + §15.2:
//   - GET    /api/projects                列表
//   - POST   /api/projects                新建
//   - GET    /api/projects/{id}           详情(含 chapters / outline / documents)
//   - DELETE /api/projects/{id}           删除(creator / admin)
//   - POST   /api/projects/{id}/start     启动(快照 API Key)
//   - POST   /api/projects/{id}/documents 上传文档(multipart/form-data)
//   - PUT    /api/projects/{id}/outline   提纲确认
//   - GET    /api/projects/{id}/proposal  完整 markdown(D-D / §15.2)
//   - GET    /api/projects/{id}/proposal.md  下载 markdown
import {
  useQuery,
  useMutation,
  useQueryClient,
} from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type {
  ProjectDTO,
  ProjectDetailDTO,
  OutlineChapter,
} from '@/lib/types'

const PROJECTS_KEY = ['projects'] as const

export function useProjects() {
  return useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => apiFetch<ProjectDTO[]>('/api/projects'),
  })
}

export function useProjectDetail(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'detail'],
    queryFn: () =>
      apiFetch<ProjectDetailDTO>(`/api/projects/${projectId}`),
    enabled: projectId != null,
  })
}

export interface CreateProjectPayload {
  name: string
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
      apiFetch(`/api/projects/${projectId}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: PROJECTS_KEY })
    },
  })
}

export interface StartProjectPayload {
  pages_per_chapter?: number
  max_retry_per_chapter?: number
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
      apiFetch<{ run_id: number; queued: boolean }>(
        `/api/projects/${projectId}/start`,
        {
          method: 'POST',
          body: JSON.stringify(body),
        },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export function useUploadDocument() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      kind,
      file,
    }: {
      projectId: number
      kind: 'tech_spec' | 'scoring_rules' | 'reference_doc'
      file: File
    }) => {
      const fd = new FormData()
      fd.append('kind', kind)
      fd.append('file', file)
      return apiFetch(`/api/projects/${projectId}/documents`, {
        method: 'POST',
        body: fd,
      })
    },
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export interface ConfirmOutlinePayload {
  chapters: OutlineChapter[] | null
}

export function useConfirmOutline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      chapters,
    }: {
      projectId: number
      chapters: OutlineChapter[] | null
    }) =>
      apiFetch(`/api/projects/${projectId}/outline`, {
        method: 'PUT',
        body: JSON.stringify({ chapters }),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export function useProposalMarkdown(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'proposal'],
    queryFn: () =>
      apiFetch<{ markdown: string }>(`/api/projects/${projectId}/proposal`),
    enabled: projectId != null,
  })
}
