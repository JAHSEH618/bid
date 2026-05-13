import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { placeholderLabel, scanPlaceholders } from '@/lib/redaction'
import { cn } from '@/lib/utils'

export interface PlaceholderBannerProps {
  // 待扫描的章节 / 全文 markdown。
  markdown: string | null | undefined
  className?: string
}

// PR-M6-1 / D3：占位符提示 banner + 清单抽屉。
// - 无占位符 → 不渲染。
// - 有占位符 → 顶部 1px warn 边条 banner，按钮打开清单 dialog。
// - 清单只显示占位符 + 类型，不展示原值（后端从不持久化原值）。
export function PlaceholderBanner({ markdown, className }: PlaceholderBannerProps) {
  const items = useMemo(() => scanPlaceholders(markdown), [markdown])
  const [open, setOpen] = useState(false)

  if (items.length === 0) return null

  const total = items.reduce((sum, it) => sum + it.count, 0)
  const kindCount = new Set(items.map((it) => it.kind)).size

  return (
    <>
      <div
        role="status"
        aria-live="polite"
        className={cn(
          'relative flex items-start justify-between gap-3 border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn',
          // 顶部 1px accent line — editorial 信号
          'before:absolute before:left-0 before:right-0 before:top-0 before:h-px before:bg-warn',
          className,
        )}
      >
        <div className="flex items-start gap-2">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">
              本文档含 {total} 处占位符（{items.length} 个唯一值，{kindCount} 类）
            </p>
            <p className="text-xs text-warn/80 mt-1">
              形如 <code className="font-mono">__ORG_xxxxxx__</code> 的 token 是后端
              脱敏自动生成的占位符。导出前请按原材料手动替换为真实公司名 / 项目号 /
              电话等敏感信息。
            </p>
          </div>
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setOpen(true)}
          className="text-warn hover:text-warn"
        >
          查看清单
        </Button>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>占位符清单</DialogTitle>
            <DialogDescription>
              本文档共 {items.length} 个唯一占位符。下表只展示占位符与类型；
              原值在后端不会持久化，请回到原始材料里查找并替换。
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[420px] overflow-y-auto pr-1">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-mute">
                  <th className="py-2 pr-4 font-medium">类型</th>
                  <th className="py-2 pr-4 font-medium">占位符</th>
                  <th className="py-2 text-right font-medium">出现次数</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.raw} className="border-b border-rule/60">
                    <td className="py-2 pr-4">
                      <Badge variant="warn">{placeholderLabel(it.kind)}</Badge>
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-ink">
                      {it.raw}
                    </td>
                    <td className="py-2 text-right text-mute">{it.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
