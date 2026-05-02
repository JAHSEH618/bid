// Mock data layer。开发期 `pnpm dev` 默认开启,设置 VITE_API_REAL=1 切回真请求。
//
// 工作机制:
//   - apiFetch 在 isMockEnabled() 时绕过 fetch,走 mockResolve(path, init)
//   - EventSource(`/api/projects/{id}/stream`)由 useSSE 改成 MockProjectEventSource
//
// 字段名 / 状态枚举 / 路径与 backend api/* 实际契约严格一致(commit 7dca873 / 2a189d1 / 44e974c)。

import type {
  ChapterStatus,
  ChapterVersionDTO,
  DocumentDTO,
  DocxJobDTO,
  OutlineChapterDTO,
  OutlineResponseDTO,
  ProjectDTO,
  TokenUsageRow,
  UserDTO,
} from './types'
import type { ProjectEvent } from '@/hooks/useSSE'

export function isMockEnabled(): boolean {
  if (typeof import.meta === 'undefined') return false
  if (import.meta.env.PROD) return false
  if (import.meta.env.VITE_API_REAL === '1') return false
  return true
}

// ─────────────────────────── Fixtures ───────────────────────────

const NOW = '2026-05-02T08:00:00+08:00'
const YESTERDAY = '2026-05-01T15:30:00+08:00'

const adminUser: UserDTO = {
  id: 1,
  username: 'admin',
  role: 'admin',
  is_active: true,
  must_change_password: false,
  created_at: '2026-04-01T09:00:00+08:00',
}
const memberUser: UserDTO = {
  id: 2,
  username: 'zhangsan',
  role: 'user',
  is_active: true,
  must_change_password: false,
  created_at: '2026-04-15T10:00:00+08:00',
}
const lisi: UserDTO = {
  id: 3,
  username: 'lisi',
  role: 'user',
  is_active: true,
  must_change_password: false,
  created_at: '2026-04-20T11:00:00+08:00',
}

let currentUser: UserDTO = adminUser

const projects: ProjectDTO[] = [
  {
    id: 101,
    name: '某市政务云投标',
    description: '某市政务云一期 IaaS + PaaS 招标',
    status: 'awaiting_review',
    created_by: 2,
    api_key_owner: 2,
    dir_path: '/var/lib/bid-app/projects/101',
    pages_per_chapter: 3,
    max_retry_per_chapter: 3,
    created_at: YESTERDAY,
  },
  {
    id: 102,
    name: '智慧园区门禁系统',
    description: '门禁 + 视频监控 + 一卡通',
    status: 'done',
    created_by: 3,
    api_key_owner: 3,
    dir_path: '/var/lib/bid-app/projects/102',
    pages_per_chapter: 3,
    max_retry_per_chapter: 3,
    created_at: '2026-04-25T14:00:00+08:00',
  },
  {
    id: 103,
    name: '中医院 HIS 升级',
    description: 'HIS 系统从 2.0 升级到 4.0',
    status: 'queued',
    created_by: 1,
    api_key_owner: 1,
    dir_path: '/var/lib/bid-app/projects/103',
    pages_per_chapter: 3,
    max_retry_per_chapter: 3,
    created_at: NOW,
  },
  {
    id: 104,
    name: '高速公路监控数据中台',
    description: '边端 + 中心一体化监控数据归集',
    status: 'failed',
    created_by: 2,
    api_key_owner: 2,
    dir_path: '/var/lib/bid-app/projects/104',
    pages_per_chapter: 3,
    max_retry_per_chapter: 3,
    created_at: '2026-04-28T09:00:00+08:00',
  },
]

interface MockChapter extends OutlineChapterDTO {
  retry_count: number
  final_text: string | null
}

const chapterFinalTexts: Record<string, string> = {
  '101_3': `# 应用支撑平台

## 平台总体设计

应用支撑平台基于 **微服务 + 容器化** 架构,提供统一的应用部署、配置、监控与治理能力。

### 关键能力

- **服务注册与发现**:基于 Nacos 集群,RPS 峰值可达 50,000
- **配置中心**:多环境隔离 + 灰度发布
- **API 网关**:Kong 集群,支持限流 / 熔断 / 鉴权

\`\`\`mermaid
flowchart LR
    A[用户请求] --> B[API 网关 Kong]
    B --> C[微服务 A]
    B --> D[微服务 B]
    C --> E[(Nacos)]
    D --> E
    C --> F[(MySQL)]
    D --> G[(Redis)]
\`\`\`

| 组件 | 版本 | 部署方式 |
|---|---|---|
| Nacos | 2.3.x | 3 节点集群 |
| Kong | 3.5.x | 4 节点 LB |
| Prometheus | 2.45.x | 联邦 |
`,
}

