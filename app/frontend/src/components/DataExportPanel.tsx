import { useState } from 'react'
import {
  Copy,
  Download,
  FileText,
  Loader2,
  Package,
  RefreshCw,
} from 'lucide-react'
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
import { readApiError } from '@/lib/apiFetch'
import type { DocxJobStatus } from '@/lib/types'

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
          description: readApiError(err, '后端无法接收 DOCX 任务'),
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
    <Card className="border-border/70 shadow-sm">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Package className="h-4 w-4" />
          </span>
          <div>
            <CardTitle className="text-base">导出方案</CardTitle>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {projectName}
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCopyMarkdown}
            disabled={!markdown}
          >
            <Copy className="mr-1.5 h-4 w-4" />
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
                <FileText className="mr-1.5 h-4 w-4" />
                下载 .md
              </a>
            ) : (
              <span>
                <FileText className="mr-1.5 h-4 w-4" />
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
            <Button
              asChild
              size="sm"
              className="shadow-md shadow-primary/15"
            >
              <a href={downloadDocxUrl(projectId)} download>
                <Download className="mr-1.5 h-4 w-4" />
                下载 .docx
              </a>
            </Button>
          )}
        </div>

        {job && (
          <div
            role="status"
            aria-live="polite"
            className="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 px-3 py-2 text-xs"
          >
            <Badge variant={STATUS_VARIANT[job.status]}>{job.stage}</Badge>
            {isWorking && (
              <span className="flex items-center gap-1 text-muted-foreground">
                <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
                正在打包…
              </span>
            )}
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
