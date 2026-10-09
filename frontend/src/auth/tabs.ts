export type TabId = 'new-cohort' | 'history' | 'account' | 'users' | 'roles' | 'semantic' | 'audit'

export interface Tab {
  id: TabId
  label: string
  path: string
}

export function visibleTabs(_permissions: readonly string[]): Tab[] {
  throw new Error('not implemented')
}
