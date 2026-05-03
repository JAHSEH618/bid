import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
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
import { useQueryClient } from '@tanstack/react-query'
import { useChangePassword, useCurrentUser } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'

// 密码策略:≥ 8 位,含字母 + 数字。后端会做最终校验。
const schema = z
  .object({
    old_password: z.string().min(1, '请输入旧密码'),
    new_password: z
      .string()
      .min(8, '密码至少 8 位')
      .regex(/[A-Za-z]/, '需包含字母')
      .regex(/[0-9]/, '需包含数字'),
    confirm: z.string(),
  })
  .refine((d) => d.new_password === d.confirm, {
    path: ['confirm'],
    message: '两次输入不一致',
  })
  .refine((d) => d.new_password !== d.old_password, {
    path: ['new_password'],
    message: '新密码不能与旧密码相同',
  })

export function ChangePasswordPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const change = useChangePassword()
  const { toast } = useToast()
  const { data: user } = useCurrentUser()
  const [errors, setErrors] = useState<Record<string, string>>({})

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const parsed = schema.safeParse({
      old_password: fd.get('old_password'),
      new_password: fd.get('new_password'),
      confirm: fd.get('confirm'),
    })
    if (!parsed.success) {
      const fe: Record<string, string> = {}
      for (const issue of parsed.error.issues) {
        fe[issue.path[0] as string] = issue.message
      }
      setErrors(fe)
      return
    }
    setErrors({})
    try {
      await change.mutateAsync({
        old_password: parsed.data.old_password,
        new_password: parsed.data.new_password,
      })
      // R-11:改密后必须跳 /login 让用户重新登录。
      // 不调 /api/auth/logout(后端 cookie 已经被新密码标记 / 不需要),
      // 只清前端 react-query 缓存,RequireAuth 看到 /me 401 会自然跳 /login。
      qc.clear()
      toast({
        title: '密码已修改,请用新密码登录',
        variant: 'success',
      })
      navigate('/login', { replace: true })
    } catch (err) {
      toast({
        title: '修改失败',
        description: readApiError(err, '修改失败'),
        variant: 'destructive',
      })
    }
  }

  const isFirstTime = user?.must_change_password ?? false

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/40 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>{isFirstTime ? '首次登录:请修改默认密码' : '修改密码'}</CardTitle>
          <CardDescription>
            {isFirstTime
              ? '默认密码 admin123 仅供首次登录,修改后才能使用其它功能。'
              : '密码至少 8 位,含字母与数字。'}
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="old_password">旧密码</Label>
              <Input
                id="old_password"
                name="old_password"
                type="password"
                autoComplete="current-password"
              />
              {errors.old_password && (
                <p className="text-xs text-destructive">{errors.old_password}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="new_password">新密码</Label>
              <Input
                id="new_password"
                name="new_password"
                type="password"
                autoComplete="new-password"
              />
              {errors.new_password && (
                <p className="text-xs text-destructive">{errors.new_password}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="confirm">确认新密码</Label>
              <Input
                id="confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
              />
              {errors.confirm && (
                <p className="text-xs text-destructive">{errors.confirm}</p>
              )}
            </div>
          </CardContent>
          <CardFooter>
            <Button type="submit" className="w-full" disabled={change.isPending}>
              {change.isPending ? '提交中…' : '修改密码'}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