function makeChapter(
  pid: number,
  idx: number,
  title: string,
  status: ChapterStatus,
): MockChapter {
  const finalText =
    chapterFinalTexts[`${pid}_${idx}`] ??
    (status === 'approved' ? `# ${title}\n\n(示例正文 …)` : null)
  return {
    id: `ch_${(idx + 1).toString().padStart(2, '0')}`,
    title,
    summary: '示例章节简介(LLM-1 生成)',
    key_points: ['要点 A', '要点 B', '要点 C'],
    target_pages: 3,
    index: idx,
    status,
    retry_count: status === 'failed' ? 1 : 0,
    final_text: finalText,
  }
}

const projectChapters: Record<number, MockChapter[]> = {
  101: [
    makeChapter(101, 0, '项目背景与需求理解', 'approved'),
    makeChapter(101, 1, '总体技术架构', 'approved'),
    makeChapter(101, 2, '云资源池设计', 'approved'),
    makeChapter(101, 3, '应用支撑平台', 'awaiting_review'),
    makeChapter(101, 4, '安全防护体系', 'pending'),
    makeChapter(101, 5, '运维管理体系', 'pending'),
    makeChapter(101, 6, '迁移实施方案', 'pending'),
    makeChapter(101, 7, '项目管理与质量保证', 'pending'),
    makeChapter(101, 8, '人员组织与培训', 'pending'),
    makeChapter(101, 9, '售后服务与 SLA', 'failed'),
  ],
  102: Array.from({ length: 8 }, (_, i) =>
    makeChapter(102, i, `第 ${i + 1} 章 完整方案`, 'approved'),
  ),
  103: [],
  104: [
    ...Array.from({ length: 5 }, (_, i) =>
      makeChapter(104, i, `第 ${i + 1} 章`, 'approved'),
    ),
    makeChapter(104, 5, '数据回流通道', 'failed'),
    ...Array.from({ length: 6 }, (_, i) =>
      makeChapter(104, 6 + i, `第 ${7 + i} 章`, 'pending'),
    ),
  ],
}

const projectDocuments: Record<number, DocumentDTO[]> = {
  101: [
    {
      id: 1001,
      project_id: 101,
      kind: 'tech_spec',
      original_filename: '技术需求书.docx',
      file_size: 2_456_032,
      extract_error: null,
    },
    {
      id: 1002,
      project_id: 101,
      kind: 'scoring',
      original_filename: '评分细则.docx',
      file_size: 158_320,
      extract_error: null,
    },
    {
      id: 1003,
      project_id: 101,
      kind: 'template',
      original_filename: '历史方案模板.docx',
      file_size: 4_891_120,
      extract_error: null,
    },
  ],
  102: [],
  103: [],
  104: [],
}

const docxJobs: Record<number, DocxJobDTO> = {
  102: {
    id: 9001,
    project_id: 102,
    status: 'done',
    stage: null,
    error: null,
    filename: '智慧园区门禁系统_技术方案_20260502.docx',
    created_at: NOW,
  },
}
let lastDocxJobId = 9001

const tokenUsageRows: TokenUsageRow[] = [
  {
    user_id: 2,
    username: 'zhangsan',
    total_input_tokens: 123_400,
    total_output_tokens: 89_200,
    total_cost: 4.62,
  },
  {
    user_id: 3,
    username: 'lisi',
    total_input_tokens: 56_800,
    total_output_tokens: 42_100,
    total_cost: 2.18,
  },
  {
    user_id: 1,
    username: 'admin',
    total_input_tokens: 8_100,
    total_output_tokens: 6_400,
    total_cost: 0.31,
  },
]

const adminUsers: UserDTO[] = [adminUser, memberUser, lisi]

let apiKeyConfigured = true
let apiKeyLastValidatedAt: string | null = NOW

// ─────────────────────────── Resolver ───────────────────────────

interface ResolveContext {
  path: string
  pathname: string
  query: URLSearchParams
  method: string
  body: unknown
}

const PROJECT_RE = /^\/api\/projects\/(\d+)(\/.*)?$/
const DOCX_JOB_RE = /^\/api\/projects\/(\d+)\/docx-job\/(\d+)$/
const CHAPTER_RE = /^\/api\/projects\/(\d+)\/chapters\/(\d+)(\/[^/]+)?$/

export async function mockResolve<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  const body = parseBody(init.body)
  await sleep(120)
  const [pathname, qs] = path.split('?', 2)
  const query = new URLSearchParams(qs ?? '')
  const result = route({ path, pathname, query, method, body })
  return result as T
}

