import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

// 极简 toast。比 shadcn 完整 toast 短,够用就好;后续如需 Promise/Action 再升级。
export type ToastVariant =
  | 'default'
  | 'success'
  | 'destructive'
  | 'warning'
  | 'info'

export interface ToastInput {
  title?: string
  description?: string
  variant?: ToastVariant
  durationMs?: number
}

interface ToastItem extends Required<Omit<ToastInput, 'description'>> {
  id: number
  description?: string
}

interface ToastContextValue {
  toast: (input: ToastInput) => void
  dismiss: (id: number) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

const DEFAULT_DURATION = 4000

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const idRef = useRef(0)

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const toast = useCallback(
    (input: ToastInput) => {
      const id = ++idRef.current
      const item: ToastItem = {
        id,
        title: input.title ?? '',
        description: input.description,
        variant: input.variant ?? 'default',
        durationMs: input.durationMs ?? DEFAULT_DURATION,
      }
      setItems((prev) => [...prev, item])
      window.setTimeout(() => dismiss(id), item.durationMs)
    },
    [dismiss],
  )

  const value = useMemo(() => ({ toast, dismiss }), [toast, dismiss])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastViewport items={items} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

function variantStyles(v: ToastVariant) {
  // v2 editorial toast — 极简：纯文本 + 顶部 1px accent line。
  // 不同 variant 切换 accent line 颜色（默认 ink / success / destructive ...）。
  switch (v) {
    case 'success':
      return {
        accent: 'bg-success',
        icon: <CheckCircle2 className="h-4 w-4 text-success" />,
      }
    case 'destructive':
      return {
        accent: 'bg-destructive',
        icon: <AlertCircle className="h-4 w-4 text-destructive" />,
      }
    case 'warning':
      return {
        accent: 'bg-warn',
        icon: <AlertTriangle className="h-4 w-4 text-warn" />,
      }
    case 'info':
      return {
        accent: 'bg-ink',
        icon: <Info className="h-4 w-4 text-mute" />,
      }
    default:
      return {
        accent: 'bg-ink',
        icon: <Info className="h-4 w-4 text-mute" />,
      }
  }
}

function ToastViewport({
  items,
  onDismiss,
}: {
  items: ToastItem[]
  onDismiss: (id: number) => void
}) {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex flex-col items-end gap-2 p-4 sm:right-4 sm:top-4">
      {items.map((t) => {
        const v = variantStyles(t.variant)
        return (
          <div
            key={t.id}
            role="status"
            className={cn(
              'pointer-events-auto relative flex w-full max-w-sm items-start gap-3 bg-paper px-4 py-3 pr-8 text-ink animate-fade-up',
              'border border-rule rounded-none',
            )}
          >
            {/* 顶部 1px accent line — variant 信号 */}
            <span
              aria-hidden="true"
              className={cn(
                'absolute left-0 right-0 top-0 h-px',
                v.accent,
              )}
            />
            <span className="mt-0.5 shrink-0">{v.icon}</span>
            <div className="min-w-0 flex-1 text-sm">
              {t.title && (
                <div className="font-medium leading-tight">{t.title}</div>
              )}
              {t.description && (
                <div
                  className={cn(
                    'text-xs leading-relaxed text-mute',
                    t.title ? 'mt-1' : '',
                  )}
                >
                  {t.description}
                </div>
              )}
            </div>
            <button
              type="button"
              onClick={() => onDismiss(t.id)}
              aria-label="关闭"
              className="absolute right-2 top-2 p-1 text-mute transition-colors hover:text-ink"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        )
      })}
    </div>
  )
}

// TODO(M4-后续): 把 useToast hook 与 ToastProvider 拆到不同文件以满足 react-refresh。
// 现状:Provider + hook 同文件,HMR 边界不纯净但便于一次 import。
// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return ctx
}
