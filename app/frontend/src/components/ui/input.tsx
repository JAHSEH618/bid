import * as React from 'react'
import { cn } from '@/lib/utils'

// v2 editorial input — 无 border，仅底部 1px line；focus 时 line 颜色变 accent。
export type InputProps = React.InputHTMLAttributes<HTMLInputElement>

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        ref={ref}
        className={cn(
          'flex h-11 w-full bg-transparent px-0 py-2 text-base',
          'border-0 border-b border-rule rounded-none',
          'ring-offset-background',
          'transition-[border-color] duration-150 ease-out',
          'file:border-0 file:bg-transparent file:text-sm file:font-medium',
          'placeholder:text-mute/60',
          'focus-visible:outline-none focus-visible:border-b-2 focus-visible:border-accent focus-visible:ring-0',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'aria-[invalid=true]:border-destructive',
          className,
        )}
        {...props}
      />
    )
  },
)
Input.displayName = 'Input'
