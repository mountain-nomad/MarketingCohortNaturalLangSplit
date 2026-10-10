/** Shapes returned by the semantic-context and crawler APIs (backend cohortsplit.semantic.api). */

export interface UserRef {
  id: number
  display_name: string
}

export interface DocColumn {
  name: string
  data_type: string
  nullable: boolean
  comment: string | null
  allowed_values: string[] | null
  sample_values: string[]
}

export interface DocForeignKey {
  columns: string[]
  referred_table: string
  referred_columns: string[]
}

export interface DocTable {
  qualified_name: string
  kind: string
  comment: string | null
  primary_key: string[]
  foreign_keys: DocForeignKey[]
  estimated_row_count: number | null
  columns: DocColumn[]
}

export interface CrawlRun {
  id: number
  status: 'running' | 'succeeded' | 'failed'
  started_at: string
  finished_at: string | null
  triggered_by: string | null
  content_hash: string | null
  summary: Record<string, unknown>
  error: string | null
  semantic_version?: string
}

export interface GeneratedDocs {
  tables: DocTable[]
  latest_run: CrawlRun | null
}

export interface ReferenceProblem {
  reference: string
  problem: string
}

export type EntryKind =
  | 'term'
  | 'metric'
  | 'status_semantics'
  | 'time_window'
  | 'exclusion'
  | 'canonical_user_id'

export interface BusinessContextEntry {
  key: string
  kind: EntryKind
  synonyms: string[]
  description: string
  definition: Record<string, unknown>
  missing_references: ReferenceProblem[]
  created_at: string
  updated_at: string
  created_by: UserRef | null
  updated_by: UserRef | null
}

export type UseCaseStatus = 'pending_review' | 'confirmed' | 'rejected' | 'needs_rereview'

export interface UseCase {
  id: number
  key: string
  origin: 'generated' | 'human'
  status: UseCaseStatus
  /** Set when today's sampling policy forbids a literal of this generated use case: its
   * request and spec are then withheld (null) and it cannot be confirmed. */
  withheld?: string | null
  nl_request: string | null
  spec: Record<string, unknown> | null
  spec_version: string
  template_key: string | null
  rewritten_from: string | null
  generation_note: string | null
  review_note: string | null
  referenced_columns: string[]
  reviewed_by: UserRef | null
  reviewed_at: string | null
  updated_at: string
}

export interface SemanticVersion {
  version: string
  components: Record<string, string>
}

export interface Permissions {
  canEdit: boolean
  canReview: boolean
  canCrawl: boolean
}
