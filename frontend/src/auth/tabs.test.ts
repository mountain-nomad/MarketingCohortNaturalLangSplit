import { ALL_PERMISSIONS } from '../test/mockApi.ts'
import { visibleTabs } from './tabs.ts'

const ids = (permissions: string[]) => visibleTabs(permissions).map((tab) => tab.id)

describe('visibleTabs (AC-A23, AC-A24)', () => {
  it('shows only Account to a user with no permissions', () => {
    expect(ids([])).toEqual(['account'])
  })

  it('shows New cohort and History for cohort.create', () => {
    expect(ids(['cohort.create'])).toEqual(['new-cohort', 'history', 'account'])
  })

  it('shows History without New cohort for cohort.read_all', () => {
    expect(ids(['cohort.read_all'])).toEqual(['history', 'account'])
  })

  it('does not show a tab for cohort.export alone', () => {
    expect(ids(['cohort.export'])).toEqual(['account'])
  })

  it('maps admin and semantic permissions to their tabs', () => {
    expect(ids(['user.read'])).toEqual(['account', 'users'])
    expect(ids(['role.read'])).toEqual(['account', 'roles'])
    expect(ids(['semantic_context.read'])).toEqual(['account', 'semantic'])
    expect(ids(['audit.read'])).toEqual(['account', 'audit'])
  })

  it('shows every tab for the full catalog', () => {
    expect(ids(ALL_PERMISSIONS)).toEqual([
      'new-cohort',
      'history',
      'account',
      'users',
      'roles',
      'semantic',
      'audit',
    ])
  })

  it('ignores unknown permission strings', () => {
    expect(ids(['admin', '*', 'user.*'])).toEqual(['account'])
  })

  it('gives every tab a label and a path', () => {
    for (const tab of visibleTabs(ALL_PERMISSIONS)) {
      expect(tab.label).toBeTruthy()
      expect(tab.path.startsWith('/')).toBe(true)
    }
  })
})
