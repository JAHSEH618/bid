import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { ArrowLeft, AlertTriangle } from 'lucide-react'
import { z } from 'zod'
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useCreateProject } from '@/api/projects'
import { useApiKeyInfo } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'

const schema = z.object({
  name: z.string().min(1, '请填写项目名').max(120, '不超过 120 字'),
  description: z.string().max(500, '不超过 500 字').optional(),
})

// PR-UI-2 retrofit:editorial 表单 — 大留白翻倍 + serif hero。
export function NewProjectPage() {
  const navigate = useNavigate()
  const create = useCreateProject()
  const apiKey = useApiKeyInfo()
  const { toast } = useToast()
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')

  const handleSubmit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const form = e.currentTarget
    const fd = new FormData(form)
    const parsed = schema.safeParse({
      name: (fd.get('name') as string)?.trim(),
      description: (fd.get('description') as string)?.trim() || undefined,
    })
    if (!parsed.success) {
      const fe: Record<string, string> = {}
      for (const issue of parsed.error.issues) {
        fe[issue.path[0] as string] = issue.message
      }
      setErrors(fe)
      // Vercel 表单指南:校验失败聚焦第一个错误字段
      const firstKey = parsed.error.issues[0]?.path[0] as string | undefined
      if (firstKey) {
        const target = form.elements.namedItem(firstKey)
        if (target instanceof HTMLElement) target.focus()
      }
      return
    }
    setErrors({})
    try {
      const proj = await create.mutateAsync(parsed.data)
      toast({ title: '项目已创建', variant: 'success' })
      navigate(`/projects/${proj.id}/upload`, { replace: true })
    } catch (err) {
      toast({
        title: '创建失败',
        description: readApiError(err, '创建失败'),
        variant: 'destructive',
      })
    }
  }

  // useApiKeyInfo:configured ⇔ data != null;404 → null。loading 中显示无横幅。
  const apiKeyMissing = !apiKey.isLoading && apiKey.data == null

  return (
    <div className="mx-auto max-w-3xl px-gutter py-12 page-enter">
      <Button variant="subtle" size="sm" asChild className="mb-8">
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      {apiKeyMissing && (
        <div
          className="mb-10 relative flex items-start gap-3 border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn before:absolute before:left-0 before:right-0 before:top-0 before:h-px before:bg-warn"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="flex-1 leading-relaxed">
            你还未配置 DashScope API Key,新建后需先到「设置」配置才能启动工作流。
          </div>
          <Button asChild variant="ghost" size="sm" className="text-warn">
            <Link to="/settings">前往设置</Link>
          </Button>
        </div>
      )}

      <header className="mb-12 border-b border-rule pb-8">
        <p className="text-meta text-mute mb-3">New project · Step 1 / 3</p>
        <h1 className="font-display text-h1 leading-tight text-ink">
          新建项目
        </h1>
        <p className="mt-4 max-w-prose text-sm text-mute">
          创建后会进入文档上传页,可上传招标文档(技术需求 / 评分细则 / 方案模板)。
        </p>
      </header>

      <Card>
        <CardHeader>
          <p className="text-meta text-mute">Project info</p>
          <CardTitle>项目信息</CardTitle>
          <p className="text-sm text-mute">仅项目名必填,备注辅助理解</p>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-10">
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Label htmlFor="name">
                  项目名 <span className="text-accent">*</span>
                </Label>
                <span className="text-meta text-mute tabular-nums">
                  {name.length} / 120
                </span>
              </div>
              <Input
                id="name"
                name="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                placeholder="如:某市政务云投标"
                maxLength={120}
                aria-invalid={errors.name ? true : undefined}
              />
              {errors.name && (
                <p className="text-xs text-destructive">{errors.name}</p>
              )}
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Label htmlFor="description">备注(可选)</Label>
                <span className="text-meta text-mute tabular-nums">
                  {desc.length} / 500
                </span>
              </div>
              <Textarea
                id="description"
                name="description"
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={4}
                placeholder="项目背景、招标方、关键时间节点等"
                maxLength={500}
                aria-invalid={errors.description ? true : undefined}
              />
              {errors.description && (
                <p className="text-xs text-destructive">{errors.description}</p>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex justify-end gap-3 pt-6">
            <Button asChild variant="secondary" type="button">
              <Link to="/">取消</Link>
            </Button>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? '创建中…' : '创建并继续'}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
