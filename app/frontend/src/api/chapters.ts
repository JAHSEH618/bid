// 章节相关 API hooks。端点契约对齐 backend api/chapters.py。
//
// 端点:
//   - GET  /api/projects/{id}/chapters/{idx}          ChapterDetailResponse(R-15 commit 7dfc2fe)
//   - PATCH /api/projects/{id}/chapters/{idx}/model   {chapter_model} → {ok}
//   - POST /api/projects/{id}/chapters/{idx}/generate → {ok}
//   - POST /api/projects/{id}/chapters/{idx}/review   {decision, feedback?} → {ok}
//   - POST /api/projects/{id}/chapters/{idx}/retry    failed → retrying      → {ok}
//
// 注意:
//   - revise 必须有非空 feedback,否则 400(后端校验)
//   - 后端没有 GET /chapters/{idx}/versions(M2/M3 未规划),历史 Tab
//     在真实 API 模式下展示「暂无历史版本」;mock 模式照常生成 fixture。
import {
  useMutation,
  useQuery,
  useQueryClient,
  type Query,
} from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { ChapterDetailDTO, ReviewDecision } from '@/lib/types'

// R-15:hydrate ChapterReviewPage 用。状态决定轮询节奏:
//   generating / retrying / reviewing → 2s polling(R-14 partial 每 1s flush)
//   awaiting_review / approved / skipped / failed / pending → 停轮询
export function useChapter(
  projectId: number | null,
  index: number | null,
  options: { refetchInterval?: number | false } = {},
) {
  return useQuery<ChapterDetailDTO, Error>({
    queryKey: ['projects', projectId, 'chapters', index],
    queryFn: () =>
      apiFetch<ChapterDetailDTO>(
        `/api/projects/${projectId}/chapters/${index}`,
      ),
    enabled: projectId != null && index != null,
    refetchInterval:
      options.refetchInterval !== undefined
        ? options.refetchInterval
        : (q: Query<ChapterDetailDTO, Error>) => {
            const status = q.state.data?.status
            if (
              status === 'generating' ||
              status === 'retrying' ||
              status === 'reviewing'
            ) {
              return 2_000
            }
            return false
          },
  })
}

export interface ReviewChapterPayload {
  decision: ReviewDecision
  feedback?: string
}

export interface SetChapterModelPayload {
  chapter_model: string | null
}

export function useSetChapterModel() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      projectId,
      index,
      body,
    }: {
      projectId: number
      index: number
      body: SetChapterModelPayload
    }) =>
      apiFetch<{ ok: boolean; chapter_model: string | null }>(
        `/api/projects/${projectId}/chapters/${index}/model`,
        {
          method: 'PATCH',
          body: JSON.stringify(body),
        },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'chapters', vars.index],
      })
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'outline'],
      })
    },
  })
}

export function useGenerateChapter() {
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
        `/api/projects/${projectId}/chapters/${index}/generate`,
        { method: 'POST' },
      ),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['projects', vars.projectId] })
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'chapters', vars.index],
      })
      qc.invalidateQueries({
        queryKey: ['projects', vars.projectId, 'outline'],
      })
    },
  })
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
