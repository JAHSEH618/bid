// 与 backend Pydantic schemas / SQLAlchemy 模型对齐的 TypeScript 类型。
// 字段名与 IMPLEMENTATION_SPEC §15 + 后端 schemas/projects.py / schemas/chapters.py 一致。

export type UserRole = 'admin' | 'user'

// 后端 MeResponse(schemas/auth.py),从 auth/me 端点返回。
// admin 端 AdminUserResponse 字段相同,合用同一类型即可。
export interface UserDTO {
  id: number
  username: string
  role: UserRole
  is_active: boolean
  must_change_password: boolean
  last_login_at: string | null
  created_at: string
}

// REQUIREMENTS FR-1.2 + 后端 models/project.py:
//   init → extracting → outlining → outline_ready → running → awaiting_review
//   → ... → done / failed / aborted;旁路 queued(FR-1.3 D-T)。
export type ProjectStatus =
  | 'init'
  | 'queued'
  | 'extracting'
  | 'outlining'
  | 'outline_ready'
  | 'running'
  | 'awaiting_review'
  | 'done'
  | 'failed'
  | 'aborted'

// 与后端 ProjectResponse(schemas/projects.py)对齐。
//   - 没有 current_index / total_chapters(由前端从 /outline 派生)
//   - 没有 updated_at(只有 created_at)
export interface ProjectDTO {
  id: number
  name: string
  description: string | null
  status: ProjectStatus
  created_by: number
  api_key_owner: number | null
  dir_path: string
  pages_per_chapter: number
  max_retry_per_chapter: number
  created_at: string
}

// 后端 models/chapter.py:
//   pending | generating | awaiting_review | reviewing | approved | skipped | failed | retrying
// reviewing / retrying 是中间态(FR-4.7),前端按钮按此禁用。
export type ChapterStatus =
  | 'pending'
  | 'generating'
  | 'awaiting_review'
  | 'reviewing'
  | 'approved'
  | 'skipped'
  | 'failed'
  | 'retrying'

// /outline 返回的章节(陈述 ID 是字符串 'ch_01' 形式;index 是数字)。
// R-15:final_text 是 backend models/chapter.py:Chapter.final_text 的快照,
// generating 期间 partial(R-14 周期 flush 1s/100chunks),
// awaiting_review/approved/skipped 时是完整版。
// 字段可能不在 OutlineResponse 默认 chapter dict 中(取决于 backend 版本),
// 故标 optional,前端按 ?? '' 处理。
export interface OutlineChapterDTO {
  id: string
  title: string
  summary: string | null
  key_points: string[]
  target_pages: number
  index: number
  status: ChapterStatus
  final_text?: string | null
}

// /outline 整体响应。
export interface OutlineResponseDTO {
  project_id: number
  run_id: number | null
  status: ProjectStatus
  chapters: OutlineChapterDTO[]
}

// /outline PUT body 中的章节(用户编辑后)。
export interface OutlineChapterIn {
  id?: string | null
  title: string
  summary?: string | null
  key_points: string[]
  target_pages: number
  matched_scoring_items?: string[]
}

// 文档 kind 与后端 _VALID_DOC_KINDS 完全一致。
export type DocumentKind = 'tech_spec' | 'scoring' | 'template'

// 后端 DocumentUploadResponse;list 端点暂未提供(等 backend 补,见 ListDocs 注释)。
export interface DocumentDTO {
  id: number
  project_id: number
  kind: DocumentKind
  original_filename: string
  file_size: number
  extract_error: string | null
}

// /proposal 返回。
export interface ProposalResponseDTO {
  project_id: number
  status: ProjectStatus
  markdown: string
  chars: number
}

// /start 返回。
export interface StartResponseDTO {
  run_id: number
  queued: boolean
}

// 章节版本(后端尚未提供端点,前端在 mock 兼容,真 API 上线后再接)。
// 字段名对齐 backend models/chapter_version.py:
//   - body_markdown(主名,与 SQLAlchemy 列同名)
//   - 旧别名 `text` 已移除;消费侧用 body_markdown。
export interface ChapterVersionDTO {
  id: number
  chapter_id: number
  version: number
  body_markdown: string
  feedback: string | null
  created_at: string
}

// DOCX 状态:in-flight 后端映射为 processing,invalidated 由 assemble 标记(D-CG)。
// 端点未在 M3 上线前暂留前端类型不变。
export type DocxJobStatus =
  | 'pending'
  | 'processing'
  | 'done'
  | 'failed'
  | 'invalidated'

// 后端 GET /api/projects/{id}/docx-job/{docx_job_id} 实际返回(D-BU 不暴露 finalizing,
// 内部状态映射为 processing)。
export interface DocxJobDTO {
  docx_job_id: number
  status: DocxJobStatus
  stage: string  // 进度文案;invalidated 时为「原文档已更新,请重新生成 DOCX」
  error: string | null
  created_at: string
  updated_at: string | null
  finished_at: string | null
}

export interface TriggerDocxResponse {
  docx_job_id: number
  arq_job_id: string | null
  cached: boolean
}

// 后端 ApiKeyInfoResponse(schemas/auth.py)。
// GET /api/me/api-key 已配置 → 200 + 此结构;未配置 → 404。
export interface ApiKeyInfoDTO {
  provider: string
  masked: string
  last_validated_at: string | null
  created_at: string
  updated_at: string | null
}

// 自己的 token usage(后端 TokenUsageSummary)。period ∈ {month, all}。
export interface MyTokenUsageRow {
  model: string
  prompt_tokens: number
  completion_tokens: number
}

export interface MyTokenUsageDTO {
  user_id: number
  period: string
  rows: MyTokenUsageRow[]
  total_prompt: number
  total_completion: number
}

// admin 全局 token usage(后端 AdminTokenUsageSummary)。多了 user_id / username。
export interface AdminTokenUsageRow {
  user_id: number
  username: string
  model: string
  prompt_tokens: number
  completion_tokens: number
}

export interface AdminTokenUsageDTO {
  period: string
  rows: AdminTokenUsageRow[]
  total_prompt: number
  total_completion: number
}

export type ReviewDecision = 'approve' | 'revise' | 'skip'
