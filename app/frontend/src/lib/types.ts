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

export type ProjectStatus =
  | 'draft'
  | 'extracting'
  | 'outlining'
  | 'awaiting_outline_review'
  | 'writing'
  | 'awaiting_chapter_review'
  | 'assembling'
  | 'completed'
  | 'failed'
  | 'paused'
  | 'canceled'

export interface ProjectDTO {
  id: number
  name: string
  status: ProjectStatus
  current_index: number
  total_chapters: number
  owner_id: number
  owner_username?: string
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

export type DocxJobStatus =
  | 'pending'
  | 'processing'
  | 'done'
  | 'failed'

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
