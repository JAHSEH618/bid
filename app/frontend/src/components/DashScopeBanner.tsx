import { useEffect, useState } from 'react'
import { Info, X } from 'lucide-react'
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
        'flex items-center gap-3 border-b border-amber-200 bg-amber-50/80 px-6 py-2.5 text-xs text-amber-900',
        className,
      )}
    >
      <Info className="h-3.5 w-3.5 shrink-0 text-amber-600" />
      <p className="flex-1 leading-relaxed">
        本系统会将文档内容发送至{' '}
        <strong className="font-semibold">阿里云 DashScope 大模型</strong>{' '}
        生成方案,机密项目请评估后使用
      </p>
      <button
        type="button"
        aria-label="关闭提示"
        onClick={dismiss}
        className="rounded-md p-1 text-amber-700/70 transition-colors hover:bg-amber-100 hover:text-amber-900"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
