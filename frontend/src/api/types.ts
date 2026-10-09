export interface RoleRef {
  id: number
  name: string
}

export interface Me {
  id: number
  email: string
  display_name: string
  is_admin: boolean
  roles: RoleRef[]
  permissions: string[]
}

export interface AdminUser {
  id: number
  email: string
  display_name: string
  is_active: boolean
  must_change_password: boolean
  roles: RoleRef[]
  created_at: string
  last_login_at: string | null
}

export interface Role {
  id: number
  name: string
  description: string
  is_system: boolean
  permissions: string[]
  export_columns: string[]
  /** Grants on columns absent from the latest crawl (inert until the column exists). */
  missing_export_columns?: string[]
  column_inventory?: 'available' | 'unavailable'
  member_ids: number[]
}

export interface PermissionDef {
  key: string
  area: string
  description: string
  grantable: boolean
}

export interface AuditEvent {
  id: number
  occurred_at: string
  actor_type: 'user' | 'cli' | 'anonymous'
  actor_user_id: number | null
  actor_email: string | null
  action: string
  target_type: string | null
  target_id: string | null
  outcome: 'success' | 'denied' | 'error'
  request_id: string | null
  metadata: Record<string, unknown>
}

export interface AuditPage {
  items: AuditEvent[]
  total: number
  limit: number
  offset: number
}

export interface ListOf<T> {
  items: T[]
}
