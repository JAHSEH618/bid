import type { Config } from 'tailwindcss'
import animate from 'tailwindcss-animate'

// v2 design tokens — 瑞典编辑风。
// Editorial-first：色板 = 单色 + 1 个 rust accent；圆角默认 2px；阴影默认 none。
// 兼容性：shadcn 既有的 background/foreground/primary/... 仍然映射到新调色板，
// 旧页面的 className 不会立即坏掉（视觉会有些 mismatch，UI-2 retrofit 时再统一）。
const config: Config = {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '1.5rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        // === Editorial palette（推荐新页面直接使用） ===
        ink: 'hsl(var(--ink))',
        paper: {
          DEFAULT: 'hsl(var(--paper))',
          2: 'hsl(var(--paper-2))',
        },
        'paper-2': 'hsl(var(--paper-2))',
        rule: 'hsl(var(--rule) / 0.10)',
        mute: 'hsl(var(--mute))',
        warn: 'hsl(var(--warn))',

        // === Shadcn-compatible tokens（指向同一组 editorial 变量） ===
        border: 'hsl(var(--rule) / 0.10)',
        input: 'hsl(var(--rule) / 0.10)',
        ring: 'hsl(var(--ink))',
        background: 'hsl(var(--paper))',
        foreground: 'hsl(var(--ink))',
        primary: {
          DEFAULT: 'hsl(var(--ink))',
          foreground: 'hsl(var(--paper))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--paper-2))',
          foreground: 'hsl(var(--ink))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        success: {
          DEFAULT: 'hsl(var(--success))',
          foreground: 'hsl(var(--success-foreground))',
        },
        warning: {
          DEFAULT: 'hsl(var(--warning))',
          foreground: 'hsl(var(--warning-foreground))',
        },
        info: {
          DEFAULT: 'hsl(var(--info))',
          foreground: 'hsl(var(--info-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--paper-2))',
          foreground: 'hsl(var(--mute))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--paper))',
          foreground: 'hsl(var(--ink))',
        },
        popover: {
          DEFAULT: 'hsl(var(--paper))',
          foreground: 'hsl(var(--ink))',
        },
      },
      fontFamily: {
        display: [
          'Tiempos Headline',
          'Noto Serif SC',
          'Georgia',
          'Songti SC',
          'STSong',
          'serif',
        ],
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'PingFang SC',
          'Microsoft YaHei',
          'Segoe UI',
          'sans-serif',
        ],
        mono: [
          'JetBrains Mono',
          'SFMono-Regular',
          'Menlo',
          'Consolas',
          'monospace',
        ],
      },
      fontSize: {
        // editorial scale — 大跨度
        hero: ['64px', { lineHeight: '1.05', letterSpacing: '-0.02em' }],
        h1: ['40px', { lineHeight: '1.1', letterSpacing: '-0.01em' }],
        h2: ['28px', { lineHeight: '1.2', letterSpacing: '-0.005em' }],
        h3: ['20px', { lineHeight: '1.3' }],
        body: ['16px', { lineHeight: '1.6' }],
        meta: ['13px', { lineHeight: '1.4', letterSpacing: '0.08em' }],
      },
      spacing: {
        gutter: 'var(--gutter)',
        rhythm: 'var(--rhythm)',
      },
      maxWidth: {
        prose: 'var(--prose-max)',
      },
      borderRadius: {
        xl: '4px',
        lg: 'var(--radius)',
        md: 'var(--radius)',
        sm: '0',
        none: '0',
      },
      borderWidth: {
        DEFAULT: '1px',
        hairline: '1px',
        rule: '1px',
        emphasis: '3px',
      },
      boxShadow: {
        none: 'none',
        sm: 'none',
        DEFAULT: 'var(--shadow-md)',
        md: 'var(--shadow-md)',
        lg: 'var(--shadow-lg)',
        xl: 'var(--shadow-xl)',
      },
      keyframes: {
        'accordion-down': {
          from: { height: '0' },
          to: { height: 'var(--radix-accordion-content-height)' },
        },
        'accordion-up': {
          from: { height: 'var(--radix-accordion-content-height)' },
          to: { height: '0' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
      },
      animation: {
        'accordion-down': 'accordion-down 0.2s ease-out',
        'accordion-up': 'accordion-up 0.2s ease-out',
        'fade-in': 'fade-in 0.2s ease-out',
      },
      transitionTimingFunction: {
        'out-soft': 'cubic-bezier(0.2, 0.8, 0.2, 1)',
      },
    },
  },
  plugins: [animate],
}

export default config
