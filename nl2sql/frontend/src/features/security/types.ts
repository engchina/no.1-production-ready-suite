export interface DataEntitlement {
  entitlement_id?: string;
  resource_code: string;
  scope_code: string;
  capability: string;
}

export interface CurrentUser {
  user_id: string;
  login_name: string;
  display_name: string;
  status: string;
  force_password_change: boolean;
  role_codes: string[];
  permissions: string[];
  data_entitlements: DataEntitlement[];
  debug_mode: boolean;
}

export interface SecurityUser {
  user_id: string;
  login_name: string;
  display_name: string;
  status: "ACTIVE" | "DISABLED";
  force_password_change: boolean;
  locked_until: string | null;
  version: number;
  role_ids: string[];
}

export interface SecurityRole {
  role_id: string;
  role_code: string;
  display_name: string;
  description: string;
  is_built_in: boolean;
  archived: boolean;
  version: number;
  permissions: string[];
  data_entitlements: DataEntitlement[];
}

export interface PermissionDefinition {
  code: string;
  group: string;
  label: string;
  description: string;
  implies: string[];
}

export interface AuditRecord {
  audit_id: number;
  actor_user_id: string | null;
  event_type: string;
  target_type: string;
  target_id: string;
  outcome: string;
  detail: Record<string, unknown>;
  request_id: string;
  client_ip: string;
  created_at: string;
}

export interface AuditPage {
  items: AuditRecord[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

export interface DeepSecStep {
  step_no: number;
  key: string;
  title: string;
  description: string;
  checksum: string;
  status: "PENDING" | "RUNNING" | "APPLIED" | "FAILED";
  error_message: string;
  executed_at: string | null;
  sql: string[];
}

export interface DeepSecPlan {
  version: string;
  driver_mode: string;
  deepsec_enabled: boolean;
  end_user: string;
  steps: DeepSecStep[];
}

export interface DeepSecStatus {
  configured: boolean;
  driver_mode: string;
  deepsec_enabled: boolean;
  end_user: string;
  objects: Record<string, number>;
  message: string;
}

export interface DeepSecVerification {
  version: string;
  passed: boolean;
  checked_at: string;
  checks: Array<{ key: string; passed: boolean; detail: string }>;
}
