import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { Check, KeyRound, ShieldCheck, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
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
import { useChangePassword, useCurrentUser } from '@/hooks/useAuth'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import { cn } from '@/lib/utils'

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

interface RuleCheck {
  label: string
  ok: boolean
}

function evaluate(pwd: string): RuleCheck[] {
  return [
    { label: '至少 8 位', ok: pwd.length >= 8 },
    { label: '含字母', ok: /[A-Za-z]/.test(pwd) },
    { label: '含数字', ok: /[0-9]/.test(pwd) },
    { label: '推荐含符号', ok: /[^A-Za-z0-9]/.test(pwd) },
  ]
}

function scoreOf(rules: RuleCheck[]): {
  score: number
  label: string
  className: string
} {
  const passed = rules.filter((r) => r.ok).length
  if (passed <= 1) return { score: 25, label: '弱', className: 'bg-destructive' }
  if (passed === 2) return { score: 50, label: '一般', className: 'bg-amber-500' }
  if (passed === 3) return { score: 75, label: '良好', className: 'bg-sky-500' }
  return { score: 100, label: '强', className: 'bg-emerald-500' }
}

export function ChangePasswordPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const change = useChangePassword()
  const { toast } = useToast()
  const { data: user } = useCurrentUser()
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [shake, setShake] = useState(false)
  const [newPwd, setNewPwd] = useState('')

  const rules = useMemo(() => evaluate(newPwd), [newPwd])
  const strength = useMemo(() => scoreOf(rules), [rules])

  const triggerShake = () => {
    setShake(true)
    window.setTimeout(() => setShake(false), 220)
  }

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
      triggerShake()
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
      triggerShake()
      toast({
        title: '修改失败',
        description: readApiError(err, '修改失败'),
        variant: 'destructive',
      })
    }
  }

  const isFirstTime = user?.must_change_password ?? false

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_at_top,_hsl(222_70%_94%)_0%,_hsl(220_25%_98.5%)_55%)]"
      />

      <div className="page-enter w-full max-w-md">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <KeyRound className="h-6 w-6" strokeWidth={2.2} />
          </div>
          <h1 className="text-[22px] font-semibold tracking-tight">
            {isFirstTime ? '首次登录:请修改默认密码' : '修改密码'}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {isFirstTime
              ? '默认密码 admin123 仅供首次登录使用,修改后才能继续操作'
              : '修改后请使用新密码重新登录'}
          </p>
        </div>

        {isFirstTime && (
          <ol className="mb-5 flex items-center justify-center gap-2 text-xs">
            <li className="flex items-center gap-1.5 text-muted-foreground">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-100 text-emerald-700">
                <Check className="h-3 w-3" />
              </span>
              登录
            </li>
            <span className="h-px w-6 bg-border" />
            <li className="flex items-center gap-1.5 font-medium text-primary">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-primary-foreground">
                2
              </span>
              修改密码
            </li>
            <span className="h-px w-6 bg-border" />
            <li className="flex items-center gap-1.5 text-muted-foreground">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted">
                3
              </span>
              进入工作台
            </li>
          </ol>
        )}

        <Card
          className={cn('border-border/60 shadow-md', shake && 'animate-shake')}
        >
          <CardHeader className="space-y-1">
            <CardTitle className="text-[17px]">设置新密码</CardTitle>
            <CardDescription>
              至少 8 位,需同时包含字母与数字。
            </CardDescription>
          </CardHeader>
          <form onSubmit={handleSubmit} noValidate>
            <CardContent className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="old_password">旧密码</Label>
                <Input
                  id="old_password"
                  name="old_password"
                  type="password"
                  autoComplete="current-password"
                  aria-invalid={errors.old_password ? true : undefined}
                />
                {errors.old_password && (
                  <p className="text-xs text-destructive">
                    {errors.old_password}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="new_password">新密码</Label>
                <Input
                  id="new_password"
                  name="new_password"
                  type="password"
                  autoComplete="new-password"
                  value={newPwd}
                  onChange={(e) => setNewPwd(e.target.value)}
                  aria-invalid={errors.new_password ? true : undefined}
                />
                {newPwd.length > 0 && (
                  <div className="mt-1.5 space-y-2">
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                        <div
                          className={cn(
                            'h-full rounded-full transition-[width,background-color] duration-200 ease-out motion-reduce:transition-none',
                            strength.className,
                          )}
                          style={{ width: `${strength.score}%` }}
                        />
                      </div>
                      <span className="w-8 text-right text-xs text-muted-foreground tabular-nums">
                        {strength.label}
                      </span>
                    </div>
                    <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                      {rules.map((r) => (
                        <li
                          key={r.label}
                          className={cn(
                            'flex items-center gap-1',
                            r.ok
                              ? 'text-emerald-700'
                              : 'text-muted-foreground',
                          )}
                        >
                          {r.ok ? (
                            <Check className="h-3 w-3" strokeWidth={2.5} />
                          ) : (
                            <X className="h-3 w-3" strokeWidth={2.5} />
                          )}
                          {r.label}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {errors.new_password && (
                  <p className="text-xs text-destructive">
                    {errors.new_password}
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm">确认新密码</Label>
                <Input
                  id="confirm"
                  name="confirm"
                  type="password"
                  autoComplete="new-password"
                  aria-invalid={errors.confirm ? true : undefined}
                />
                {errors.confirm && (
                  <p className="text-xs text-destructive">{errors.confirm}</p>
                )}
              </div>
            </CardContent>
            <CardFooter className="flex flex-col items-stretch gap-2.5">
              <Button
                type="submit"
                size="lg"
                className="w-full"
                disabled={change.isPending}
              >
                {change.isPending ? '提交中…' : '修改密码'}
              </Button>
              <p className="flex items-center justify-center gap-1 text-center text-xs text-muted-foreground">
                <ShieldCheck className="h-3 w-3" />
                密码经 bcrypt 加密落库,服务器永远不会保存明文
              </p>
            </CardFooter>
          </form>
        </Card>
      </div>
    </div>
  )
}
