import type { ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { LogOut, Settings, ShieldCheck, FileText } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DashScopeBanner } from '@/components/DashScopeBanner'
import { GlobalProgressBanner } from '@/components/GlobalProgressBanner'
import { useCurrentUser, useLogout } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

// 已登录页面外壳:顶部导航 + DashScopeBanner + 主内容区。
export function AppShell({ children }: { children: ReactNode }) {
  const { data: user } = useCurrentUser()
  const logout = useLogout()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-40 flex h-14 items-center gap-6 border-b bg-background/95 px-6 backdrop-blur">
        <Link to="/" className="text-base font-semibold tracking-tight">
          投标技术方案生成器
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <NavItem to="/" icon={<FileText className="h-4 w-4" />}>
            项目
          </NavItem>
          {user?.role === 'admin' && (
            <NavItem to="/admin" icon={<ShieldCheck className="h-4 w-4" />}>
              管理
            </NavItem>
          )}
          <NavItem to="/settings" icon={<Settings className="h-4 w-4" />}>
            设置
          </NavItem>
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {user && (
            <span className="text-muted-foreground">
              {user.username}
              {user.role === 'admin' && (
                <span className="ml-1 rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                  admin
                </span>
              )}
            </span>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            disabled={logout.isPending}
          >
            <LogOut className="mr-1 h-4 w-4" />
            退出
          </Button>
        </div>
      </header>
      <DashScopeBanner />
      <GlobalProgressBanner />
      <main className="flex-1 bg-muted/30">{children}</main>
    </div>
  )
}

function NavItem({
  to,
  icon,
  children,
}: {
  to: string
  icon: ReactNode
  children: ReactNode
}) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors',
          isActive
            ? 'bg-accent text-accent-foreground'
            : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
        )
      }
    >
      {icon}
      {children}
    </NavLink>
  )
}
