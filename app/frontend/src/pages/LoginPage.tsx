import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { z } from 'zod'
import { FileText, ShieldCheck, Sparkles } from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
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
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
      {/* 背景渐变 + 浮动光斑,提升登录页质感 */}
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_hsl(222_70%_94%)_0%,_hsl(220_25%_98.5%)_55%)]"
      />
      <div
        aria-hidden
        className="absolute -top-32 left-1/2 -z-10 h-[460px] w-[460px] -translate-x-1/2 rounded-full bg-primary/10 blur-3xl"
      />

      <div className="page-enter w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <FileText className="h-6 w-6" strokeWidth={2.2} />
          </div>
          <h1 className="text-[22px] font-semibold tracking-tight text-foreground">
            投标技术方案生成器
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            AI 驱动的投标方案自动撰写与人工审核工作流
          </p>
        </div>

        <Card
          className={cn(
            'border-border/60 shadow-md',
            shake && 'animate-shake',
          )}
        >
          <CardHeader className="space-y-1.5">
            <CardTitle className="text-[17px]">登录</CardTitle>
            <CardDescription>使用账号密码进入工作台</CardDescription>
          </CardHeader>
          <form onSubmit={handleSubmit} noValidate>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
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
              <div className="space-y-1.5">
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
            <CardFooter className="flex flex-col items-stretch gap-2.5">
              <Button
                type="submit"
                size="lg"
                disabled={login.isPending}
                className="w-full"
              >
                {login.isPending ? '登录中…' : '登录'}
              </Button>
              <p className="flex items-center justify-center gap-1 text-center text-xs text-muted-foreground">
                <ShieldCheck className="h-3 w-3" />
                首次登录后系统会要求修改默认密码
              </p>
            </CardFooter>
          </form>
        </Card>

        <p className="mt-5 flex items-center justify-center gap-1.5 text-[11px] text-muted-foreground/80">
          <Sparkles className="h-3 w-3" />
          内网部署 · 阿里云 DashScope 大模型驱动
        </p>
      </div>
    </div>
  )
}
