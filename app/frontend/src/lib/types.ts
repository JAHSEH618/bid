// 与 backend Pydantic schemas 对齐的 TypeScript 类型(IMPLEMENTATION_SPEC §15 / REQUIREMENTS §9)

export type UserRole = 'admin' | 'user'

export interface UserDTO {
  id: number
  username: string
  role: UserRole
  is_active: boolean
  must_change_password: boolean
  created_at: string
}

// REQUIREMENTS FR-1.2:`init` → `extracting` → `outlining` → `outline_ready`
// → `running` → `awaiting_review` → ... → `done` / `failed` / `aborted`;
// 旁路 `queued`(FR-1.3 D-T 并发等位)。
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

export interface ProjectDTO {
  id: number
  name: string
  description?: string | null
  status: ProjectStatus
  current_index: number
  total_chapters: number
  created_by: number
  created_by_username?: string
  api_key_owner?: number | null
  created_at: string
  updated_at: string
}

export type ChapterStatus =
  | 'pending'
  | 'writing'
  | 'awaiting_review'
  | 'approved'
  | 'skipped'
  | 'failed'

export interface ChapterDTO {
  id: number
  project_id: number
  index: number
  title: string
  status: ChapterStatus
  retry_count: number
  final_text: string | null
  current_version_id: number | null
}

export interface ChapterVersionDTO {
  id: number
  chapter_id: number
  version: number
  text: string
  feedback: string | null
  created_at: string
}

export type DocumentKind = 'tech_spec' | 'scoring_rules' | 'reference_doc'

export interface DocumentDTO {
  id: number
  project_id: number
  filename: string
  kind: DocumentKind
  size_bytes: number
  uploaded_at: string
}

export interface OutlineChapter {
  index: number
  title: string
  description?: string
}

export interface OutlineDTO {
  chapters: OutlineChapter[]
}

export interface ProjectDetailDTO {
  project: ProjectDTO
  chapters: ChapterDTO[]
  documents: DocumentDTO[]
  outline: OutlineDTO | null
  current_index: number
}

// 对外业务状态(D-CG / D-CN):in-flight 状态(pending / rendering_mermaid /
// pandoc / finalizing)经后端映射为 `processing` 暴露;`invalidated` 显式终态,
// 表示 markdown 重生后旧 DOCX 被作废。
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
