// 个人设置 hooks。端点契约对齐 backend api/me.py(M2 commit 9a4aa6c)。
//
// 端点:
//   - GET    /api/me/api-key            ApiKeyInfoResponse,未配置 404
//   - PUT    /api/me/api-key            body: {key}                 → {ok}
//   - DELETE /api/me/api-key            → {ok} (幂等)
//   - GET    /api/me/api-key/test       → {ok, last_validated_at} 或 {ok: false, error}
//   - GET    /api/me/token-usage?period=month|all  → TokenUsageSummary
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import type { ApiKeyInfoDTO, MyTokenUsageDTO } from '@/lib/types'

// 404 视为未配置(返 null);其它错误正常抛。
async function fetchApiKeyInfo(): Promise<ApiKeyInfoDTO | null> {
  try {
    return await apiFetch<ApiKeyInfoDTO>('/api/me/api-key')
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export function useApiKeyInfo(
  options?: Omit<
    UseQueryOptions<ApiKeyInfoDTO | null>,
    'queryKey' | 'queryFn'
  >,
) {
  return useQuery<ApiKeyInfoDTO | null>({
    queryKey: ['me', 'api-key'],
    queryFn: fetchApiKeyInfo,
    ...options,
  })
}

export function useSetApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: string) =>
      apiFetch<{ ok: boolean }>('/api/me/api-key', {
        method: 'PUT',
        body: JSON.stringify({ key }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['me', 'api-key'] })
    },
  })
}

export function useDeleteApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiFetch<{ ok: boolean }>('/api/me/api-key', { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['me', 'api-key'] })
    },
  })
}

export interface ApiKeyTestResult {
  ok: boolean
  error?: string
  last_validated_at?: string | null
}

export function useTestApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiFetch<ApiKeyTestResult>('/api/me/api-key/test'),
    onSuccess: (data) => {
      if (data.ok) qc.invalidateQueries({ queryKey: ['me', 'api-key'] })
    },
  })
}

export type UsagePeriod = 'month' | 'all'

export function useMyTokenUsage(period: UsagePeriod) {
  return useQuery({
    queryKey: ['me', 'token-usage', period],
    queryFn: () =>
      apiFetch<MyTokenUsageDTO>(`/api/me/token-usage?period=${period}`),
  })
}
