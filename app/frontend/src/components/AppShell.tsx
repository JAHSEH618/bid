import type { ReactNode } from 'react'
import { Link, NavLink, useNavigate } from 'react-router-dom'
import { LogOut } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { DashScopeBanner } from '@/components/DashScopeBanner'
import { GlobalProgressBanner } from '@/components/GlobalProgressBanner'
import { useCurrentUser, useLogout } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

// PR-UI-2 retrofit:editorial 应用外壳 —
// - 顶部 header 走 1px hairline rule 替代 backdrop-blur 阴影
// - 主导航用 tabs-style 底线 indicator
// - logo 改 serif 大写小标 + 大写字间距 meta
export function AppShell({ children }: { children: ReactNode }) {
  const { data: user } = useCurrentUser()
  const logout = useLogout()
  const navigate = useNavigate()

  const handleLogout = async () => {
    await logout.mutateAsync()
    navigate('/login', { replace: true })
  }

  return (
    <div className="flex min-h-screen flex-col bg-paper">
      {/* Skip link:键盘用户跳过顶部 nav 直达主内容(Vercel 指南必备) */}
      <a
        href="#main-content"
        className="sr-only focus-visible:not-sr-only focus-visible:fixed focus-visible:left-4 focus-visible:top-4 focus-visible:z-50 focus-visible:bg-paper focus-visible:px-3 focus-visible:py-2 focus-visible:text-sm focus-visible:text-ink focus-visible:border focus-visible:border-ink"
      >
        跳到主内容
      </a>
      <header className="sticky top-0 z-40 border-b border-rule bg-paper">
        <div className="mx-auto flex h-16 max-w-7xl items-center gap-10 px-gutter">
          <Link
            to="/"
            className="group flex items-baseline gap-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2"
          >
            <span className="font-display text-h3 leading-none text-ink">
              Bid
            </span>
            <span className="text-meta text-mute hidden sm:inline">
              投标方案生成器
            </span>
          </Link>
          <nav aria-label="主导航" className="flex items-center gap-8 text-sm">
            <NavItem to="/">项目</NavItem>
            {user?.role === 'admin' && <NavItem to="/admin">管理</NavItem>}
            <NavItem to="/settings">设置</NavItem>
          </nav>
          <div className="ml-auto flex items-center gap-4 text-sm">
            {user && (
              <div className="flex items-center gap-3">
                <span className="hidden text-mute sm:inline">
                  {user.username}
                </span>
                {user.role === 'admin' && (
                  <span className="text-meta text-accent">admin</span>
                )}
              </div>
            )}
            <Button
              variant="subtle"
              size="sm"
              onClick={handleLogout}
              disabled={logout.isPending}
            >
              <LogOut className="mr-1 h-3.5 w-3.5" aria-hidden="true" />
              退出
            </Button>
          </div>
        </div>
      </header>
      <DashScopeBanner />
      <GlobalProgressBanner />
      <main id="main-content" className="flex-1 bg-paper">
        {children}
      </main>
    </div>
  )
}

function NavItem({ to, children }: { to: string; children: ReactNode }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      className={({ isActive }) =>
        cn(
          'relative inline-flex items-center py-5 transition-colors duration-150',
          isActive
            ? 'text-ink font-medium after:absolute after:left-0 after:right-0 after:bottom-0 after:h-[3px] after:bg-ink after:content-[""]'
            : 'text-mute hover:text-ink',
        )
      }
    >
      {children}
    </NavLink>
  )
}