function parseBody(raw: BodyInit | null | undefined): unknown {
  if (raw == null) return null
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch {
      return raw
    }
  }
  if (raw instanceof FormData) {
    const obj: Record<string, unknown> = {}
    raw.forEach((v, k) => {
      obj[k] = v instanceof File ? { _file: v.name, size: v.size } : v
    })
    return obj
  }
  return raw
}

function sleep(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms))
}

function route(ctx: ResolveContext): unknown {
  const { pathname, method } = ctx

  // ── Auth ──
  if (pathname === '/api/auth/login' && method === 'POST') {
    const b = ctx.body as { username?: string; password?: string }
    if (!b?.username || !b?.password) {
      throw apiError(400, { detail: '用户名或密码不能为空' })
    }
    if (b.password === 'wrong') {
      throw apiError(401, { detail: '用户名或密码错误' })
    }
    if (b.username === 'admin' && b.password === 'admin123') {
      currentUser = { ...adminUser, must_change_password: true }
      return currentUser
    }
    currentUser = adminUsers.find((u) => u.username === b.username) ?? adminUser
    return currentUser
  }
  if (pathname === '/api/auth/logout' && method === 'POST') return null
  if (pathname === '/api/auth/me' && method === 'GET') return currentUser
  if (pathname === '/api/me/change-password' && method === 'POST') {
    currentUser = { ...currentUser, must_change_password: false }
    return null
  }

  // ── Me / API Key ──
  if (pathname === '/api/me/api-key' && method === 'GET') {
    return {
      configured: apiKeyConfigured,
      last_validated_at: apiKeyLastValidatedAt,
    }
  }
  if (pathname === '/api/me/api-key' && method === 'PUT') {
    apiKeyConfigured = true
    apiKeyLastValidatedAt = new Date().toISOString()
    return null
  }
  if (pathname === '/api/me/api-key' && method === 'DELETE') {
    apiKeyConfigured = false
    apiKeyLastValidatedAt = null
    return null
  }
  if (pathname === '/api/me/api-key/test' && method === 'GET') {
    if (!apiKeyConfigured) throw apiError(412, { detail: '尚未配置 API Key' })
    return { ok: true }
  }
  if (pathname === '/api/me/usage' && method === 'GET') {
    const month = ctx.query.get('month') ?? '2026-05'
    return {
      month,
      total_input_tokens: 56_800,
      total_output_tokens: 42_100,
      total_cost: 2.18,
      by_model: [
        {
          model: 'qwen3.6-max-preview',
          input_tokens: 38_200,
          output_tokens: 31_100,
          cost: 1.74,
        },
        {
          model: 'qwen3.6-flash',
          input_tokens: 12_400,
          output_tokens: 7_800,
          cost: 0.28,
        },
        {
          model: 'deepseek-v4-flash',
          input_tokens: 6_200,
          output_tokens: 3_200,
          cost: 0.16,
        },
      ],
    }
  }

  // ── Admin ──
  if (pathname === '/api/admin/users' && method === 'GET') return adminUsers
  if (pathname === '/api/admin/users' && method === 'POST') {
    const b = ctx.body as {
      username: string
      password: string
      role: 'admin' | 'user'
    }
    const u: UserDTO = {
      id: adminUsers.length + 10,
      username: b.username,
      role: b.role,
      is_active: true,
      must_change_password: true,
      created_at: new Date().toISOString(),
    }
    adminUsers.push(u)
    return u
  }
  if (pathname.endsWith('/disable') && pathname.startsWith('/api/admin/users/')) {
    const id = Number(pathname.split('/')[4])
    const u = adminUsers.find((x) => x.id === id)
    if (u) u.is_active = false
    return null
  }
  if (
    pathname.endsWith('/password') &&
    pathname.startsWith('/api/admin/users/')
  ) {
    return null
  }
  if (pathname === '/api/admin/usage' && method === 'GET') {
    const month = ctx.query.get('month') ?? '2026-05'
    return {
      month,
      rows: tokenUsageRows,
      total_cost: tokenUsageRows.reduce((s, r) => s + r.total_cost, 0),
    }
  }

  // ── Projects 列表 / 创建 ──
  if (pathname === '/api/projects' && method === 'GET') return projects
  if (pathname === '/api/projects' && method === 'POST') {
    const b = ctx.body as {
      name: string
      description?: string | null
      pages_per_chapter?: number
      max_retry_per_chapter?: number
    }
    const id = projects.length ? Math.max(...projects.map((x) => x.id)) + 1 : 200
    const p: ProjectDTO = {
      id,
      name: b.name,
      description: b.description ?? null,
      status: 'init',
      created_by: currentUser.id,
      api_key_owner: null,
      dir_path: `/var/lib/bid-app/projects/${id}`,
      pages_per_chapter: b.pages_per_chapter ?? 3,
      max_retry_per_chapter: b.max_retry_per_chapter ?? 3,
      created_at: new Date().toISOString(),
    }
    projects.unshift(p)
    projectChapters[p.id] = []
    projectDocuments[p.id] = []
    return p
  }

  const docxJobMatch = pathname.match(DOCX_JOB_RE)
  if (docxJobMatch && method === 'GET') {
    const pid = Number(docxJobMatch[1])
    const jid = Number(docxJobMatch[2])
    const job = docxJobs[pid]
    if (!job || job.id !== jid) throw apiError(404, { detail: 'job not found' })
    return job
  }

  const chapterMatch = pathname.match(CHAPTER_RE)
  if (chapterMatch) {
    const pid = Number(chapterMatch[1])
    const idx = Number(chapterMatch[2])
    const sub = chapterMatch[3] ?? ''
    const list = projectChapters[pid] ?? []
    const ch = list.find((c) => c.index === idx)
    if (!ch) throw apiError(404, { detail: 'chapter not found' })

    if (sub === '/review' && method === 'POST') {
      const b = ctx.body as { decision: 'approve' | 'revise' | 'skip' }
      if (b.decision === 'approve') ch.status = 'approved'
      else if (b.decision === 'skip') ch.status = 'skipped'
      else if (b.decision === 'revise') {
        ch.status = 'generating'
        ch.retry_count += 1
      }
      return { ok: true }
    }
    if (sub === '/retry' && method === 'POST') {
      ch.status = 'pending'
      ch.retry_count = 0
      return { ok: true }
    }
    if (sub === '/versions' && method === 'GET') {
      return mockChapterVersions(ch)
    }
  }

  const projMatch = pathname.match(PROJECT_RE)
  if (projMatch) {
    const pid = Number(projMatch[1])
    const sub = projMatch[2] ?? ''
    const proj = projects.find((p) => p.id === pid)
    if (!proj) throw apiError(404, { detail: 'project not found' })

    if (sub === '' && method === 'GET') return proj
    if (sub === '' && method === 'DELETE') {
      const ix = projects.findIndex((x) => x.id === pid)
      if (ix >= 0) projects.splice(ix, 1)
      return { ok: true }
    }
    if (sub === '/start' && method === 'POST') {
      proj.status = 'extracting'
      return { run_id: pid * 10, queued: false }
    }
    if (sub === '/documents' && method === 'POST') {
      const b = ctx.body as Record<string, unknown>
      const file = b['file'] as { _file: string; size: number } | undefined
      const kind = b['kind'] as DocumentDTO['kind']
      const doc: DocumentDTO = {
        id: Math.floor(Math.random() * 100_000),
        project_id: pid,
        kind,
        original_filename: file?._file ?? 'unknown',
        file_size: file?.size ?? 0,
        extract_error: null,
      }
      projectDocuments[pid] = [...(projectDocuments[pid] ?? []), doc]
      return doc
    }
    if (sub === '/outline' && method === 'GET') {
      const chs = projectChapters[pid] ?? []
      const resp: OutlineResponseDTO = {
        project_id: pid,
        run_id: chs.length > 0 ? pid * 10 : null,
        status: proj.status,
        chapters: chs.map((c) => ({
          id: c.id,
          title: c.title,
          summary: c.summary,
          key_points: c.key_points,
          target_pages: c.target_pages,
          index: c.index,
          status: c.status,
        })),
      }
      return resp
    }
    if (sub === '/outline' && method === 'PUT') {
      const b = ctx.body as {
        chapters?: {
          title: string
          summary?: string | null
          key_points: string[]
          target_pages: number
        }[]
      }
      if (b?.chapters && b.chapters.length > 0) {
        projectChapters[pid] = b.chapters.map((c, i) => ({
          id: `ch_${(i + 1).toString().padStart(2, '0')}`,
          title: c.title,
          summary: c.summary ?? null,
          key_points: c.key_points,
          target_pages: c.target_pages,
          index: i,
          status: 'pending',
          retry_count: 0,
          final_text: null,
        }))
      }
      proj.status = 'running'
      return { ok: true }
    }
    if (sub === '/proposal' && method === 'GET') {
      const md = (projectChapters[pid] ?? [])
        .map((c) => c.final_text ?? `# ${c.title}\n\n(待生成)`)
        .join('\n\n---\n\n')
      return {
        project_id: pid,
        status: proj.status,
        markdown: md,
        chars: md.length,
      }
    }
    if (sub === '/proposal.docx' && method === 'POST') {
      const job: DocxJobDTO = {
        id: ++lastDocxJobId,
        project_id: pid,
        status: 'processing',
        stage: 'rendering_mermaid',
        error: null,
        filename: `${proj.name}_技术方案_20260502.docx`,
        created_at: new Date().toISOString(),
      }
      docxJobs[pid] = job
      window.setTimeout(() => {
        const j = docxJobs[pid]
        if (j) {
          j.status = 'done'
          j.stage = null
        }
      }, 4_000)
      return { docx_job_id: job.id, arq_job_id: 'mock-arq-job', cached: false }
    }
  }

  throw apiError(404, { detail: `mock not implemented: ${method} ${pathname}` })
}

