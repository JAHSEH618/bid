import { useState } from 'react'
import { Download, FileText, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  downloadDocxUrl,
  downloadMarkdownUrl,
  useDocxJob,
  useTriggerDocx,
} from '@/api/docx'
import { useToast } from '@/hooks/useToast'
import type { DocxJobStatus } from '@/lib/types'

// 后端 stage 已返回中文进度提示(如「渲染流程图...」),前端直接用,不再二次映射。
const STATUS_VARIANT: Record<
  DocxJobStatus,
  'secondary' | 'success' | 'destructive' | 'info' | 'warning'
> = {
  pending: 'info',
  processing: 'info',
  done: 'success',
  failed: 'destructive',
  invalidated: 'warning',
}

export interface DataExportPanelProps {
  projectId: number
  projectName: string
  markdown?: string
}

// REQUIREMENTS P6 / FR-5:全文预览 + 复制 + .md / .docx 下载 + 进度条。
// DOCX 流程:POST 触发 → 轮询 docx-job → done 后链接转可下载。
// invalidated 状态(D-CG):显示"原文档已更新,需重新生成"。
export function DataExportPanel({
  projectId,
  projectName,
  markdown,
}: DataExportPanelProps) {
  const { toast } = useToast()
  const [docxJobId, setDocxJobId] = useState<number | null>(null)

  const triggerDocx = useTriggerDocx()
  const docxJob = useDocxJob(projectId, docxJobId, {
    refetchInterval:
      docxJobId == null
        ? false
        : (query) => {
            const status = query.state.data?.status
            if (status === 'done' || status === 'failed') return false
            return 3_000
          },
  })

  const handleGenerate = () => {
    triggerDocx.mutate(projectId, {
      onSuccess: (data) => {
        setDocxJobId(data.docx_job_id)
        toast({
          title: data.cached ? '已复用上次生成的 DOCX' : 'DOCX 已开始生成',
          variant: data.cached ? 'default' : 'success',
        })
      },
      onError: (err) => {
        toast({
          title: 'DOCX 触发失败',
          description: String(err),
          variant: 'destructive',
        })
      },
    })
  }

  const handleCopyMarkdown = async () => {
    if (!markdown) return
    try {
      await navigator.clipboard.writeText(markdown)
      toast({ title: '已复制到剪贴板', variant: 'success' })
    } catch {
      toast({ title: '复制失败,请手动选择', variant: 'destructive' })
    }
  }

  const job = docxJob.data
  const isDone = job?.status === 'done'
  const isFailed = job?.status === 'failed'
  const isWorking =
    docxJobId != null && job && job.status !== 'done' && job.status !== 'failed'

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-base">导出</CardTitle>
        <span className="text-xs text-muted-foreground">{projectName}</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCopyMarkdown}
            disabled={!markdown}
          >
            <FileText className="mr-1.5 h-4 w-4" />
            复制 Markdown
          </Button>
          <Button
            asChild={!!markdown}
            variant="outline"
            size="sm"
            disabled={!markdown}
          >
            {markdown ? (
              <a href={downloadMarkdownUrl(projectId)} download>
                <Download className="mr-1.5 h-4 w-4" />
                下载 .md
              </a>
            ) : (
              <span>
                <Download className="mr-1.5 h-4 w-4" />
                下载 .md
              </span>
            )}
          </Button>
          <Button
            size="sm"
            onClick={handleGenerate}
            disabled={triggerDocx.isPending || isWorking}
          >
            {triggerDocx.isPending || isWorking ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                生成中…
              </>
            ) : (
              <>
                <RefreshCw className="mr-1.5 h-4 w-4" />
                生成 .docx
              </>
            )}
          </Button>
          {isDone && (
            <Button asChild variant="default" size="sm">
              <a href={downloadDocxUrl(projectId)} download>
                <Download className="mr-1.5 h-4 w-4" />
                下载 .docx
              </a>
            </Button>
          )}
        </div>

        {job && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant={STATUS_VARIANT[job.status]}>{job.stage}</Badge>
            {isFailed && job.error && (
              <span className="text-destructive">{job.error}</span>
            )}
            {job.status === 'invalidated' && (
              <span className="text-amber-700">
                请点击「生成 .docx」重新打包
              </span>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
