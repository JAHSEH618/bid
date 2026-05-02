// DOCX 相关 API hooks。具体 schema 等 #22 (api/docx.py) 落地后回填。
//
// 端点参考 IMPLEMENTATION_SPEC §15.3:
//   - POST /api/projects/{id}/proposal.docx           触发(返回 {docx_job_id, arq_job_id, cached})
//   - GET  /api/projects/{id}/proposal.docx           下载(FileResponse,前端走 window.open)
//   - GET  /api/projects/{id}/docx-job/{job_id}       轮询状态(D-BW)
import {
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
} from '@tanstack/react-query'
import { apiFetch, apiUrl } from '@/lib/apiFetch'
import type { DocxJobDTO } from '@/lib/types'

export interface TriggerDocxResponse {
  docx_job_id: number
  arq_job_id: string | null
  cached: boolean
}

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
