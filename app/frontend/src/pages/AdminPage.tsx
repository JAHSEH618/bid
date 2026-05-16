import { useState } from 'react'
import {
  Eye,
  EyeOff,
  KeyRound,
  Plus,
  ShieldCheck,
  Trash2,
  UserCheck,
  UserMinus,
  Users,
} from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { confirmDialog } from '@/components/ConfirmDialog'
import {
  useAdminTokenUsage,
  useAdminUsers,
  useCreateAdminUser,
  useUpdateAdminUser,
} from '@/api/admin'
import type { UsagePeriod } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'
import type { AdminTokenUsageRow } from '@/lib/types'

interface ResetPwdTarget {
  userId: number
  username: string
}

export function AdminPage() {
  const users = useAdminUsers()
  const create = useCreateAdminUser()
  const update = useUpdateAdminUser()
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [resetTarget, setResetTarget] = useState<ResetPwdTarget | null>(null)
  const [period, setPeriod] = useState<UsagePeriod>('month')
  const usage = useAdminTokenUsage(period)

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const username = ((fd.get('username') as string) ?? '').trim()
    const password = ((fd.get('password') as string) ?? '').trim()
    const role = (fd.get('role') as 'admin' | 'user') ?? 'user'
    if (username.length < 3 || password.length < 8) {
      toast({
        title: '用户名 ≥ 3 字符,密码 ≥ 8 位',
        variant: 'warning',
      })
      return
    }
    try {
      await create.mutateAsync({ username, password, role })
      toast({ title: '用户已创建', variant: 'success' })
      setOpen(false)
      e.currentTarget.reset()
    } catch (err) {
      toast({
        title: '创建失败',
        description: readApiError(err, '创建失败'),
        variant: 'destructive',
      })
    }
  }

  const submitResetPwd = async (
    target: ResetPwdTarget,
    newPwd: string,
  ): Promise<boolean> => {
    if (newPwd.length < 8) {
      toast({ title: '至少 8 位', variant: 'warning' })
      return false
    }
    try {
      await update.mutateAsync({
        userId: target.userId,
        body: { reset_password: newPwd },
      })
      toast({
        title: '密码已重置,该用户下次登录需改密',
        variant: 'success',
      })
      return true
    } catch (err) {
      toast({
        title: '重置失败',
        description: readApiError(err, '重置失败'),
        variant: 'destructive',
      })
      return false
    }
  }

  const handleToggleActive = async (
    userId: number,
    username: string,
    currentlyActive: boolean,
  ) => {
    if (currentlyActive) {
      const ok = await confirmDialog({
        title: `禁用 ${username}?`,
        description: '该用户将无法登录,但已有项目数据保留',
        confirmText: '禁用',
        destructive: true,
      })
      if (!ok) return
    }
    try {
      await update.mutateAsync({
        userId,
        body: { is_active: !currentlyActive },
      })
      toast({
        title: currentlyActive ? '已禁用' : '已启用',
        variant: 'success',
      })
    } catch (err) {
      toast({
        title: '操作失败',
        description: readApiError(err, '操作失败'),
        variant: 'destructive',
      })
    }
  }

  const handleDelete = async (userId: number, username: string) => {
    // FR-6.5:不删账号(保留历史归属);改成"禁用"语义,走 PATCH is_active=false
    const ok = await confirmDialog({
      title: `禁用 ${username}?`,
      description: (
        <span>
          禁用后该用户将无法登录。历史项目、审核记录、token 消费保留。
          后续可再次启用。
        </span>
      ),
      confirmText: '禁用',
      destructive: true,
    })
    if (!ok) return
    try {
      await update.mutateAsync({ userId, body: { is_active: false } })
      toast({ title: '已禁用', variant: 'success' })
    } catch (err) {
      toast({
        title: '操作失败',
        description: readApiError(err, '操作失败'),
        variant: 'destructive',
      })
    }
  }

  const usageByUser = aggregateUsageByUser(usage.data?.rows ?? [])

  return (
    <div className="container max-w-6xl space-y-6 py-8 page-enter">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <ShieldCheck className="h-6 w-6 text-primary" />
          管理后台
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          用户管理与全员 token 消费汇总
        </p>
      </header>

      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">
            <Users className="mr-1.5 h-3.5 w-3.5" />
            用户
          </TabsTrigger>
          <TabsTrigger value="usage">Token 消费</TabsTrigger>
        </TabsList>

        <TabsContent value="users">
          <Card className="border-border/70 shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
              <div>
                <CardTitle className="text-base">用户列表</CardTitle>
                <CardDescription>
                  仅 admin 可访问。新建用户首次登录必须改密
                </CardDescription>
              </div>
              <Dialog open={open} onOpenChange={setOpen}>
                <DialogTrigger asChild>
                  <Button size="sm">
                    <Plus className="mr-1 h-4 w-4" />
                    新建用户
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>新建用户</DialogTitle>
                    <DialogDescription>
                      用户首次登录会被强制要求修改密码
                    </DialogDescription>
                  </DialogHeader>
                  <form onSubmit={handleCreate} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="new-user-username">
                        用户名(≥ 3 字符)
                      </Label>
                      <Input
                        id="new-user-username"
                        name="username"
                        autoComplete="off"
                        spellCheck={false}
                        autoFocus
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="new-user-password">
                        初始密码(≥ 8 位)
                      </Label>
                      <Input
                        id="new-user-password"
                        name="password"
                        type="password"
                        autoComplete="new-password"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="new-user-role">角色</Label>
                      <select
                        id="new-user-role"
                        name="role"
                        style={{ colorScheme: 'light' }}
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground shadow-sm transition-colors focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                        defaultValue="user"
                      >
                        <option value="user">普通用户</option>
                        <option value="admin">管理员</option>
                      </select>
                    </div>
                    <DialogFooter>
                      <Button type="submit" disabled={create.isPending}>
                        {create.isPending ? '创建中…' : '创建'}
                      </Button>
                    </DialogFooter>
                  </form>
                </DialogContent>
              </Dialog>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 text-left font-medium">用户名</th>
                      <th className="py-2 text-left font-medium">角色</th>
                      <th className="py-2 text-left font-medium">状态</th>
                      <th className="py-2 text-left font-medium">最近登录</th>
                      <th className="py-2 text-left font-medium">创建时间</th>
                      <th className="py-2 text-right font-medium">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.data?.map((u) => (
                      <tr
                        key={u.id}
                        className="border-b last:border-0 hover:bg-muted/30"
                      >
                        <td className="py-3 font-medium">{u.username}</td>
                        <td className="py-3">
                          <Badge
                            variant={u.role === 'admin' ? 'default' : 'outline'}
                          >
                            {u.role === 'admin' ? 'admin' : 'user'}
                          </Badge>
                        </td>
                        <td className="py-3">
                          <div className="flex flex-wrap items-center gap-1">
                            <Badge
                              variant={u.is_active ? 'success' : 'destructive'}
                            >
                              {u.is_active ? '启用' : '已禁用'}
                            </Badge>
                            {u.must_change_password && (
                              <Badge variant="warning">待改密</Badge>
                            )}
                          </div>
                        </td>
                        <td className="py-3 text-xs text-muted-foreground">
                          {u.last_login_at
                            ? new Date(u.last_login_at).toLocaleString('zh-CN')
                            : '从未登录'}
                        </td>
                        <td className="py-3 text-xs text-muted-foreground">
                          {new Date(u.created_at).toLocaleDateString('zh-CN')}
                        </td>
                        <td className="py-3 text-right">
                          <div className="flex items-center justify-end gap-0.5">
                            <Button
                              variant="ghost"
                              size="iconSm"
                              onClick={() =>
                                setResetTarget({
                                  userId: u.id,
                                  username: u.username,
                                })
                              }
                              disabled={update.isPending}
                              aria-label={`重置 ${u.username} 的密码`}
                              title="重置密码"
                            >
                              <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="iconSm"
                              onClick={() =>
                                handleToggleActive(
                                  u.id,
                                  u.username,
                                  u.is_active,
                                )
                              }
                              disabled={update.isPending}
                              aria-label={
                                u.is_active
                                  ? `禁用 ${u.username}`
                                  : `启用 ${u.username}`
                              }
                              title={u.is_active ? '禁用' : '启用'}
                            >
                              {u.is_active ? (
                                <UserMinus
                                  className="h-3.5 w-3.5"
                                  aria-hidden="true"
                                />
                              ) : (
                                <UserCheck
                                  className="h-3.5 w-3.5"
                                  aria-hidden="true"
                                />
                              )}
                            </Button>
                            <Button
                              variant="ghost"
                              size="iconSm"
                              onClick={() => handleDelete(u.id, u.username)}
                              disabled={update.isPending || !u.is_active}
                              aria-label={`禁用 ${u.username}`}
                              title="禁用"
                              className="text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {users.isLoading && (
                  <p className="py-3 text-sm text-muted-foreground">加载中…</p>
                )}
                {users.data && users.data.length === 0 && (
                  <p className="py-12 text-center text-sm text-muted-foreground">
                    暂无用户
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="usage">
          <Card className="border-border/70 shadow-sm">
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
              <div>
                <CardTitle className="text-base">全员 token 消费</CardTitle>
                <CardDescription>
                  {period === 'month' ? '本月' : '累计'}(按用户聚合 + 模型明细)
                </CardDescription>
              </div>
              <div className="inline-flex rounded-md border border-border bg-muted p-0.5">
                <button
                  type="button"
                  onClick={() => setPeriod('month')}
                  className={
                    period === 'month'
                      ? 'rounded-sm bg-background px-3 py-1 text-xs font-medium shadow-sm'
                      : 'rounded-sm px-3 py-1 text-xs text-muted-foreground hover:text-foreground'
                  }
                >
                  本月
                </button>
                <button
                  type="button"
                  onClick={() => setPeriod('all')}
                  className={
                    period === 'all'
                      ? 'rounded-sm bg-background px-3 py-1 text-xs font-medium shadow-sm'
                      : 'rounded-sm px-3 py-1 text-xs text-muted-foreground hover:text-foreground'
                  }
                >
                  累计
                </button>
              </div>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 text-left font-medium">用户</th>
                      <th className="py-2 text-left font-medium">模型</th>
                      <th className="py-2 text-right font-medium">
                        输入 tokens
                      </th>
                      <th className="py-2 text-right font-medium">
                        输出 tokens
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {usageByUser.length === 0 && !usage.isLoading && (
                      <tr>
                        <td
                          colSpan={4}
                          className="py-12 text-center text-muted-foreground"
                        >
                          暂无消费记录
                        </td>
                      </tr>
                    )}
                    {usageByUser.flatMap((g) =>
                      g.rows.map((r, i) => (
                        <tr
                          key={`${g.user_id}-${r.model}`}
                          className="border-b last:border-0 hover:bg-muted/30"
                        >
                          <td className="py-2 font-medium">
                            {i === 0 ? g.username : ''}
                          </td>
                          <td className="py-2 font-mono text-xs text-muted-foreground">
                            {r.model}
                          </td>
                          <td className="py-2 text-right tabular-nums">
                            {r.prompt_tokens.toLocaleString()}
                          </td>
                          <td className="py-2 text-right tabular-nums">
                            {r.completion_tokens.toLocaleString()}
                          </td>
                        </tr>
                      )),
                    )}
                    {usage.data && usage.data.rows.length > 0 && (
                      <tr className="bg-muted/40 font-semibold">
                        <td colSpan={2} className="py-2.5">
                          合计
                        </td>
                        <td className="py-2.5 text-right tabular-nums">
                          {usage.data.total_prompt.toLocaleString()}
                        </td>
                        <td className="py-2.5 text-right tabular-nums">
                          {usage.data.total_completion.toLocaleString()}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
                {usage.isLoading && (
                  <p className="py-3 text-sm text-muted-foreground">加载中…</p>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <ResetPasswordDialog
        target={resetTarget}
        onClose={() => setResetTarget(null)}
        onSubmit={submitResetPwd}
        pending={update.isPending}
      />
    </div>
  )
}

function ResetPasswordDialog({
  target,
  onClose,
  onSubmit,
  pending,
}: {
  target: ResetPwdTarget | null
  onClose: () => void
  onSubmit: (target: ResetPwdTarget, newPwd: string) => Promise<boolean>
  pending: boolean
}) {
  const [pwd, setPwd] = useState('')
  const [show, setShow] = useState(false)

  const handleClose = () => {
    setPwd('')
    setShow(false)
    onClose()
  }

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    if (!target) return
    const ok = await onSubmit(target, pwd)
    if (ok) handleClose()
  }

  return (
    <Dialog open={target != null} onOpenChange={(v) => !v && handleClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重置 {target?.username} 的密码</DialogTitle>
          <DialogDescription>
            ≥ 8 位。重置后该用户首次登录会被强制再次修改密码
            (must_change_password=true)
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="reset-pwd">新密码</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  id="reset-pwd"
                  type={show ? 'text' : 'password'}
                  autoFocus
                  autoComplete="new-password"
                  value={pwd}
                  onChange={(e) => setPwd(e.target.value)}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShow((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  tabIndex={-1}
                  aria-label={show ? '隐藏' : '显示'}
                >
                  {show ? <Eye className="h-4 w-4" /> : <EyeOff className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={pending}
            >
              取消
            </Button>
            <Button type="submit" disabled={pending || pwd.length < 8}>
              {pending ? '提交中…' : '确认重置'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface UserGroup {
  user_id: number
  username: string
  rows: AdminTokenUsageRow[]
}

function aggregateUsageByUser(rows: AdminTokenUsageRow[]): UserGroup[] {
  const map = new Map<number, UserGroup>()
  for (const r of rows) {
    const g = map.get(r.user_id)
    if (g) {
      g.rows.push(r)
    } else {
      map.set(r.user_id, {
        user_id: r.user_id,
        username: r.username,
        rows: [r],
      })
    }
  }
  return [...map.values()]
}

