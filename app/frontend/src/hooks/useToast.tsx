import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
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

function ToastViewport({
  items,
  onDismiss,
}: {
  items: ToastItem[]
  onDismiss: (id: number) => void
}) {
  return (
    <div className="pointer-events-none fixed inset-0 z-50 flex flex-col items-end gap-2 p-4 sm:right-4 sm:top-4">
      {items.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onDismiss(t.id)}
          className={cn(
            'pointer-events-auto w-full max-w-sm rounded-lg border bg-card p-4 text-left text-sm shadow-md transition-all',
            t.variant === 'success' && 'border-emerald-200 bg-emerald-50',
            t.variant === 'destructive' && 'border-destructive bg-destructive/10',
            t.variant === 'warning' && 'border-amber-200 bg-amber-50',
            t.variant === 'info' && 'border-sky-200 bg-sky-50',
          )}
        >
          {t.title && <div className="font-medium">{t.title}</div>}
          {t.description && (
            <div className="mt-1 text-xs text-muted-foreground">
              {t.description}
            </div>
          )}
        </button>
      ))}
    </div>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return ctx
}
