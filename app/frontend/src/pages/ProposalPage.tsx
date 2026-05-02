import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
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
      <div className="container py-12 text-sm text-muted-foreground">
        加载中…
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
    <div className="container max-w-5xl space-y-6 py-8">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/">
          <ArrowLeft className="mr-1 h-4 w-4" />
          返回项目列表
        </Link>
      </Button>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">
          {project.data.name} · 完整方案
        </h1>
        <p className="text-sm text-muted-foreground">
          全文 markdown 预览,可复制 / 下载 .md / 触发 .docx 生成。
          {proposal.data && (
            <span className="ml-2">{proposal.data.chars} 字</span>
          )}
        </p>
      </header>

      {!isReady && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-4 text-sm text-amber-900">
            项目尚未完成生成(当前状态:{project.data.status})。仅展示已通过章节的占位拼接。
          </CardContent>
        </Card>
      )}

      <DataExportPanel
        projectId={projectId}
        projectName={project.data.name}
        markdown={proposal.data?.markdown}
      />

      <Card>
        <CardContent className="px-8 py-8">
          {proposal.isLoading && (
            <p className="text-sm text-muted-foreground">加载全文…</p>
          )}
          {proposal.data && (
            <MarkdownRenderer markdown={proposal.data.markdown} />
          )}
          {!proposal.isLoading && !proposal.data && (
            <p className="text-sm text-muted-foreground">
              {proposal.error instanceof ApiError && proposal.error.status === 404
                ? '全文尚未生成。等所有章节通过审核后,assemble 节点会写入 proposal.md。'
                : '暂无内容。'}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
