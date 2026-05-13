// DOCX 相关 API hooks。端点契约对齐 backend api/docx.py(M3-3)。
//
// 端点:
//   - POST /api/projects/{id}/proposal.docx     → {docx_job_id, arq_job_id, cached}
//        · 命中缓存 → cached=true + 复用最近一次 done 的 docx_job_id(D-CK)
//        · 已有进行中任务 → 409
//   - GET  /api/projects/{id}/docx-job/{docx_job_id} → DocxJobDTO 轮询(D-BW)
//        · 内部 finalizing 状态映射为 processing(D-BU 不暴露)
//        · invalidated:assemble 重写后旧产物作废,前端展示「请重新生成」
//   - GET  /api/projects/{id}/proposal.docx     FileResponse(浏览器 cookie + Content-Disposition)
import {
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
} from '@tanstack/react-query'
import { apiFetch, apiUrl } from '@/lib/apiFetch'
import type { DocxJobDTO, TriggerDocxResponse } from '@/lib/types'

export type { TriggerDocxResponse } from '@/lib/types'

export function useTriggerDocx() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (projectId: number) =>
      apiFetch<TriggerDocxResponse>(
        `/api/projects/${projectId}/proposal.docx`,
        { method: 'POST' },
      ),
    onSuccess: (_data, projectId) => {
      qc.invalidateQueries({ queryKey: ['projects', projectId, 'docx-job'] })
    },
  })
}

export type DocxRefetchInterval =
  | number
  | false
  | ((query: Query<DocxJobDTO, Error>) => number | false | undefined)

export function useDocxJob(
  projectId: number | null,
  jobId: number | null,
  options: { refetchInterval?: DocxRefetchInterval } = {},
) {
  return useQuery({
    queryKey: ['projects', projectId, 'docx-job', jobId],
    queryFn: () =>
      apiFetch<DocxJobDTO>(`/api/projects/${projectId}/docx-job/${jobId}`),
    enabled: projectId != null && jobId != null,
    refetchInterval: options.refetchInterval ?? 3_000,
  })
}

// docx 下载是 FileResponse,前端走 window.location 跳转(浏览器 cookie 带过去),
// 不用 fetch + Blob 是为了让 Content-Disposition 文件名生效。
export function downloadDocxUrl(projectId: number): string {
  return apiUrl(`/api/projects/${projectId}/proposal.docx`)
}

export function downloadMarkdownUrl(projectId: number): string {
  return apiUrl(`/api/projects/${projectId}/proposal.md`)
}

// ============== PR-M6-2:单章 DOCX 导出 ==============
//
// 端点:
//   - POST /api/chapters/{chapter_id}/export.docx → {docx_job_id, arq_job_id, cached, project_id}
//   - GET  /api/chapters/{chapter_id}/export.docx → FileResponse
//   - 进度查询复用 GET /api/projects/{project_id}/docx-job/{docx_job_id}
//     (job_id 全局唯一,trigger 返回 project_id 给前端轮询用)

export interface TriggerChapterDocxResponse {
  docx_job_id: number
  arq_job_id: string | null
  cached: boolean
  scope: 'chapter'
  project_id?: number
}

export function useTriggerChapterDocx() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (chapterId: number) =>
      apiFetch<TriggerChapterDocxResponse>(
        `/api/chapters/${chapterId}/export.docx`,
        { method: 'POST' },
      ),
    onSuccess: (data) => {
      if (data.project_id) {
        qc.invalidateQueries({
          queryKey: ['projects', data.project_id, 'docx-job'],
        })
      }
    },
  })
}

export function downloadChapterDocxUrl(chapterId: number): string {
  return apiUrl(`/api/chapters/${chapterId}/export.docx`)
}
