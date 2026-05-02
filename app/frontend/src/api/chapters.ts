// 章节相关 API hooks。端点契约对齐 backend api/chapters.py(M1-9 commit 44e974c)。
//
// 端点:
//   - POST /api/projects/{id}/chapters/{idx}/review   {decision, feedback?} → {ok}
//   - POST /api/projects/{id}/chapters/{idx}/retry    failed → retrying      → {ok}
//
// 注意:
//   - revise 必须有非空 feedback,否则 400(后端校验)
//   - 后端没有 GET /chapters/{idx}/versions(M2/M3 也未规划),前端历史 Tab
//     在真实 API 模式下展示「暂无历史版本」;mock 模式照常生成 fixture。
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { ReviewDecision } from '@/lib/types'

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
      apiFetch<{ ok: boolean }>(
        `/api/projects/${projectId}/chapters/${index}/review`,
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
      apiFetch<{ ok: boolean }>(
        `/api/projects/${projectId}/chapters/${index}/retry`,
        { method: 'POST' },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
    },
  })
}
