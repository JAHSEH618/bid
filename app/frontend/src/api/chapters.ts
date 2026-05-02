// 章节相关 API hooks。具体 schema 等 #11 / #13 落地后回填。
//
// 端点参考 IMPLEMENTATION_SPEC §15.2:
//   - POST /api/projects/{id}/chapters/{idx}/review   {decision, feedback?}
//   - POST /api/projects/{id}/chapters/{idx}/retry    failed → pending
//   - GET  /api/projects/{id}/chapters/{idx}/versions 历史版本(D-V)
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { ChapterVersionDTO, ReviewDecision } from '@/lib/types'

export interface ReviewChapterPayload {
  decision: ReviewDecision
  feedback?: string
}

export function useReviewChapter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      index,
      body,
    }: {
      projectId: number
      index: number
      body: ReviewChapterPayload
    }) =>
      apiFetch(`/api/projects/${projectId}/chapters/${index}/review`, {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export function useRetryChapter() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      index,
    }: {
      projectId: number
      index: number
    }) =>
      apiFetch(`/api/projects/${projectId}/chapters/${index}/retry`, {
        method: 'POST',
      }),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}

export function useChapterVersions(
  projectId: number | null,
  index: number | null,
) {
  return useQuery({
    queryKey: ['projects', projectId, 'chapters', index, 'versions'],
    queryFn: () =>
      apiFetch<ChapterVersionDTO[]>(
        `/api/projects/${projectId}/chapters/${index}/versions`,
      ),
    enabled: projectId != null && index != null,
  })
}
