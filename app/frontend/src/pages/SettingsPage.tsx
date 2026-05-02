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
import {
  useApiKeyStatus,
  useDeleteApiKey,
  useMyUsage,
  useSetApiKey,
  useTestApiKey,
} from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'

export function SettingsPage() {
  const { toast } = useToast()
  const status = useApiKeyStatus()
  const setKey = useSetApiKey()
  const deleteKey = useDeleteApiKey()
  const testKey = useTestApiKey()

  const [keyInput, setKeyInput] = useState('')

  const month = new Date().toISOString().slice(0, 7)
  const usage = useMyUsage(month)

  const configured = status.data?.configured ?? false

  const handleSave = async () => {
    if (!keyInput.trim()) {
      toast({ title: '请输入 API Key', variant: 'warning' })
      return
    }
    try {
      await setKey.mutateAsync(keyInput.trim())
      setKeyInput('')
      toast({ title: 'API Key 已保存并校验通过', variant: 'success' })
    } catch (err) {
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '保存失败')
          : '保存失败'
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
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '测试失败')
          : '测试失败'
      toast({ title: '测试失败', description: msg, variant: 'destructive' })
    }
  }

  const handleDelete = async () => {
    if (!window.confirm('确认删除已保存的 API Key?')) return
    await deleteKey.mutateAsync()
    toast({ title: '已删除', variant: 'default' })
  }

  return (
    <div className="container max-w-3xl space-y-6 py-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">个人设置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          配置 DashScope API Key 与查看本月 token 消费。
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4" />
            DashScope API Key
          </CardTitle>
          <CardDescription>
            Key 加密保存,后端不返回明文。新建项目前必须配置。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-2 text-sm">
            状态:
            {configured ? (
              <Badge variant="success">
                <CheckCircle2 className="mr-1 h-3 w-3" />
                已配置
              </Badge>
            ) : (
              <Badge variant="warning">
                <XCircle className="mr-1 h-3 w-3" />
                未配置
              </Badge>
            )}
            {status.data?.last_validated_at && (
              <span className="text-xs text-muted-foreground">
                上次校验:
                {new Date(status.data.last_validated_at).toLocaleString('zh-CN')}
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
              保存时后端会做一次最小调用验证,通过才落库。
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
        <CardHeader>
          <CardTitle className="text-base">本月 token 消费</CardTitle>
          <CardDescription>{month}(按调用模型分组)</CardDescription>
        </CardHeader>
        <CardContent>
          {usage.isLoading && (
            <p className="text-sm text-muted-foreground">加载中…</p>
          )}
          {usage.data && (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-3 text-sm">
                <Stat label="输入 tokens" value={usage.data.total_input_tokens} />
                <Stat label="输出 tokens" value={usage.data.total_output_tokens} />
                <Stat
                  label="预估费用 (¥)"
                  value={usage.data.total_cost.toFixed(2)}
                />
              </div>
              <Separator />
              <table className="w-full text-sm">
                <thead className="text-xs text-muted-foreground">
                  <tr>
                    <th className="py-1.5 text-left">模型</th>
                    <th className="py-1.5 text-right">输入</th>
                    <th className="py-1.5 text-right">输出</th>
                    <th className="py-1.5 text-right">费用 (¥)</th>
                  </tr>
                </thead>
                <tbody>
                  {usage.data.by_model.map((m) => (
                    <tr key={m.model} className="border-t">
                      <td className="py-2">{m.model}</td>
                      <td className="py-2 text-right tabular-nums">
                        {m.input_tokens.toLocaleString()}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {m.output_tokens.toLocaleString()}
                      </td>
                      <td className="py-2 text-right tabular-nums">
                        {m.cost.toFixed(2)}
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
