import { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
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
import { useApiKeyStatus } from '@/api/me'
import { useToast } from '@/hooks/useToast'
import { ApiError } from '@/lib/apiFetch'

const schema = z.object({
  name: z.string().min(1, '请填写项目名').max(120, '不超过 120 字'),
  description: z.string().max(500, '不超过 500 字').optional(),
})

export function NewProjectPage() {
  const navigate = useNavigate()
  const create = useCreateProject()
  const apiKey = useApiKeyStatus()
  const { toast } = useToast()
  const [errors, setErrors] = useState<Record<string, string>>({})

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
      const msg =
        err instanceof ApiError && typeof err.body === 'object' && err.body
          ? ((err.body as { detail?: string }).detail ?? '创建失败')
          : '创建失败'
      toast({ title: '创建失败', description: msg, variant: 'destructive' })
    }
  }

  const apiKeyMissing = apiKey.data && !apiKey.data.configured

  return (
    <div className="container max-w-2xl space-y-6 py-8">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      {apiKeyMissing && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="flex items-center justify-between gap-3 py-4 text-sm">
            <span className="text-amber-900">
              你还未配置 DashScope API Key,新建后需先到「设置」配置才能启动工作流。
            </span>
            <Button asChild variant="outline" size="sm">
              <Link to="/settings">前往设置</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>新建项目</CardTitle>
          <CardDescription>
            创建后会进入文档上传页,上传 3 份文档(技术需求 / 评分细则 / 方案模板)。
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit} noValidate>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">项目名 *</Label>
              <Input
                id="name"
                name="name"
                autoFocus
                placeholder="如:某市政务云投标"
                maxLength={120}
              />
              {errors.name && (
                <p className="text-xs text-destructive">{errors.name}</p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="description">备注(可选)</Label>
              <Textarea
                id="description"
                name="description"
                rows={3}
                placeholder="项目背景、招标方、关键时间节点等"
                maxLength={500}
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
              {create.isPending ? '创建中…' : '创建项目'}
            </Button>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
