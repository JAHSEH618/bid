import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, FileCheck2, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { MarkdownRenderer } from '@/lib/markdown'
import { DataExportPanel } from '@/components/DataExportPanel'
import { useProject, useProposalMarkdown } from '@/api/projects'
import { ApiError } from '@/lib/apiFetch'

export function ProposalPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const project = useProject(projectId)
  const proposal = useProposalMarkdown(projectId)

  const isReady = project.data?.status === 'done'

  if (project.isLoading) {
    return (
      <div className="container max-w-5xl space-y-4 py-8">
        <div className="skeleton h-8 w-1/3" />
        <div className="skeleton h-4 w-1/2" />
        <div className="skeleton mt-6 h-32 rounded-xl" />
        <div className="skeleton h-96 rounded-xl" />
      </div>
    )
  }
  if (!project.data) {
    return (
      <div className="container py-12 text-sm text-destructive">
        项目不存在或无访问权限
      </div>
    )
  }

  return (
    <div className="container max-w-5xl space-y-6 py-8 page-enter">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-2xl font-semibold tracking-tight">
            {project.data.name}
          </h1>
          {isReady ? (
            <Badge variant="success">
              <FileCheck2 className="h-3 w-3" />
              已完成
            </Badge>
          ) : (
            <Badge variant="warning">生成中</Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          全文 markdown 预览,可复制 / 下载 .md / 触发 .docx 生成
          {proposal.data && (
            <span className="ml-2 inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs">
              共 {proposal.data.chars.toLocaleString()} 字
            </span>
          )}
        </p>
      </header>

      {!isReady && (
        <Card
          role="status"
          aria-live="polite"
          className="border-amber-200 bg-amber-50/70"
        >
          <CardContent className="flex items-center gap-2 py-4 text-sm text-amber-900">
            <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
            项目尚未完成生成(当前状态:{project.data.status})。下方仅展示已通过章节的占位拼接
          </CardContent>
        </Card>
      )}

      <DataExportPanel
        projectId={projectId}
        projectName={project.data.name}
        markdown={proposal.data?.markdown}
      />

      <Card className="overflow-hidden border-border/70 shadow-sm">
        <CardContent className="px-8 py-8 sm:px-12 sm:py-10">
          {proposal.isLoading && (
            <div
              role="status"
              aria-live="polite"
              className="flex items-center gap-2 text-sm text-muted-foreground"
            >
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              加载全文…
            </div>
          )}
          {proposal.data && (
            <MarkdownRenderer markdown={proposal.data.markdown} />
          )}
          {!proposal.isLoading && !proposal.data && (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-muted">
                <FileCheck2 className="h-6 w-6 text-muted-foreground" />
              </span>
              <p className="text-sm font-medium text-foreground">
                {proposal.error instanceof ApiError && proposal.error.status === 404
                  ? '全文尚未生成'
                  : '暂无内容'}
              </p>
              <p className="max-w-md text-xs text-muted-foreground">
                {proposal.error instanceof ApiError && proposal.error.status === 404
                  ? '所有章节通过审核后,assemble 节点会自动写入 proposal.md'
                  : '请稍后刷新或返回审核页查看进度'}
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
