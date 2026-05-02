import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { z } from 'zod'
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
import { ApiError } from '@/lib/apiFetch'

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

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
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
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '登录失败')
          : '登录失败'
      toast({ title: '登录失败', description: msg, variant: 'destructive' })
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>投标技术方案生成器</CardTitle>
          <CardDescription>请登录后使用</CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">用户名</Label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                autoFocus
                placeholder="admin"
              />
              {errors.username && (
                <p className="text-xs text-destructive">{errors.username}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                placeholder="首次登录用 admin123 / admin123"
              />
              {errors.password && (
                <p className="text-xs text-destructive">{errors.password}</p>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex flex-col items-stretch gap-2">
            <Button type="submit" disabled={login.isPending}>
              {login.isPending ? '登录中…' : '登录'}
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              首次登录后系统会要求修改默认密码
            </p>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
