// 管理员 hooks。具体 schema 等 #18 (api/admin.py) 落地后回填。
//
// 端点参考 REQUIREMENTS §9 管理员:
//   - GET  /api/admin/users
//   - POST /api/admin/users                      body: {username, password, role}
//   - PUT  /api/admin/users/{id}/password        body: {new_password}
//   - PUT  /api/admin/users/{id}/disable
//   - GET  /api/admin/usage?month=YYYY-MM
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { UserDTO, UserRole, TokenUsageRow } from '@/lib/types'

export function useAdminUsers() {
  return useQuery({
    queryKey: ['admin', 'users'],
    queryFn: () => apiFetch<UserDTO[]>('/api/admin/users'),
  })
}

export interface CreateUserPayload {
  username: string
  password: string
  role: UserRole
}

export function useCreateAdminUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateUserPayload) =>
      apiFetch<UserDTO>('/api/admin/users', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    },
  })
}

export function useResetUserPassword() {
  return useMutation({
    mutationFn: ({
      userId,
      newPassword,
    }: {
      userId: number
      newPassword: string
    }) =>
      apiFetch(`/api/admin/users/${userId}/password`, {
        method: 'PUT',
        body: JSON.stringify({ new_password: newPassword }),
      }),
  })
}

export function useDisableUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) =>
      apiFetch(`/api/admin/users/${userId}/disable`, { method: 'PUT' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    },
  })
}

export interface AdminUsage {
  month: string
  rows: TokenUsageRow[]
  total_cost: number
}

export function useAdminUsage(month: string | null) {
  return useQuery({
    queryKey: ['admin', 'usage', month],
    queryFn: () =>
      apiFetch<AdminUsage>(`/api/admin/usage?month=${encodeURIComponent(month!)}`),
    enabled: month != null,
  })
}
