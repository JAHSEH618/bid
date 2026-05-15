import { useMemo, useState } from 'react'
import { Link, useParams, useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  Loader2,
  RotateCcw,
  SkipForward,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { useProject } from '@/api/projects'
import { useToast } from '@/hooks/useToast'
import { apiFetch, readApiError } from '@/lib/apiFetch'
import type { ProjectDTO } from '@/lib/types'

// PR-M8-1:材料理解评审页。
// LLM-0 把招标材料黑板拆成 5 个分组 JSON;用户在这里 pass / revise / skip。
//
// 端点:
//   GET  /api/projects/{id}/material-understanding   → { material_understanding: {...} }
//   POST /api/projects/{id}/material-understanding/decision { decision, feedback? }

interface MaterialUnderstanding {
  core_requirements?: string[]
  scoring_focus?: string[]
  template_style?: string[]
  key_constraints?: string[]
  risk_notes?: string[]
  [key: string]: string[] | undefined
}

interface MaterialUnderstandingResponse {
  project_id: number
  material_understanding: MaterialUnderstanding
}

type Decision = 'pass' | 'revise' | 'skip'

const SECTIONS: { key: keyof MaterialUnderstanding; label: string; meta: string }[] = [
  {
    key: 'core_requirements',
    label: '核心需求',
    meta: 'Core requirements',
  },
  {
    key: 'scoring_focus',
    label: '评分要点',
    meta: 'Scoring focus',
  },
  {
    key: 'template_style',
    label: '模板风格',
    meta: 'Template style',
  },
  {
    key: 'key_constraints',
    label: '关键约束',
    meta: 'Key constraints',
  },
  {
    key: 'risk_notes',
    label: '风险标注',
    meta: 'Risk notes',
  },
]

export function MaterialUnderstandingPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const { toast } = useToast()
  const qc = useQueryClient()
  const project = useProject(projectId)

  const understanding = useQuery<MaterialUnderstandingResponse, Error>({
    queryKey: ['projects', projectId, 'material-understanding'],
    queryFn: () =>
      apiFetch<MaterialUnderstandingResponse>(
        `/api/projects/${projectId}/material-understanding`,
      ),
    enabled: !Number.isNaN(projectId),
    refetchInterval: (q) => {
      const status = project.data?.status
      if (status === 'awaiting_material_understanding') return false
      // LLM-0 还没出结果时轮询;就绪后停
      return q.state.data ? false : 3_000
    },
  })

  const [feedback, setFeedback] = useState('')

  const decide = useMutation({
    mutationFn: (decision: Decision) =>
      apiFetch<{ ok: boolean }>(
        `/api/projects/${projectId}/material-understanding/decision`,
        {
          method: 'POST',
          body: JSON.stringify({
            decision,
            feedback: decision === 'revise' ? feedback.trim() : null,
          }),
        },
      ),
    onSuccess: (_data, decision) => {
      qc.invalidateQueries({
        queryKey: ['projects', projectId, 'material-understanding'],
      })
      if (decision === 'pass' || decision === 'skip') {
        // 后端 decision 端点只是入队 resume_review_task,DB 里 status 还会维持
        // awaiting_material_understanding 一小段时间。如果直接 navigate 到 /outline,
        // OutlineConfirmPage 的 status 守卫会把用户弹回 /understanding(甚至再到 /review)。
        // 乐观更新到 "outlining",让 /outline 页面接得住;真实 status 后续 useProject
        // 轮询会自然覆盖。
        qc.setQueryData<ProjectDTO | undefined>(
          ['projects', projectId],
          (prev) => (prev ? { ...prev, status: 'outlining' } : prev),
        )
        toast({
          variant: 'success',
          title: '已提交',
          description: '继续生成提纲…',
        })
        navigate(`/projects/${projectId}/outline`, { replace: true })
      } else {
        qc.invalidateQueries({ queryKey: ['projects', projectId] })
        toast({
          variant: 'info',
          title: 'LLM 正在重新理解材料',
          description: '完成后会自动刷新本页',
        })
        setFeedback('')
      }
    },
    onError: (err) => {
      toast({
        variant: 'destructive',
        title: '提交失败',
        description: readApiError(err, '请重试'),
      })
    },
  })

  const payload = useMemo(
    () => understanding.data?.material_understanding ?? null,
    [understanding.data],
  )

  const projectStatus = project.data?.status
  const isReady = projectStatus === 'awaiting_material_understanding'
  const stillRunning = projectStatus && !isReady && projectStatus !== 'failed'

  return (
    <div className="mx-auto max-w-5xl px-gutter py-12 page-enter">
      <Button variant="subtle" size="sm" asChild className="mb-8">
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="mb-12 border-b border-rule pb-8">
        <p className="text-meta text-mute mb-3">
          Material understanding · Step 2 / 3
        </p>
        <h1 className="font-display text-h1 leading-tight text-ink">
          项目材料理解
        </h1>
        <p className="mt-4 max-w-prose text-sm text-mute">
          LLM-0 已经读完你的招标材料,在进入提纲生成之前,请确认它的理解是否
          与你预期一致。通过 → 直接进提纲;修订 → LLM 用反馈重读;跳过 →
          忽略本步直接进提纲(不会重读)。
        </p>
        {project.data && (
          <p className="mt-4 flex items-center gap-2">
            <Badge variant="outline">{project.data.name}</Badge>
            <span className="text-meta text-mute">
              status · {projectStatus}
            </span>
          </p>
        )}
      </header>

      {understanding.isLoading && !payload && (
        <div className="flex items-center gap-3 text-sm text-mute">
          <Loader2 className="h-4 w-4 animate-spin" />
          加载材料理解…
        </div>
      )}

      {understanding.error && !payload && (
        <div className="border border-rule bg-paper-2 px-6 py-8 text-sm">
          <p className="font-display text-h3 text-ink mb-2">暂未就绪</p>
          <p className="text-mute">
            后端返回:{readApiError(understanding.error, '404 / 503')}。
            如果项目刚启动,LLM-0 仍在跑;稍候自动刷新。
          </p>
        </div>
      )}

      {payload && (
        <div className="space-y-10">
          {SECTIONS.map((section) => {
            const items = payload[section.key] ?? []
            return (
              <Card key={section.key}>
                <CardHeader>
                  <p className="text-meta text-mute">{section.meta}</p>
                  <CardTitle>{section.label}</CardTitle>
                </CardHeader>
                <CardContent>
                  {items.length === 0 ? (
                    <p className="text-sm text-mute italic">
                      LLM 在本组未识别到内容(可能是材料里确实没有,也可能是漏读 — 用「修订」反馈)
                    </p>
                  ) : (
                    <ul className="space-y-3 text-sm text-ink">
                      {items.map((item, idx) => (
                        <li
                          key={`${section.key}-${idx}`}
                          className="flex gap-3"
                        >
                          <span className="font-mono text-meta text-mute pt-0.5 shrink-0">
                            {String(idx + 1).padStart(2, '0')}
                          </span>
                          <span className="leading-relaxed">{item}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            )
          })}

          <Card>
            <CardHeader>
              <p className="text-meta text-mute">Decision</p>
              <CardTitle>下一步</CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="space-y-2">
                <label
                  htmlFor="feedback"
                  className="text-meta text-mute"
                >
                  修订反馈(仅在选「修订」时需要填)
                </label>
                <Textarea
                  id="feedback"
                  value={feedback}
                  onChange={(e) => setFeedback(e.target.value)}
                  rows={4}
                  placeholder="例:漏读了招标方对运维 SLA 的要求 / 评分要点里把权重 30% 的合规项漏了"
                />
              </div>

              {stillRunning && (
                <div className="flex items-center gap-2 text-xs text-mute">
                  <AlertCircle className="h-3.5 w-3.5" />
                  项目当前 status={projectStatus},决策按钮在
                  awaiting_material_understanding 时才生效。
                </div>
              )}

              <div className="flex flex-wrap items-center gap-3">
                <Button
                  variant="default"
                  onClick={() => decide.mutate('pass')}
                  disabled={
                    decide.isPending ||
                    projectStatus !== 'awaiting_material_understanding'
                  }
                >
                  <Check className="mr-1 h-4 w-4" />
                  通过 → 进提纲
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => decide.mutate('revise')}
                  disabled={
                    decide.isPending ||
                    !feedback.trim() ||
                    projectStatus !== 'awaiting_material_understanding'
                  }
                >
                  <RotateCcw className="mr-1 h-4 w-4" />
                  修订 → 让 LLM 重读
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => decide.mutate('skip')}
                  disabled={
                    decide.isPending ||
                    projectStatus !== 'awaiting_material_understanding'
                  }
                >
                  <SkipForward className="mr-1 h-4 w-4" />
                  跳过 → 不重读直接进提纲
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
