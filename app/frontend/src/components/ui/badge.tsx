import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// v2 editorial badge — 极简标签：1px hairline 边框 + 大写字间距。
const badgeVariants = cva(
  'inline-flex items-center gap-1 border px-2 py-0.5 text-[11px] font-medium leading-none uppercase tracking-[0.06em] rounded-[2px] transition-colors',
  {
    variants: {
      variant: {
        default: 'border-ink bg-ink text-paper',
        secondary: 'border-rule bg-paper-2 text-ink',
        outline: 'border-rule bg-transparent text-ink',
        muted: 'border-rule bg-transparent text-mute',
        accent: 'border-accent bg-accent text-paper',
        warn: 'border-warn bg-warn/10 text-warn',
        destructive: 'border-destructive bg-destructive/10 text-destructive',
        success: 'border-success bg-success/10 text-success',
        warning: 'border-warn bg-warn/10 text-warn',
        info: 'border-rule bg-transparent text-ink',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  )
}
