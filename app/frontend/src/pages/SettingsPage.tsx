import { useEffect, useMemo, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  Eye,
  EyeOff,
  Gauge,
  KeyRound,
  Plus,
  RefreshCw,
  Shield,
  Trash2,
  XCircle,
} from 'lucide-react'
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
  useModelConfig,
  useMyTokenUsage,
  useSetApiKey,
  useSetModelConfig,
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
  const [showKey, setShowKey] = useState(false)
  const [period, setPeriod] = useState<UsagePeriod>('month')
  const usage = useMyTokenUsage(period)

  // 模型配置(§0002)
  const modelConfig = useModelConfig()
  const setModelConfig = useSetModelConfig()
  const [modelInput, setModelInput] = useState('')
  const [customModels, setCustomModels] = useState<string[]>([])

  useEffect(() => {
    if (modelConfig.data) setCustomModels(modelConfig.data.custom_models ?? [])
  }, [modelConfig.data])

  const builtInModels = useMemo(() => {
    const data = modelConfig.data
    if (!data) return []
    return uniqueModels([
      data.default_outline_model,
      data.default_chapter_model,
      data.default_visuals_model,
      ...data.known_models,
    ])
  }, [modelConfig.data])

  const allModels = useMemo(
    () => uniqueModels([...builtInModels, ...customModels]),
    [builtInModels, customModels],
  )

  const handleAddModel = () => {
    const model = modelInput.trim()
    if (!model) {
      toast({ title: '请输入模型名', variant: 'warning' })
      return
    }
    if (model.length > 128) {
      toast({ title: '模型名不能超过 128 字符', variant: 'warning' })
      return
    }
    if (allModels.includes(model)) {
      toast({ title: '模型已在列表中', variant: 'warning' })
      return
    }
    setCustomModels((prev) => [...prev, model])
    setModelInput('')
  }

  const handleSaveModels = async () => {
    try {
      await setModelConfig.mutateAsync({ custom_models: customModels })
      toast({ title: '模型库已保存', variant: 'success' })
    } catch (err) {
      const msg = readApiError(err, '保存失败')
      toast({ title: '保存失败', description: msg, variant: 'destructive' })
    }
  }

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
    const ok = await confirmDialog({
      title: '确认删除已保存的 API Key?',
      description:
        'Key 将从数据库永久删除。已启动的项目仍能继续跑(快照机制 FR-7.6)',
      confirmText: '删除',
      destructive: true,
    })
    if (!ok) return
    await deleteKey.mutateAsync()
    toast({ title: '已删除', variant: 'default' })
  }

  return (
    <div className="container max-w-3xl space-y-6 py-8 page-enter">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">个人设置</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          配置 DashScope API Key、维护模型库与查看 token 消费
        </p>
      </header>

      <Card className="border-border/70 shadow-sm">
        <CardHeader className="pb-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
              <KeyRound className="h-4 w-4" />
            </span>
            DashScope API Key
          </CardTitle>
          <CardDescription>
            Key 加密保存,后端只返回掩码后的尾号。新建项目前必须配置
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">状态:</span>
            {apiKey.isLoading ? (
              <span className="text-sm text-muted-foreground">加载中…</span>
            ) : configured ? (
              <Badge variant="success">
                <CheckCircle2 className="h-3 w-3" />
                已配置 {info?.masked}
              </Badge>
            ) : (
              <Badge variant="warning">
                <XCircle className="h-3 w-3" />
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

          <div className="space-y-1.5">
            <Label htmlFor="api-key">填写新的 API Key</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <Input
                  id="api-key"
                  type={showKey ? 'text' : 'password'}
                  placeholder="sk-..."
                  value={keyInput}
                  onChange={(e) => setKeyInput(e.target.value)}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowKey((s) => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                  tabIndex={-1}
                  aria-label={showKey ? '隐藏' : '显示'}
                >
                  {showKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              <Button onClick={handleSave} disabled={setKey.isPending}>
                {setKey.isPending ? '保存中…' : '保存'}
              </Button>
            </div>
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Shield className="h-3 w-3" />
              保存时后端会先调 DashScope 验证连通,通过后才落库
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
            className="text-muted-foreground hover:text-destructive"
          >
            删除已保存的 Key
          </Button>
        </CardFooter>
      </Card>

      {/* ⭐ 模型配置(§0002) */}
      <Card className="border-border/70 shadow-sm">
        <CardHeader className="pb-4">
          <CardTitle className="flex items-center gap-2 text-base">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Bot className="h-4 w-4" />
            </span>
            模型库
          </CardTitle>
          <CardDescription>
            这里只维护可选模型,启动项目与确认章节时再选择具体用途
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {modelConfig.isLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="model-name">添加模型</Label>
                <div className="flex gap-2">
                  <Input
                    id="model-name"
                    value={modelInput}
                    onChange={(e) => setModelInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddModel()
                      }
                    }}
                    placeholder="dashscope/qwen-max"
                    className="font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    onClick={handleAddModel}
                    disabled={setModelConfig.isPending}
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    添加
                  </Button>
                </div>
              </div>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-sm font-medium">已添加模型</Label>
                  <Badge variant="outline" className="text-[10px]">
                    {customModels.length}
                  </Badge>
                </div>
                {customModels.length === 0 ? (
                  <p className="rounded-md border border-dashed border-border bg-muted/30 px-3 py-4 text-sm text-muted-foreground">
                    暂无自定义模型
                  </p>
                ) : (
                  <div className="space-y-2">
                    {customModels.map((model) => (
                      <div
                        key={model}
                        className="flex items-center gap-2 rounded-md border border-border/70 bg-background px-3 py-2"
                      >
                        <code className="min-w-0 flex-1 truncate font-mono text-xs">
                          {model}
                        </code>
                        <Button
                          type="button"
                          variant="ghost"
                          size="iconSm"
                          className="text-muted-foreground hover:text-destructive"
                          onClick={() =>
                            setCustomModels((prev) =>
                              prev.filter((m) => m !== model),
                            )
                          }
                          aria-label={`删除模型 ${model}`}
                          title="删除模型"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <Separator />

              <div className="space-y-2">
                <Label className="text-sm font-medium">启动流程可选模型</Label>
                <div className="flex flex-wrap gap-2">
                  {allModels.map((model) => (
                    <Badge
                      key={model}
                      variant="muted"
                      className="max-w-full font-mono text-[10px]"
                    >
                      <span className="truncate">{model}</span>
                    </Badge>
                  ))}
                </div>
              </div>
            </>
          )}
        </CardContent>
        <CardFooter>
          <Button
            onClick={handleSaveModels}
            disabled={setModelConfig.isPending || modelConfig.isLoading}
            size="sm"
          >
            {setModelConfig.isPending ? '保存中…' : '保存模型库'}
          </Button>
          <span className="ml-3 text-xs text-muted-foreground">
            修改后启动项目和确认章节时可选择
          </span>
        </CardFooter>
      </Card>

      <Card className="border-border/70 shadow-sm">
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
          <div className="flex items-center gap-2">
            <span className="flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Gauge className="h-4 w-4" />
            </span>
            <div>
              <CardTitle className="text-base">Token 消费</CardTitle>
              <CardDescription className="mt-0.5">
                {period === 'month' ? '当月' : '累计'}(按调用模型分组)
              </CardDescription>
            </div>
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
              当月
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
          {usage.isLoading && (
            <p className="text-sm text-muted-foreground">加载中…</p>
          )}
          {usage.data && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <Stat
                  label="输入 tokens"
                  value={usage.data.total_prompt}
                  hint="prompt"
                />
                <Stat
                  label="输出 tokens"
                  value={usage.data.total_completion}
                  hint="completion"
                />
              </div>
              <Separator />
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-xs text-muted-foreground">
                    <tr className="border-b">
                      <th className="py-2 text-left font-medium">模型</th>
                      <th className="py-2 text-right font-medium">输入</th>
                      <th className="py-2 text-right font-medium">输出</th>
                    </tr>
                  </thead>
                  <tbody>
                    {usage.data.rows.length === 0 && (
                      <tr>
                        <td
                          colSpan={3}
                          className="py-6 text-center text-muted-foreground"
                        >
                          暂无消费记录
                        </td>
                      </tr>
                    )}
                    {usage.data.rows.map((m) => (
                      <tr
                        key={m.model}
                        className="border-b last:border-0 hover:bg-muted/30"
                      >
                        <td className="py-2.5 font-mono text-xs">{m.model}</td>
                        <td className="py-2.5 text-right tabular-nums">
                          {m.prompt_tokens.toLocaleString()}
                        </td>
                        <td className="py-2.5 text-right tabular-nums">
                          {m.completion_tokens.toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string
  value: number | string
  hint?: string
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums leading-tight">
        {typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      {hint && (
        <p className="mt-0.5 font-mono text-[10px] uppercase text-muted-foreground/70">
          {hint}
        </p>
      )}
    </div>
  )
}

function uniqueModels(models: string[]) {
  const out: string[] = []
  const seen = new Set<string>()
  for (const raw of models) {
    const model = raw.trim()
    if (!model || seen.has(model)) continue
    out.push(model)
    seen.add(model)
  }
  return out
}
