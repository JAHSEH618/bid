import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

const STORAGE_KEY = 'dashscope_banner_dismissed'

// REQUIREMENTS §13/D3:登录后首页一次性提示。
// localStorage `dashscope_banner_dismissed=1` 后不再显示。
export function DashScopeBanner({ className }: { className?: string }) {
  const [show, setShow] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined') return
    if (window.localStorage.getItem(STORAGE_KEY) === '1') return
    setShow(true)
  }, [])

  if (!show) return null

  const dismiss = () => {
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, '1')
    }
    setShow(false)
  }

  return (
    <div
      role="status"
      className={cn(
        'flex items-start gap-3 border-b border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900',
        className,
      )}
    >
      <span className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-amber-200 text-xs font-semibold">
        i
      </span>
      <p className="flex-1 leading-relaxed">
        本系统会将文档内容发送至 <strong>阿里云 DashScope 大模型</strong>{' '}
        生成方案,机密项目请评估后使用。
      </p>
      <button
        type="button"
        aria-label="关闭提示"
        onClick={dismiss}
        className="rounded p-1 hover:bg-amber-100"
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}
