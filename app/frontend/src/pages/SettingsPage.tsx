import { useState } from 'react'
import { CheckCircle2, KeyRound, RefreshCw, XCircle } from 'lucide-react'
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
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { confirmDialog } from '@/components/ConfirmDialog'
import {
  useApiKeyInfo,
  useDeleteApiKey,
  useMyTokenUsage,
  useSetApiKey,
  useTestApiKey,
  type UsagePeriod,
} from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'

export function SettingsPage() {
  const { toast } = useToast()
  const apiKey = useApiKeyInfo()
  const setKey = useSetApiKey()
  const deleteKey = useDeleteApiKey()
  const testKey = useTestApiKey()

  const [keyInput, setKeyInput] = useState('')
  const [period, setPeriod] = useState<UsagePeriod>('month')
  const usage = useMyTokenUsage(period)

  const configured = apiKey.data != null
  const info = apiKey.data

  const handleSave = async () => {
    if (!keyInput.trim()) {
      toast({ title: '请输入 API Key', variant: 'warning' })
      return
    }
    if (keyInput.trim().length < 8) {
      toast({ title: 'API Key 至少 8 位', variant: 'warning' })
      return
    }
    try {
      await setKey.mutateAsync(keyInput.trim())
      setKeyInput('')
      toast({
        title: 'API Key 已保存并通过 DashScope 校验',
        variant: 'success',
      })
    } catch (err) {
      // 后端 PUT /api/me/api-key 先调 DashScope 验证才存,失败 → 400 detail 含错误原文
      const msg = readApiError(err, '保存失败')
      toast({ title: '保存失败', description: msg, variant: 'destructive' })
    }
  }

  const handleTest = async () => {
    try {
      const res = await testKey.mutateAsync()
      if (res.ok) {
        toast({ title: 'DashScope 连通正常', variant: 'success' })
      } else {
        toast({
          title: '连通失败',
          description: res.error ?? '未知错误',
          variant: 'destructive',
        })
      }
    } catch (err) {
      const msg = readApiError(err, '测试失败')
      toast({ title: '测试失败', description: msg, variant: 'destructive' })
    }
  }

  const handleDelete = async () => {
    // shadcn 风格统一(REVIEW-3 🟡 #6)。比 window.confirm 更显眼,destructive 样式。
    const ok = await confirmDialog({
      title: '确认删除已保存的 API Key?',
      description:
        'Key 将从数据库永久删除。已启动的项目仍能继续跑(快照机制 FR-7.6)。',
      confirmText: '删除',
      destructive: true,
    })
    if (!ok) return
    await deleteKey.mutateAsync()
    toast({ title: '已删除', variant: 'default' })
  }

  return (
    <div className="container max-w-3xl space-y-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">个人设置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          配置 DashScope API Key 与查看 token 消费。
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4" />
            DashScope API Key
          </CardTitle>
          <CardDescription>
            Key 加密保存,后端只返回掩码后的尾号。新建项目前必须配置。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            状态:
            {apiKey.isLoading ? (
              <span className="text-muted-foreground">加载中…</span>
            ) : configured ? (
              <Badge variant="success">
                <CheckCircle2 className="mr-1 h-3 w-3" />
                已配置 {info?.masked}
              </Badge>
            ) : (
              <Badge variant="warning">
                <XCircle className="mr-1 h-3 w-3" />
                未配置
              </Badge>
            )}
            {info?.last_validated_at && (
              <span className="text-xs text-muted-foreground">
                上次校验:
                {new Date(info.last_validated_at).toLocaleString('zh-CN')}
              </span>
            )}
          </div>

          <Separator />

          <div className="space-y-2">
            <Label htmlFor="api-key">填写新的 API Key</Label>
            <div className="flex gap-2">
              <Input
                id="api-key"
                type="password"
                placeholder="sk-..."
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
              />
              <Button onClick={handleSave} disabled={setKey.isPending}>
                {setKey.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              保存时后端会先调 DashScope 验证连通,通过后才落库。
            </p>
          </div>
        </CardContent>
        <CardFooter className="flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            onClick={handleTest}
            disabled={!configured || testKey.isPending}
          >
            <RefreshCw className="mr-1 h-4 w-4" />
            {testKey.isPending ? '测试中…' : '测试连通'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleDelete}
            disabled={!configured || deleteKey.isPending}
          >
            删除已保存的 Key
          </Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <div>
            <CardTitle className="text-base">Token 消费</CardTitle>
            <CardDescription>
              {period === 'month' ? '当月' : '累计'}(按调用模型分组)
            </CardDescription>
          </div>
          <div className="flex gap-1">
            <Button
              variant={period === 'month' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setPeriod('month')}
            >
              当月
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
          {usage.isLoading && (
            <p className="text-sm text-muted-foreground">加载中…</p>
          )}
          {usage.data && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <Stat
                  label="输入 tokens"
                  value={usage.data.total_prompt}
                />
                <Stat
                  label="输出 tokens"
                  value={usage.data.total_completion}
                />
              </div>
              <Separator />
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1.5 text-left">模型</th>
                    <th className="py-1.5 text-right">输入</th>
                    <th className="py-1.5 text-right">输出</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.data.rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={3}
                        className="py-3 text-center text-muted-foreground"
                      >
                        暂无消费记录
                      </td>
                    </tr>
                  )}
                  {usage.data.rows.map((m) => (
                    <tr key={m.model} className="border-t">
                      <td className="py-2">{m.model}</td>
                      <td className="py-2 text-right tabular-nums">
                        {m.prompt_tokens.toLocaleString()}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {m.completion_tokens.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border bg-card p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
    </div>
  )
}

