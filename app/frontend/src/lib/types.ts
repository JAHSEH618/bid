// 与 backend Pydantic schemas / SQLAlchemy 模型对齐的 TypeScript 类型。
// 字段名与 IMPLEMENTATION_SPEC §15 + 后端 schemas/projects.py / schemas/chapters.py 一致。

export type UserRole = 'admin' | 'user'

export interface UserDTO {
  id: number
  username: string
  role: UserRole
  is_active: boolean
  must_change_password: boolean
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
export interface OutlineChapterDTO {
  id: string
  title: string
  summary: string | null
  key_points: string[]
  target_pages: number
  index: number
  status: ChapterStatus
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

// 章节版本(M2/REVIEW-2 暂未提供端点,前端在 mock 兼容,真 API 上线后再接)。
export interface ChapterVersionDTO {
  id: number
  chapter_id: number
  version: number
  text: string
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

export interface DocxJobDTO {
  id: number
  project_id: number
  status: DocxJobStatus
  stage?: string | null
  error?: string | null
  filename?: string | null
  created_at: string
}

export interface TokenUsageRow {
  user_id: number
  username: string
  total_input_tokens: number
  total_output_tokens: number
  total_cost: number
}

export type ReviewDecision = 'approve' | 'revise' | 'skip'
