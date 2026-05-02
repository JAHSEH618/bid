// 统一 API 客户端。401 → /login,428 → /change-password(IMPLEMENTATION_SPEC §16.2,D-F)。
//
// 注意:
//   - 401/428 在这里直接 location.replace 跳转,避免每个 hook 自己写一遍。
//   - 拦截器对 /api/auth/login 自身放行(否则登录失败会陷入循环)。
//   - cookie 走 credentials: 'include'(JWT httpOnly cookie,§14)。
//   - DEV 期默认走 mock(lib/mock.ts);设置 VITE_API_REAL=1 切回真请求。

import { isMockApiError, isMockEnabled, mockResolve } from './mock'

const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message ?? `API ${status}`)
    this.name = 'ApiError'
  }
}

// 这些端点的 401/428 不触发自动跳转:
//   - /api/auth/login   401 = 用户名或密码错(否则会循环跳 /login)
//   - /api/auth/refresh 401 = refresh token 过期(由调用方决定下一步)
//   - /api/auth/logout  幂等,挂壳 lax;不应该触发拦截
//   - /api/me/change-password
//       · 走 lax:must_change_password=true 的用户能调,所以不会拿到 428
//       · 401 = 旧密码错(用户在改密页输错,不能跳走)
const PASSTHROUGH_PATHS = [
  '/api/auth/login',
  '/api/auth/refresh',
  '/api/auth/logout',
  '/api/me/change-password',
]

function isPassthrough(path: string): boolean {
  return PASSTHROUGH_PATHS.some((p) => path.startsWith(p))
}

function redirect(target: string) {
  if (typeof window === 'undefined') return
  if (window.location.pathname === target) return
  window.location.replace(target)
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  if (isMockEnabled()) {
    try {
      return await mockResolve<T>(path, init)
    } catch (err) {
      if (isMockApiError(err)) {
        if (!isPassthrough(path)) {
          if (err.status === 401) redirect('/login')
          else if (err.status === 428) redirect('/change-password')
        }
        throw new ApiError(err.status, err.body)
      }
      throw err
    }
  }

  const isFormData = init.body instanceof FormData
  const headers: HeadersInit = {
    ...(init.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
    ...(init.headers ?? {}),
  }

  const res = await fetch(`${BASE}${path}`, {
    ...init,
    credentials: 'include',
    headers,
  })

  if (!isPassthrough(path)) {
    if (res.status === 401) {
      redirect('/login')
      throw new ApiError(401, null, '未登录')
    }
    if (res.status === 428) {
      redirect('/change-password')
      throw new ApiError(428, null, '需要修改默认密码')
    }
  }

  if (res.status === 204) {
    return null as T
  }

  const text = await res.text()
  let body: unknown = null
  if (text) {
    try {
      body = JSON.parse(text)
    } catch {
      body = text
    }
  }

  if (!res.ok) {
    throw new ApiError(res.status, body)
  }

  return body as T
}

export function apiUrl(path: string): string {
  return `${BASE}${path}`
}

// 统一从 ApiError 提取人类可读 detail。
// 后端 detail 可能是:
//   - 字符串(常见,FastAPI HTTPException(status, "detail"))
//   - {detail: "..."}(FastAPI 默认 wrap)
//   - {detail: {error: "must_change_password"}} (D-F 428,前端不会展示)
//   - 纯文本响应(text/plain)
export function readApiError(err: unknown, fallback: string): string {
  if (!(err instanceof ApiError)) return fallback
  const b = err.body
  if (typeof b === 'string' && b.trim()) return b
  if (b && typeof b === 'object') {
    const d = (b as { detail?: unknown }).detail
    if (typeof d === 'string' && d.trim()) return d
    if (d && typeof d === 'object') {
      const e = (d as { error?: unknown; message?: unknown }).error
      if (typeof e === 'string') return e
      const m = (d as { message?: unknown }).message
      if (typeof m === 'string') return m
    }
  }
  return fallback
}
