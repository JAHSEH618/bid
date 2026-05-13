import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// v2 editorial button — 1px hairline / 几乎无圆角 / 无阴影。
// default = ink fill + paper text；secondary = 1px ink border；ghost = underline text。
const buttonVariants = cva(
  [
    'inline-flex items-center justify-center whitespace-nowrap rounded-[2px]',
    'text-sm font-medium ring-offset-background',
    'transition-[background-color,color,border-color,opacity] duration-150 ease-out',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2',
    'disabled:pointer-events-none disabled:opacity-50',
    'select-none touch-manipulation',
  ].join(' '),
  {
    variants: {
      variant: {
        // ink 填充 + paper 文字 — 主 CTA
        default:
          'bg-ink text-paper hover:bg-ink/88 active:bg-ink',
        destructive:
          'bg-destructive text-destructive-foreground hover:bg-destructive/88',
        // 1px ink border + transparent — 次级
        secondary:
          'border border-ink bg-transparent text-ink hover:bg-ink hover:text-paper',
        outline:
          'border border-ink bg-transparent text-ink hover:bg-ink hover:text-paper',
        // underline 文本按钮 — ghost / 内联
        ghost:
          'bg-transparent text-ink underline underline-offset-4 decoration-1 hover:decoration-2 hover:text-accent',
        // 与 ghost 区分:无下划线、低调悬停。给原 NavItem / 工具栏复用。
        subtle:
          'bg-transparent text-mute hover:text-ink hover:bg-paper-2',
        link:
          'bg-transparent text-ink underline underline-offset-4 decoration-1 hover:decoration-2 hover:text-accent px-0 h-auto',
        success:
          'bg-success text-success-foreground hover:bg-success/88',
        // 强调（accent rust）— 留给「锁定目录 / 关键确认」等少量 CTA
        accent:
          'bg-accent text-paper hover:bg-accent/88',
      },
      size: {
        default: 'h-10 px-5 py-2',
        sm: 'h-9 px-3 text-sm',
        xs: 'h-7 px-2.5 text-xs',
        lg: 'h-12 px-7 text-[15px]',
        icon: 'h-10 w-10',
        iconSm: 'h-8 w-8',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    )
  },
)
Button.displayName = 'Button'

export { buttonVariants }
