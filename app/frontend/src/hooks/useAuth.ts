import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { apiFetch } from '@/lib/apiFetch'
import type { UserDTO } from '@/lib/types'

// 查 /api/auth/me 拿当前登录用户。
// 后端 GET /api/auth/me 走"宽松版" deps(允许 must_change_password=true 通过,见 §14.5 / D-F),
// 前端拿到 must_change_password=true 自行决定是否跳 /change-password(在 RequireAuth 里做)。
export function useCurrentUser() {
  return useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => apiFetch<UserDTO>('/api/auth/me'),
    retry: false,
    staleTime: 60_000,
  })
}

// login mutation 的具体 body schema 等 #16 (api/auth.py) 落地后回填。
export interface LoginPayload {
  username: string
  password: string
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: LoginPayload) =>
      apiFetch<UserDTO>('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (user) => {
      qc.setQueryData(['auth', 'me'], user)
    },
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      apiFetch('/api/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      qc.clear()
    },
  })
}

export interface ChangePasswordPayload {
  old_password: string
  new_password: string
}

export function useChangePassword() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: ChangePasswordPayload) =>
      apiFetch('/api/me/change-password', {
        method: 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['auth', 'me'] })
    },
  })
}
