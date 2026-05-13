import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { z } from 'zod'
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useLogin } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import { cn } from '@/lib/utils'

const schema = z.object({
  username: z.string().min(1, '请输入用户名'),
  password: z.string().min(1, '请输入密码'),
})

// PR-UI-2:editorial 登录页 — 衬线大标题 + 大留白 + 1px hairline border。
// 不动业务逻辑;diff 限定为 className / 结构。
export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const login = useLogin()
  const { toast } = useToast()
  const [errors, setErrors] = useState<{ username?: string; password?: string }>(
    {},
  )
  const [shake, setShake] = useState(false)

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const fd = new FormData(form)
    const parsed = schema.safeParse({
      username: fd.get('username'),
      password: fd.get('password'),
    })
    if (!parsed.success) {
      const fe: typeof errors = {}
      for (const issue of parsed.error.issues) {
        fe[issue.path[0] as 'username' | 'password'] = issue.message
      }
      setErrors(fe)
      triggerShake()
      // Vercel 表单指南:校验失败时把焦点送到第一个错误字段。
      const firstKey = parsed.error.issues[0]?.path[0] as
        | 'username'
        | 'password'
        | undefined
      if (firstKey) {
        const target = form.elements.namedItem(firstKey)
        if (target instanceof HTMLElement) target.focus()
      }
      return
    }
    setErrors({})
    try {
      const user = await login.mutateAsync(parsed.data)
      const from = (location.state as { from?: string } | null)?.from ?? '/'
      if (user.must_change_password) {
        navigate('/change-password', { replace: true })
      } else {
        navigate(from, { replace: true })
      }
    } catch (err) {
      triggerShake()
      toast({
        title: '登录失败',
        description: readApiError(err, '登录失败'),
        variant: 'destructive',
      })
    }
  }

  const triggerShake = () => {
    setShake(true)
    window.setTimeout(() => setShake(false), 220)
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-paper px-gutter py-16">
      <div className="page-enter w-full max-w-md">
        <header className="mb-12 text-center">
          <p className="text-meta text-mute mb-3">Bid · Proposal Generator</p>
          <h1 className="font-display text-h1 leading-tight text-ink">
            投标技术方案生成器
          </h1>
          <p className="mt-4 text-sm text-mute">
            AI 驱动的投标方案自动撰写与人工审核工作流
          </p>
        </header>

        <Card className={cn('bg-paper', shake && 'animate-shake')}>
          <CardHeader>
            <p className="text-meta text-mute">Sign in</p>
            <CardTitle className="text-h3">登录</CardTitle>
          </CardHeader>
          <form onSubmit={handleSubmit} noValidate>
            <CardContent className="space-y-8">
              <div className="space-y-2">
                <Label htmlFor="username">用户名</Label>
                <Input
                  id="username"
                  name="username"
                  autoComplete="username"
                  autoFocus
                  spellCheck={false}
                  placeholder="admin"
                  aria-invalid={errors.username ? true : undefined}
                  aria-describedby={
                    errors.username ? 'username-error' : undefined
                  }
                />
                {errors.username && (
                  <p id="username-error" className="text-xs text-destructive">
                    {errors.username}
                  </p>
                )}
              </div>
              <div className="space-y-2">
                <Label htmlFor="password">密码</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  placeholder="首次登录使用 admin123"
                  aria-invalid={errors.password ? true : undefined}
                  aria-describedby={
                    errors.password ? 'password-error' : undefined
                  }
                />
                {errors.password && (
                  <p id="password-error" className="text-xs text-destructive">
                    {errors.password}
                  </p>
                )}
              </div>
            </CardContent>
            <CardFooter className="flex flex-col items-stretch gap-4 pt-2">
              <Button
                type="submit"
                size="lg"
                disabled={login.isPending}
                className="w-full"
              >
                {login.isPending ? '登录中…' : '登录'}
              </Button>
              <p className="text-meta text-mute text-center">
                首次登录后系统会要求修改默认密码
              </p>
            </CardFooter>
          </form>
        </Card>

        <p className="mt-8 text-meta text-mute text-center">
          内网部署 · 阿里云 DashScope 大模型驱动
        </p>
      </div>
    </div>
  )
}
