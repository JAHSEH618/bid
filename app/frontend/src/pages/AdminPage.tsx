import { useState } from 'react'
import { Plus, KeyRound, UserMinus, UserCheck, Trash2 } from 'lucide-react'
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
import {
  useAdminTokenUsage,
  useAdminUsers,
  useCreateAdminUser,
  useDeleteAdminUser,
  useUpdateAdminUser,
} from '@/api/admin'
import type { UsagePeriod } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'
import type { AdminTokenUsageRow } from '@/lib/types'

export function AdminPage() {
  const users = useAdminUsers()
  const create = useCreateAdminUser()
  const update = useUpdateAdminUser()
  const remove = useDeleteAdminUser()
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
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
        description: readError(err, '创建失败'),
        variant: 'destructive',
      })
    }
  }

  const handleResetPwd = async (userId: number, username: string) => {
    const newPwd = window.prompt(`为 ${username} 重置密码(≥ 8 位):`)?.trim()
    if (!newPwd) return
    if (newPwd.length < 8) {
      toast({ title: '至少 8 位', variant: 'warning' })
      return
    }
    try {
      await update.mutateAsync({
        userId,
        body: { reset_password: newPwd },
      })
      toast({
        title: '密码已重置,该用户下次登录需改密',
        variant: 'success',
      })
    } catch (err) {
      toast({
        title: '重置失败',
        description: readError(err, '重置失败'),
        variant: 'destructive',
      })
    }
  }

  const handleToggleActive = async (
    userId: number,
    username: string,
    currentlyActive: boolean,
  ) => {
    if (
      currentlyActive &&
      !window.confirm(`确认禁用 ${username}?该用户将无法登录。`)
    ) {
      return
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
        description: readError(err, '操作失败'),
        variant: 'destructive',
      })
    }
  }

  const handleDelete = async (userId: number, username: string) => {
    if (
      !window.confirm(
        `永久删除 ${username}?将连带 API Key 与 token 消费记录一起删除。\n注意:如该用户名下有项目会失败。`,
      )
    ) {
      return
    }
    try {
      await remove.mutateAsync(userId)
      toast({ title: '已删除', variant: 'success' })
    } catch (err) {
      toast({
        title: '删除失败',
        description: readError(err, '删除失败'),
        variant: 'destructive',
      })
    }
  }

  // 按 user_id 聚合 admin token usage,显示「用户级」摘要
  const usageByUser = aggregateUsageByUser(usage.data?.rows ?? [])

  return (
    <div className="container max-w-6xl space-y-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">管理后台</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          用户管理与全员 token 消费汇总
        </p>
      </header>

      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">用户</TabsTrigger>
          <TabsTrigger value="usage">Token 消费</TabsTrigger>
        </TabsList>

        <TabsContent value="users">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
              <div>
                <CardTitle className="text-base">用户列表</CardTitle>
                <CardDescription>
                  仅 admin 可访问。新建用户首次登录必须改密。
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
                      用户首次登录会被强制要求修改密码。
                    </DialogDescription>
                  </DialogHeader>
                  <form onSubmit={handleCreate} className="space-y-3">
                    <div className="space-y-1.5">
                      <Label htmlFor="username">用户名(≥ 3 字符)</Label>
                      <Input id="username" name="username" autoFocus />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="password">初始密码(≥ 8 位)</Label>
                      <Input
                        id="password"
                        name="password"
                        type="password"
                      />
                    </div>
                    <div className="space-y-1.5">
                      <Label htmlFor="role">角色</Label>
                      <select
                        id="role"
                        name="role"
                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
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
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="py-2 text-left">用户名</th>
                    <th className="py-2 text-left">角色</th>
                    <th className="py-2 text-left">状态</th>
                    <th className="py-2 text-left">最近登录</th>
                    <th className="py-2 text-left">创建时间</th>
                    <th className="py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {users.data?.map((u) => (
                    <tr key={u.id} className="border-b last:border-0">
                      <td className="py-3 font-medium">{u.username}</td>
                      <td className="py-3">
                        <Badge
                          variant={u.role === 'admin' ? 'default' : 'outline'}
                        >
                          {u.role === 'admin' ? 'admin' : 'user'}
                        </Badge>
                      </td>
                      <td className="py-3">
                        <Badge
                          variant={u.is_active ? 'success' : 'destructive'}
                        >
                          {u.is_active ? '启用' : '已禁用'}
                        </Badge>
                        {u.must_change_password && (
                          <Badge variant="warning" className="ml-1">
                            待改密
                          </Badge>
                        )}
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
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleResetPwd(u.id, u.username)}
                          disabled={update.isPending}
                          title="重置密码"
                        >
                          <KeyRound className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            handleToggleActive(u.id, u.username, u.is_active)
                          }
                          disabled={update.isPending}
                          title={u.is_active ? '禁用' : '启用'}
                        >
                          {u.is_active ? (
                            <UserMinus className="h-3.5 w-3.5" />
                          ) : (
                            <UserCheck className="h-3.5 w-3.5" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDelete(u.id, u.username)}
                          disabled={remove.isPending}
                          title="删除"
                        >
                          <Trash2 className="h-3.5 w-3.5 text-destructive" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {users.isLoading && (
                <p className="py-3 text-sm text-muted-foreground">加载中…</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="usage">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0">
              <div>
                <CardTitle className="text-base">全员 token 消费</CardTitle>
                <CardDescription>
                  {period === 'month' ? '本月' : '累计'}(按用户聚合 + 模型明细)
                </CardDescription>
              </div>
              <div className="flex gap-1">
                <Button
                  variant={period === 'month' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setPeriod('month')}
                >
                  本月
                </Button>
                <Button
                  variant={period === 'all' ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setPeriod('all')}
                >
                  累计
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="py-2 text-left">用户</th>
                    <th className="py-2 text-left">模型</th>
                    <th className="py-2 text-right">输入 tokens</th>
                    <th className="py-2 text-right">输出 tokens</th>
                  </tr>
                </thead>
                <tbody>
                  {usageByUser.length === 0 && !usage.isLoading && (
                    <tr>
                      <td
                        colSpan={4}
                        className="py-3 text-center text-muted-foreground"
                      >
                        暂无消费记录
                      </td>
                    </tr>
                  )}
                  {usageByUser.flatMap((g) =>
                    g.rows.map((r, i) => (
                      <tr
                        key={`${g.user_id}-${r.model}`}
                        className="border-b last:border-0"
                      >
                        <td className="py-2 font-medium">
                          {i === 0 ? g.username : ''}
                        </td>
                        <td className="py-2 text-xs text-muted-foreground">
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
                  {usage.data && (
                    <tr className="font-medium">
                      <td colSpan={2} className="py-2">
                        合计
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {usage.data.total_prompt.toLocaleString()}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {usage.data.total_completion.toLocaleString()}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {usage.isLoading && (
                <p className="py-3 text-sm text-muted-foreground">加载中…</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
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

function readError(err: unknown, fallback: string): string {
  if (err instanceof ApiError && typeof err.body === 'object' && err.body) {
    return (err.body as { detail?: string }).detail ?? fallback
  }
  return fallback
}
