/** Shapes of /api/cohorts/interpret and /api/cohorts/preview. */

export interface Interpretation {
  kind: 'interpretation'
  interpretation_id: string
  readable: string
  spec: Record<string, unknown>
  spec_hash: string
  semantic_version: string
  expires_at: string
}

export interface UnknownValue {
  column: string
  value: string
  suggestions: string[]
}

export interface Clarification {
  kind: 'clarification'
  question: string
  unknown_terms: string[]
  unknown_values: UnknownValue[]
  semantic_version: string
}

export type RejectionReason = 'mutation' | 'analytics' | 'unknown_term' | 'unsupported_construct'

export interface Rejection {
  kind: 'rejection'
  reason: RejectionReason
  message: string
  semantic_version: string
}

export type InterpretResult = Interpretation | Clarification | Rejection

export type Cell = string | number | boolean | null

export interface Preview {
  prepared_cohort_id: string | null
  expires_at: string | null
  total_count: number
  empty: boolean
  split_export_enabled: boolean
  disabled_reason: string | null
  sample: { columns: string[]; rows: Cell[][]; limit: number }
  null_counts: Record<string, number>
  readable: string
  sql: string
  sql_params: Record<string, Cell>
  as_of: string
  sql_hash: string
  spec_hash: string
  semantic_version: string
  row_cap: number
}
