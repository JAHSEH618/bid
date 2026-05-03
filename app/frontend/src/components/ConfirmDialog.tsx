import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

// 风格统一的确认弹窗,替代 window.confirm。
// 用法:
//   const confirm = useConfirm()
//   const ok = await confirm({ title: '...', description: '...', destructive: true })
//   if (!ok) return
//
// 比 window.confirm 强的地方:
//   - shadcn 视觉一致
//   - destructive 时按钮变红
//   - description 支持 ReactNode(可换行 / 高亮)
//   - 异步 await(window.confirm 是同步阻塞,某些环境会被浏览器拦截)
export interface ConfirmOptions {
  title: string
  description?: React.ReactNode
  confirmText?: string
  cancelText?: string
  destructive?: boolean
}

interface PendingConfirm extends ConfirmOptions {
  resolve: (ok: boolean) => void
}

let setPendingExternal: ((p: PendingConfirm | null) => void) | null = null

export function ConfirmDialogHost() {
  const [pending, setPending] = useState<PendingConfirm | null>(null)
  setPendingExternal = setPending

  const close = (ok: boolean) => {
    if (!pending) return
    pending.resolve(ok)
    setPending(null)
  }

  return (
    <Dialog open={pending != null} onOpenChange={(v) => !v && close(false)}>
      <DialogContent
        // 防止误删/误确认:destructive 时把默认 autoFocus 让给 Cancel,
        // 避免用户回车后立即触发不可恢复操作(Vercel 指南:破坏性操作需慎用 autoFocus)。
        onOpenAutoFocus={(e) => {
          if (pending?.destructive) e.preventDefault()
        }}
      >
        <DialogHeader>
          <DialogTitle>{pending?.title}</DialogTitle>
          {pending?.description && (
            <DialogDescription>{pending.description}</DialogDescription>
          )}
        </DialogHeader>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => close(false)}
            autoFocus={pending?.destructive}
          >
            {pending?.cancelText ?? '取消'}
          </Button>
          <Button
            variant={pending?.destructive ? 'destructive' : 'default'}
            onClick={() => close(true)}
            autoFocus={!pending?.destructive}
          >
            {pending?.confirmText ?? '确认'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise<boolean>((resolve) => {
    if (!setPendingExternal) {
      // host 没挂载时回退 window.confirm,保险起见
      resolve(window.confirm(options.title))
      return
    }
    setPendingExternal({ ...options, resolve })
  })
}
