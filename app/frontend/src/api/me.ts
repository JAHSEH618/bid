// 个人设置 hooks。具体 schema 等 #16 / #18 落地后回填。
//
// 端点参考 REQUIREMENTS §9 个人设置:
//   - GET    /api/me/api-key         返回 {configured: bool, last_validated_at?: string}
//   - PUT    /api/me/api-key         body: {key}
//   - DELETE /api/me/api-key
//   - GET    /api/me/api-key/test    返回 {ok: bool, error?: string}
//   - GET    /api/me/usage?month=YYYY-MM
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'

export interface ApiKeyStatus {
  configured: boolean
  last_validated_at: string | null
}

export function useApiKeyStatus() {
  return useQuery({
    queryKey: ['me', 'api-key'],
    queryFn: () => apiFetch<ApiKeyStatus>('/api/me/api-key'),
  })
}

export function useSetApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (key: string) =>
      apiFetch('/api/me/api-key', {
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
    mutationFn: () => apiFetch('/api/me/api-key', { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['me', 'api-key'] })
    },
  })
}

export interface ApiKeyTestResult {
  ok: boolean
  error?: string
}

export function useTestApiKey() {
  return useMutation({
    mutationFn: () => apiFetch<ApiKeyTestResult>('/api/me/api-key/test'),
  })
}

export interface MyUsage {
  month: string
  total_input_tokens: number
  total_output_tokens: number
  total_cost: number
  by_model: Array<{
    model: string
    input_tokens: number
    output_tokens: number
    cost: number
  }>
}

export function useMyUsage(month: string | null) {
  return useQuery({
    queryKey: ['me', 'usage', month],
    queryFn: () =>
      apiFetch<MyUsage>(`/api/me/usage?month=${encodeURIComponent(month!)}`),
    enabled: month != null,
  })
}
