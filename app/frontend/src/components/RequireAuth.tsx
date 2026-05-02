import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useCurrentUser } from '@/hooks/useAuth'

// IMPLEMENTATION_SPEC §16.1 + §14 D-F:
//   - 401:apiFetch 已自动跳 /login,这里也兜底
//   - 428(must_change_password):普通页面跳 /change-password;
//     allowMustChange=true 的路由(/change-password 自身)不再跳转
//   - requireAdmin=true:role !== 'admin' 跳 / (项目列表)
export interface RequireAuthProps {
  children: ReactNode
  allowMustChange?: boolean
  requireAdmin?: boolean
}

export function RequireAuth({
  children,
  allowMustChange = false,
  requireAdmin = false,
}: RequireAuthProps) {
  const { data: user, isLoading, isError } = useCurrentUser()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }

  if (isError || !user) {
    // apiFetch 已 redirect /login;这里再兜底渲染 Navigate 防止白屏。
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }

  if (user.must_change_password && !allowMustChange) {
    return <Navigate to="/change-password" replace />
  }

  if (requireAdmin && user.role !== 'admin') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}
