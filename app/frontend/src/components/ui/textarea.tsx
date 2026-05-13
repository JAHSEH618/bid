import * as React from 'react'
import { cn } from '@/lib/utils'

// v2 editorial textarea — 无 border，仅底部 1px line；focus 时 line 颜色变 accent。
export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        ref={ref}
        className={cn(
          'flex min-h-[96px] w-full bg-transparent px-0 py-2 text-base',
          'border-0 border-b border-rule rounded-none',
          'ring-offset-background transition-[border-color] duration-150 ease-out',
          'placeholder:text-mute/60',
          'focus-visible:outline-none focus-visible:border-b-2 focus-visible:border-accent focus-visible:ring-0',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'aria-[invalid=true]:border-destructive',
          'resize-y',
          className,
        )}
        {...props}
      />
    )
  },
)
Textarea.displayName = 'Textarea'
