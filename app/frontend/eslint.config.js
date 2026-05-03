// ESLint 9 flat config —— 替代 v8 旧版 .eslintrc.cjs。
// Vite 项目根 package.json `"type": "module"`,所以本文件用 .js + ESM。
// 规则集对齐迁移前(eslint:recommended + ts/recommended + react-hooks/recommended +
// react-refresh + 自定义 unused-vars)。shadcn ui 原语(src/components/ui/**)不走 lint
// — 它们是上游模板代码,不在我们的维护边界。
import js from '@eslint/js'
import tsParser from '@typescript-eslint/parser'
import tsPlugin from '@typescript-eslint/eslint-plugin'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import globals from 'globals'

export default [
  // 全局忽略
  {
    ignores: [
      'dist',
      'node_modules',
      // 配置/构建产物本身不 lint
      '*.config.js',
      '*.config.ts',
      'vite.config.d.ts',
      // shadcn ui 原语:上游模板,不维护
      'src/components/ui/**',
    ],
  },

  // ESLint 官方 recommended
  js.configs.recommended,

  // TS / TSX 主规则块
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: 'latest',
        sourceType: 'module',
        ecmaFeatures: { jsx: true },
      },
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    plugins: {
      '@typescript-eslint': tsPlugin,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      // typescript-eslint recommended(对齐旧 plugin:@typescript-eslint/recommended)
      ...tsPlugin.configs.recommended.rules,
      // react-hooks recommended(对齐旧 plugin:react-hooks/recommended)
      ...reactHooks.configs.recommended.rules,
      // react-refresh:HMR 边界
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      // _ 前缀豁免(惯例)
      '@typescript-eslint/no-unused-vars': [
        'warn',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      // TS 文件由 tsc 做 undef 检查,关闭 ESLint 重复检查
      // (否则 `React`、DOM lib 类型如 `RequestInit` `HeadersInit` 等会被误报)
      'no-undef': 'off',
    },
    settings: {
      react: { version: '18' },
    },
  },
]
