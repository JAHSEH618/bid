import type { ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { FileText, LogOut, Settings, ShieldCheck } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DashScopeBanner } from '@/components/DashScopeBanner'
import { GlobalProgressBanner } from '@/components/GlobalProgressBanner'
import { useCurrentUser, useLogout } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

// 已登录页面外壳:顶部导航 + DashScopeBanner + GlobalProgressBanner + 主内容区。
export function AppShell({ children }: { children: ReactNode }) {
  const { data: user } = useCurrentUser()
  const logout = useLogout()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen flex-col bg-background">
      {/* Skip link:键盘用户跳过顶部 nav 直达主内容(Vercel 指南必备) */}
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:left-4 focus-visible:top-4 focus-visible:z-50 focus-visible:rounded-md focus-visible:bg-background focus-visible:px-3 focus-visible:py-2 focus-visible:text-sm focus-visible:font-medium focus-visible:text-foreground focus-visible:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        跳到主内容
      </a>
      <header className="sticky top-0 z-40 flex h-14 items-center gap-6 border-b border-border/70 bg-background/85 px-6 backdrop-blur supports-[backdrop-filter]:bg-background/70">
        <Link
          to="/"
          className="group flex items-center gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-md"
        >
          <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-sm shadow-primary/20 transition-transform duration-150 ease-out group-hover:scale-105 motion-reduce:group-hover:scale-100">
            <FileText
              className="h-3.5 w-3.5"
              strokeWidth={2.5}
              aria-hidden="true"
            />
          </span>
          <span
            translate="no"
            className="text-[15px] font-semibold tracking-tight"
          >
            投标方案生成器
          </span>
        </Link>
        <nav aria-label="主导航" className="flex items-center gap-0.5 text-sm">
          <NavItem
            to="/"
            icon={<FileText className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            项目
          </NavItem>
          {user?.role === 'admin' && (
            <NavItem
              to="/admin"
              icon={
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
              }
            >
              管理
            </NavItem>
          )}
          <NavItem
            to="/settings"
            icon={<Settings className="h-3.5 w-3.5" aria-hidden="true" />}
          >
            设置
          </NavItem>
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm">
          {user && (
            <div className="flex items-center gap-2">
              <span className="hidden text-foreground/80 sm:inline">
                {user.username}
              </span>
              {user.role === 'admin' && (
                <span className="inline-flex items-center gap-0.5 rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary ring-1 ring-inset ring-primary/15">
                  <ShieldCheck className="h-2.5 w-2.5" aria-hidden="true" />
                  admin
                </span>
              )}
            </div>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            disabled={logout.isPending}
            className="text-muted-foreground hover:text-foreground"
          >
            <LogOut className="mr-1 h-4 w-4" aria-hidden="true" />
            退出
          </Button>
        </div>
      </header>
      <DashScopeBanner />
      <GlobalProgressBanner />
      <main id="main-content" className="flex-1 bg-muted/40">
        {children}
      </main>
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
          'flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors duration-150',
          isActive
            ? 'bg-accent font-medium text-accent-foreground'
            : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
        )
      }
    >
      {icon}
      {children}
    </NavLink>
  )
}
