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

export function useProjects() {
  return useQuery({
    queryKey: PROJECTS_KEY,
    queryFn: () => apiFetch<ProjectDTO[]>('/api/projects'),
  })
}

export function useProject(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId],
    queryFn: () => apiFetch<ProjectDTO>(`/api/projects/${projectId}`),
    enabled: projectId != null,
  })
}

export function useProjectOutline(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'outline'],
    queryFn: () =>
      apiFetch<OutlineResponseDTO>(`/api/projects/${projectId}/outline`),
    enabled: projectId != null,
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
        }),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export function useProjectDocuments(projectId: number | null) {
  return useQuery({
    queryKey: ['projects', projectId, 'documents'],
    queryFn: () =>
      apiFetch<DocumentDTO[]>(`/api/projects/${projectId}/documents`),
    enabled: projectId != null,
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
      kind: DocumentKind
      file: File
    }) => {
      const fd = new FormData()
      fd.append('kind', kind)
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

export interface ConfirmOutlinePayload {
  chapters: OutlineChapterIn[]
}

export function useConfirmOutline() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      chapters,
    }: {
      projectId: number
      chapters: OutlineChapterIn[]
    }) =>
      apiFetch<{ ok: boolean }>(`/api/projects/${projectId}/outline`, {
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
      apiFetch<ProposalResponseDTO>(`/api/projects/${projectId}/proposal`),
    enabled: projectId != null,
  })
}
