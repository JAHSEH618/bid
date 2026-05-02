import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { MarkdownRenderer } from '@/lib/markdown'
import { DataExportPanel } from '@/components/DataExportPanel'
import { useProjectDetail, useProposalMarkdown } from '@/api/projects'

export function ProposalPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const detail = useProjectDetail(projectId)
  const proposal = useProposalMarkdown(projectId)

  const project = detail.data?.project
  const isReady = project?.status === 'done'

  if (detail.isLoading) {
    return (
      <div className="container py-12 text-sm text-muted-foreground">
        加载中…
      </div>
    )
  }
  if (!project) {
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
          {project.name} · 完整方案
        </h1>
        <p className="text-sm text-muted-foreground">
          全文 markdown 预览,可复制 / 下载 .md / 触发 .docx 生成。
        </p>
      </header>

      {!isReady && (
        <Card className="border-amber-200 bg-amber-50">
          <CardContent className="py-4 text-sm text-amber-900">
            项目尚未完成生成(当前状态:{project.status})。仅展示已通过章节的占位拼接。
          </CardContent>
        </Card>
      )}

      <DataExportPanel
        projectId={projectId}
        projectName={project.name}
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
            <p className="text-sm text-muted-foreground">暂无内容。</p>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
