/** Download refusals that concern only the extra columns: the canonical user ids can still be exported (ruling E11). */
export const ID_ONLY_RETRY: ReadonlySet<string> = new Set([
  'export_column_denied',
  'export_column_unsupported',
  'canonical_user_id_changed',
])
