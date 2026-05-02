// 管理员 hooks。端点契约对齐 backend api/admin.py(M2-5)。
//
// 端点:
//   - GET    /api/admin/users              list[AdminUserResponse]
//   - POST   /api/admin/users              {username, password, role} → AdminUserResponse(201)
//   - PATCH  /api/admin/users/{id}         {role?, is_active?, reset_password?} → AdminUserResponse
//   - DELETE /api/admin/users/{id}         → {ok}
//   - GET    /api/admin/token-usage?period=month|all → AdminTokenUsageSummary
//
// 注意:
//   - 改密 / 禁用 / 改角色都走同一个 PATCH(不再是分离端点)
//   - 删除是 DELETE(不是 PUT /disable;DELETE 端点也存在)
//   - 全局 token usage 路径是 /token-usage(不是 /usage)
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { AdminTokenUsageDTO, UserDTO, UserRole } from '@/lib/types'
import type { UsagePeriod } from './me'

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

export interface UpdateUserPayload {
  role?: UserRole
  is_active?: boolean
  reset_password?: string
}

export function useUpdateAdminUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      userId,
      body,
    }: {
      userId: number
      body: UpdateUserPayload
    }) =>
      apiFetch<UserDTO>(`/api/admin/users/${userId}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    },
  })
}

export function useDeleteAdminUser() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (userId: number) =>
      apiFetch<{ ok: boolean }>(`/api/admin/users/${userId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['admin', 'users'] })
    },
  })
}

export function useAdminTokenUsage(period: UsagePeriod) {
  return useQuery({
    queryKey: ['admin', 'token-usage', period],
    queryFn: () =>
      apiFetch<AdminTokenUsageDTO>(
        `/api/admin/token-usage?period=${period}`,
      ),
  })
}