function apiError(status: number, body: unknown) {
  const err = new Error(`mock ${status}`) as Error & {
    __mock: true
    status: number
    body: unknown
  }
  err.__mock = true
  err.status = status
  err.body = body
  return err
}

export function isMockApiError(
  err: unknown,
): err is { __mock: true; status: number; body: unknown } {
  return Boolean(
    err && typeof err === 'object' && (err as { __mock?: boolean }).__mock,
  )
}

function mockChapterVersions(ch: MockChapter): ChapterVersionDTO[] {
  if (
    ch.retry_count === 0 &&
    ch.status !== 'approved' &&
    ch.status !== 'skipped'
  ) {
    return []
  }
  return [
    {
      id: ch.index * 10 + 1,
      chapter_id: ch.index,
      version: 1,
      text: `# ${ch.title}\n\n(初版正文)`,
      feedback:
        ch.retry_count > 0 ? '请补充技术方案的可视化图表与细节' : null,
      created_at: YESTERDAY,
    },
    ...(ch.retry_count > 0
      ? [
          {
            id: ch.index * 10 + 2,
            chapter_id: ch.index,
            version: 2,
            text: ch.final_text ?? `# ${ch.title}\n\n(重写后)`,
            feedback: null,
            created_at: NOW,
          },
        ]
      : []),
  ]
}

