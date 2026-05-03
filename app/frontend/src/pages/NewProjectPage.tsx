import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { ArrowLeft, AlertTriangle, Sparkles } from 'lucide-react'
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
import { Textarea } from '@/components/ui/textarea'
import { useCreateProject } from '@/api/projects'
import { useApiKeyInfo } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { readApiError } from '@/lib/apiFetch'

const schema = z.object({
  name: z.string().min(1, '请填写项目名').max(120, '不超过 120 字'),
  description: z.string().max(500, '不超过 500 字').optional(),
})

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
    const fd = new FormData(e.currentTarget)
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
    <div className="container max-w-2xl space-y-6 py-8 page-enter">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      {apiKeyMissing && (
        <Card className="border-amber-200 bg-amber-50/70">
          <CardContent className="flex items-center gap-3 py-4 text-sm">
            <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
            <div className="flex-1 leading-relaxed text-amber-900">
              你还未配置 DashScope API Key,新建后需先到「设置」配置才能启动工作流。
            </div>
            <Button asChild variant="outline" size="sm">
              <Link to="/settings">前往设置</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <header className="space-y-1">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Sparkles className="h-5 w-5 text-primary" />
          新建项目
        </h1>
        <p className="text-sm text-muted-foreground">
          创建后会进入文档上传页,上传 3 份文档(技术需求 / 评分细则 / 方案模板)
        </p>
      </header>

      <Card className="shadow-sm">
        <CardHeader>
          <CardTitle className="text-base">项目信息</CardTitle>
          <CardDescription>仅项目名必填,备注辅助理解</CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="name">
                  项目名 <span className="text-destructive">*</span>
                </Label>
                <span className="text-[11px] tabular-nums text-muted-foreground">
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
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="description">备注(可选)</Label>
                <span className="text-[11px] tabular-nums text-muted-foreground">
                  {desc.length} / 500
                </span>
              </div>
              <Textarea
                id="description"
                name="description"
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
                rows={3}
                placeholder="项目背景、招标方、关键时间节点等"
                maxLength={500}
                aria-invalid={errors.description ? true : undefined}
              />
              {errors.description && (
                <p className="text-xs text-destructive">{errors.description}</p>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex justify-end gap-2">
            <Button asChild variant="outline" type="button">
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
