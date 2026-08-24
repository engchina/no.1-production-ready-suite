export type DataEntitlementScopeValueType = "TEXT" | "NUMBER" | "TEMPORAL";
export type DataEntitlementScopeValueSource = "LITERAL" | "LOGIN_USER_ID";
export type DataEntitlementScopeOperator =
  | "EQ"
  | "NE"
  | "CONTAINS"
  | "STARTS_WITH"
  | "IN"
  | "GT"
  | "GTE"
  | "LT"
  | "LTE"
  | "BETWEEN"
  | "BEFORE"
  | "ON_OR_BEFORE"
  | "AFTER"
  | "ON_OR_AFTER"
  | "IS_NULL"
  | "IS_NOT_NULL";

export interface DataEntitlementScopeFilter {
  column_name: string;
  operator: DataEntitlementScopeOperator | string;
  value_type: DataEntitlementScopeValueType | string;
  value_source?: DataEntitlementScopeValueSource | string;
  value?: string;
  value_to?: string;
  values?: string[];
}

export interface DataEntitlement {
  entitlement_id?: string;
  resource_code: string;
  scope_code: string;
  capability: string;
  target_owner?: string;
  target_object?: string;
  target_type?: "TABLE" | "VIEW" | "MATERIALIZED VIEW" | string;
  column_names?: string[];
  scope_mode?: "ALL" | "COLUMN_EQUALS" | "FILTERS" | string;
  scope_column?: string;
  scope_filters?: DataEntitlementScopeFilter[];
  data_grant_name?: string;
  sql_checksum?: string;
  apply_status?: "PENDING" | "RUNNING" | "APPLIED" | "FAILED" | string;
  apply_error_message?: string;
  applied_at?: string | null;
  sql?: string[];
  checksum?: string;
}

export interface DeepSecTargetObject {
  name: string;
  owner: string;
  qualified_name?: string;
  object_type: string;
  comment: string;
}

export interface DeepSecTargetObjectPage {
  items: DeepSecTargetObject[];
  total: number;
  table_count?: number;
  view_count?: number;
  counts_included?: boolean;
  next_cursor: string | null;
  warnings?: string[];
}

export interface DeepSecTargetColumn {
  column_name: string;
  logical_name: string;
  data_type: string;
  nullable: boolean;
  comment: string;
  sample_values?: string[];
}

export interface DeepSecTargetObjectDetail extends DeepSecTargetObject {
  columns: DeepSecTargetColumn[];
  warnings?: string[];
}

export interface CurrentUser {
  user_uuid: string;
  login_user_id: string;
  display_name: string;
  status: string;
  force_password_change: boolean;
  role_codes: string[];
  is_system_admin: boolean;
  permissions: string[];
  data_entitlements: DataEntitlement[];
  debug_mode: boolean;
  password_change_allowed: boolean;
}

export interface SecurityUser {
  user_uuid: string;
  login_user_id: string;
  display_name: string;
  status: "ACTIVE" | "DISABLED";
  force_password_change: boolean;
  locked_until: string | null;
  version: number;
  role_ids: string[];
  assigned_roles: AssignedRole[];
  is_bootstrap_admin: boolean;
}

export interface AssignedRole {
  role_id: string;
  role_code: string;
  display_name: string;
  is_built_in: boolean;
  archived: boolean;
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

export interface DeepSecRoleEntitlements {
  role_id: string;
  role_code: string;
  display_name: string;
  description: string;
  is_built_in: boolean;
  archived: boolean;
  version: number;
  data_entitlements: DataEntitlement[];
}

export interface DeepSecDataEntitlementPreview {
  role_id: string;
  data_entitlements: DataEntitlement[];
}

export interface PermissionDefinition {
  code: string;
  group: string;
  label: string;
  description: string;
  implies: string[];
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
  connection_security?: string;
  deepsec_enabled: boolean;
  data_user: string;
  has_data_user_password: boolean;
  steps: DeepSecStep[];
}

export interface DeepSecStatus {
  configured: boolean;
  driver_mode: string;
  connection_security?: string;
  deepsec_enabled: boolean;
  data_user: string;
  has_data_user_password: boolean;
  objects: Record<string, number>;
  message: string;
}

export interface DeepSecVerification {
  version: string;
  passed: boolean;
  checked_at: string;
  checks: Array<{ key: string; passed: boolean; detail: string }>;
}
