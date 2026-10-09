/**
 * Dashboard tabs and the permissions that reveal them (FR-A6).
 * Hiding a tab is UX only: every API endpoint enforces its own permission.
 */

export type TabId = 'new-cohort' | 'history' | 'account' | 'users' | 'roles' | 'semantic' | 'audit'

export interface Tab {
  id: TabId
  label: string
  path: string
  group: 'workspace' | 'admin'
  /** Any one of these permissions reveals the tab; empty = every signed-in user. */
  anyOf: readonly string[]
}

export const TABS: readonly Tab[] = [
  { id: 'new-cohort', label: 'New cohort', path: '/cohorts/new', group: 'workspace', anyOf: ['cohort.create'] },
  { id: 'history', label: 'History', path: '/history', group: 'workspace', anyOf: ['cohort.create', 'cohort.read_all'] },
  { id: 'account', label: 'Account', path: '/account', group: 'workspace', anyOf: [] },
  { id: 'users', label: 'Users', path: '/admin/users', group: 'admin', anyOf: ['user.read'] },
  { id: 'roles', label: 'Roles', path: '/admin/roles', group: 'admin', anyOf: ['role.read'] },
  { id: 'semantic', label: 'Semantic context', path: '/semantic', group: 'admin', anyOf: ['semantic_context.read'] },
  { id: 'audit', label: 'Audit', path: '/admin/audit', group: 'admin', anyOf: ['audit.read'] },
]

export function canSee(tab: Tab, permissions: readonly string[]): boolean {
  return tab.anyOf.length === 0 || tab.anyOf.some((permission) => permissions.includes(permission))
}

export function visibleTabs(permissions: readonly string[]): Tab[] {
  return TABS.filter((tab) => canSee(tab, permissions))
}

export function tabById(id: TabId): Tab {
  const tab = TABS.find((candidate) => candidate.id === id)
  if (!tab) throw new Error(`unknown tab ${id}`)
  return tab
}