// ─────────────────────────── SSE Mock ───────────────────────────

// 模拟流:open 后 1.2s 推 chapter_started → chapter_token × N → awaiting_review
// (含 chapter_text payload)。事件名严格对齐 backend events/bus.py。
export class MockProjectEventSource {
  private listeners: Record<string, ((e: MessageEvent) => void)[]> = {}
  private timers: number[] = []
  private closed = false
  readonly url: string
  readonly readyState = 1

  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  onopen: ((e: Event) => void) | null = null

  constructor(url: string) {
    this.url = url
    window.setTimeout(() => {
      if (this.closed) return
      this.onopen?.(new Event('open'))
      this.simulate(url)
    }, 50)
  }

  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    ;(this.listeners[type] ??= []).push(fn)
  }
  removeEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((f) => f !== fn)
  }
  close() {
    this.closed = true
    for (const t of this.timers) window.clearTimeout(t)
    this.timers = []
  }

  private dispatch(payload: ProjectEvent) {
    if (this.closed) return
    const event = new MessageEvent('message', {
      data: JSON.stringify(payload),
    })
    this.onmessage?.(event)
    for (const fn of this.listeners.message ?? []) fn(event)
  }

  private simulate(url: string) {
    const m = url.match(/\/api\/projects\/(\d+)\/stream/)
    if (!m) return
    const pid = Number(m[1])
    const proj = projects.find((p) => p.id === pid)
    if (!proj) return
    if (proj.status !== 'awaiting_review' && proj.status !== 'running') return

    const list = projectChapters[pid] ?? []
    // 拿第一个非 approved/skipped 的章节 idx 作为流目标
    const target = list.find(
      (c) =>
        c.status === 'awaiting_review' ||
        c.status === 'generating' ||
        c.status === 'pending',
    )
    if (!target) return
    const idx = target.index
    const tokens = '本章在 mock 模式下流式涌入,用于验证渲染管线。'.split('')
    let acc = 0
    this.timers.push(
      window.setTimeout(() => {
        this.dispatch({ type: 'chapter_started', chapter_index: idx })
        const tick = () => {
          if (this.closed) return
          if (acc >= tokens.length) {
            this.dispatch({
              type: 'awaiting_review',
              chapter_index: idx,
              chapter_text: target.final_text ?? tokens.join(''),
            })
            return
          }
          this.dispatch({
            type: 'chapter_token',
            chapter_index: idx,
            delta: tokens[acc++],
          })
          this.timers.push(window.setTimeout(tick, 80))
        }
        tick()
      }, 1200),
    )
  }
}
