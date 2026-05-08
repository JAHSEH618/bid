import { useEffect, useState } from 'react'
import {
  Bot,
  CheckCircle2,
  Eye,
  EyeOff,
  Gauge,
  KeyRound,
  RefreshCw,
  Shield,
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
import type { ModelConfigDTO } from '@/lib/types'

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
  const [outlineModel, setOutlineModel] = useState('')
  const [chapterModel, setChapterModel] = useState('')
  const [visualsModel, setVisualsModel] = useState('')
  const [outlineCustom, setOutlineCustom] = useState(false)
  const [chapterCustom, setChapterCustom] = useState(false)
  const [visualsCustom, setVisualsCustom] = useState(false)

  // 同步后端数据到本地 state
  const syncModelState = (data: ModelConfigDTO) => {
    const o = data.llm1_outline_model ?? ''
    const c = data.llm2_chapter_model ?? ''
    const v = data.llm3_visuals_model ?? ''
    setOutlineModel(o || data.default_outline_model)
    setChapterModel(c || data.default_chapter_model)
    setVisualsModel(v || data.default_visuals_model)
    setOutlineCustom(!!o && !data.known_models.includes(o))
    setChapterCustom(!!c && !data.known_models.includes(c))
    setVisualsCustom(!!v && !data.known_models.includes(v))
  }
  useEffect(() => {
    if (modelConfig.data) syncModelState(modelConfig.data)
  }, [modelConfig.data])

  const knownModels = modelConfig.data?.known_models ?? []
  const defaults = modelConfig.data
    ? {
        outline: modelConfig.data.default_outline_model,
        chapter: modelConfig.data.default_chapter_model,
        visuals: modelConfig.data.default_visuals_model,
      }
    : { outline: '', chapter: '', visuals: '' }

  const handleSaveModels = async () => {
    const payload = {
      llm1_outline_model: outlineModel
        ? outlineModel === defaults.outline
          ? null
          : outlineModel
        : null,
      llm2_chapter_model: chapterModel
        ? chapterModel === defaults.chapter
          ? null
          : chapterModel
        : null,
      llm3_visuals_model: visualsModel
        ? visualsModel === defaults.visuals
          ? null
          : visualsModel
        : null,
    }
    try {
      await setModelConfig.mutateAsync(payload)
      toast({ title: '模型配置已保存', variant: 'success' })
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
          配置 DashScope API Key、选择调用模型与查看 token 消费
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
            调用模型
          </CardTitle>
          <CardDescription>
            为三类任务选择模型,留空则使用系统默认。启动项目时快照,运行中不受后续修改影响
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {modelConfig.isLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : (
            <>
              {/* 提纲生成 LLM-1 */}
              <ModelSelector
                label="提纲生成 (LLM-1)"
                description="阅读理解招标文档,生成章节级应标提纲"
                value={outlineModel}
                onChange={setOutlineModel}
                isCustom={outlineCustom}
                onCustomToggle={setOutlineCustom}
                knownModels={knownModels}
                defaultModel={defaults.outline}
              />

              <Separator />

              {/* 正文撰写 LLM-2 */}
              <ModelSelector
                label="正文撰写 (LLM-2)"
                description="流式逐章生成专业应标正文,每章 3000-10000 字"
                value={chapterModel}
                onChange={setChapterModel}
                isCustom={chapterCustom}
                onCustomToggle={setChapterCustom}
                knownModels={knownModels}
                defaultModel={defaults.chapter}
              />

              <Separator />

              {/* 配图 LLM-3 */}
              <ModelSelector
                label="配图 (LLM-3)"
                description="为章节补充 Mermaid 流程图/架构图(非关键路径,失败不中断)"
                value={visualsModel}
                onChange={setVisualsModel}
                isCustom={visualsCustom}
                onCustomToggle={setVisualsCustom}
                knownModels={knownModels}
                defaultModel={defaults.visuals}
              />
            </>
          )}
        </CardContent>
        <CardFooter>
          <Button
            onClick={handleSaveModels}
            disabled={setModelConfig.isPending || modelConfig.isLoading}
            size="sm"
          >
            {setModelConfig.isPending ? '保存中…' : '保存模型配置'}
          </Button>
          <span className="ml-3 text-xs text-muted-foreground">
            修改后新建的项目生效,已在跑的项目不受影响
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

// ⭐ 模型选择器(§0002)
function ModelSelector({
  label,
  description,
  value,
  onChange,
  isCustom,
  onCustomToggle,
  knownModels,
  defaultModel,
}: {
  label: string
  description: string
  value: string
  onChange: (v: string) => void
  isCustom: boolean
  onCustomToggle: (v: boolean) => void
  knownModels: string[]
  defaultModel: string
}) {
  const isDefault = !value || value === defaultModel
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-medium">{label}</Label>
        {isDefault ? (
          <Badge variant="outline" className="text-[10px]">
            系统默认
          </Badge>
        ) : (
          <Badge variant="success" className="text-[10px]">
            自定义
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{description}</p>
      {!isCustom ? (
        <select
          className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={value || defaultModel}
          onChange={(e) => {
            const v = e.target.value
            if (v === '__custom__') {
              onCustomToggle(true)
              onChange('')
            } else {
              onChange(v)
            }
          }}
        >
          {knownModels.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
          <option value="__custom__">自定义输入…</option>
        </select>
      ) : (
        <div className="flex gap-2">
          <Input
            placeholder="输入 LiteLLM 模型名,如 dashscope/qwen-max"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="flex-1 font-mono text-xs"
          />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              onCustomToggle(false)
              onChange(defaultModel)
            }}
          >
            返回列表
          </Button>
        </div>
      )}
      <p className="text-[11px] text-muted-foreground">
        当前生效:{' '}
        <code className="rounded bg-muted px-1 font-mono text-[11px]">
          {isDefault ? defaultModel : value || '(空)'}
        </code>
        {isDefault && ' (系统默认)'}
      </p>
    </div>
  )
}
