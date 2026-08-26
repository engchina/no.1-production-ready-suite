import { apiGet, apiPatch, apiPost, type ApiRequestOptions } from "@/lib/api";

import type {
  CurrentUser,
  DataEntitlement,
  DeepSecDataEntitlementPreview,
  DeepSecPlan,
  DeepSecRoleEntitlements,
  DeepSecStep,
  DeepSecStatus,
  DeepSecTargetObject,
  DeepSecTargetObjectDetail,
  DeepSecTargetObjectPage,
  DeepSecVerification,
  PermissionDefinition,
  ProfileAccessProfile,
  SecurityRole,
  SecurityUser,
} from "./types";

export interface UserDraft {
  login_user_id: string;
  display_name: string;
  role_ids: string[];
  temporary_password?: string;
}

export interface RoleDraft {
  role_code: string;
  display_name: string;
  description: string;
  permissions: string[];
  data_entitlements: DataEntitlement[];
  allowed_profile_ids?: string[];
}

function dataEntitlementPayload(role: Pick<DeepSecRoleEntitlements, "data_entitlements">) {
  return role.data_entitlements.map(
    ({
      entitlement_id,
      resource_code,
      scope_code,
      capability,
      target_owner,
      target_object,
      target_type,
      column_names,
      scope_mode,
      scope_column,
      scope_filters,
    }) => ({
      ...(entitlement_id ? { entitlement_id } : {}),
      resource_code,
      scope_code,
      capability,
      target_owner,
      target_object,
      target_type,
      column_names,
      scope_mode,
      scope_column,
      scope_filters: scope_filters ?? [],
    })
  );
}

export interface DeepSecTargetObjectsQuery extends ApiRequestOptions {
  q?: string;
  owner?: string;
  cursor?: string | null;
  limit?: number;
}

export const securityApi = {
  login: (loginUserId: string, password: string) =>
    apiPost<CurrentUser>("/api/auth/login", { login_user_id: loginUserId, password }),
  me: (options: ApiRequestOptions = {}) => apiGet<CurrentUser>("/api/auth/me", options),
  logout: () => apiPost<{ logged_out: boolean }>("/api/auth/logout"),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiPost<{ changed: boolean }>("/api/auth/password/change", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  users: (options: ApiRequestOptions = {}) =>
    apiGet<SecurityUser[]>("/api/security/users", options),
  createUser: (draft: UserDraft) =>
    apiPost<{ user: SecurityUser; temporary_password: string }>("/api/security/users", draft),
  updateUser: (user: SecurityUser) =>
    apiPatch<SecurityUser>(`/api/security/users/${user.user_uuid}`, {
      version: user.version,
      display_name: user.display_name,
      status: user.status,
      role_ids: user.role_ids,
    }),
  resetPassword: (userUuid: string, temporaryPassword?: string) =>
    apiPost<{ user: SecurityUser; temporary_password: string }>(
      `/api/security/users/${userUuid}/reset-password`,
      { temporary_password: temporaryPassword || null }
    ),
  unlockUser: (userUuid: string) =>
    apiPost<SecurityUser>(`/api/security/users/${userUuid}/unlock`),
  setUserEnabled: (user: SecurityUser, enabled: boolean) =>
    apiPost<SecurityUser>(
      `/api/security/users/${user.user_uuid}/${enabled ? "enable" : "disable"}`,
      { version: user.version }
    ),
  roles: (includeArchived = false, options: ApiRequestOptions = {}) =>
    apiGet<SecurityRole[]>(
      `/api/security/roles?include_archived=${String(includeArchived)}`,
      options
    ),
  createRole: (draft: RoleDraft) => apiPost<SecurityRole>("/api/security/roles", draft),
  updateRole: (role: SecurityRole) =>
    apiPatch<SecurityRole>(`/api/security/roles/${role.role_id}`, {
      version: role.version,
      display_name: role.display_name,
      description: role.description,
      permissions: role.permissions,
      data_entitlements: role.data_entitlements,
      allowed_profile_ids: role.allowed_profile_ids,
    }),
  archiveRole: (role: SecurityRole) =>
    apiPost<SecurityRole>(`/api/security/roles/${role.role_id}/archive`, {
      version: role.version,
    }),
  restoreRole: (role: SecurityRole) =>
    apiPost<SecurityRole>(`/api/security/roles/${role.role_id}/restore`, {
      version: role.version,
    }),
  permissions: (options: ApiRequestOptions = {}) =>
    apiGet<PermissionDefinition[]>("/api/security/permissions", options),
  profileAccessProfiles: (options: ApiRequestOptions = {}) =>
    apiGet<ProfileAccessProfile[]>("/api/security/profile-access/profiles", options),
  deepSecStatus: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecStatus>("/api/security/deepsec/status", options),
  deepSecPlan: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecPlan>("/api/security/deepsec/plan", options),
  deepSecDataEntitlements: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecRoleEntitlements[]>("/api/security/deepsec/data-entitlements", options),
  deepSecTargetObjects: ({
    q = "",
    owner = "",
    cursor = null,
    limit = 50,
    ...options
  }: DeepSecTargetObjectsQuery = {}) => {
    const params = new URLSearchParams({
      limit: String(limit),
      type: "all",
      row_state: "all",
      include_counts: "false",
    });
    if (q.trim()) params.set("q", q.trim());
    if (owner.trim()) params.set("owner", owner.trim());
    if (cursor) params.set("cursor", cursor);
    return apiGet<DeepSecTargetObjectPage>(
      `/api/nl2sql/db-admin/objects?${params.toString()}`,
      options
    );
  },
  deepSecTargetObjectDetail: (object: DeepSecTargetObject, options: ApiRequestOptions = {}) => {
    const collectionPath = object.object_type.toUpperCase().includes("VIEW")
      ? "/api/nl2sql/db-admin/views"
      : "/api/nl2sql/db-admin/tables";
    const params = new URLSearchParams({ include_ddl: "0", owner: object.owner });
    return apiGet<DeepSecTargetObjectDetail>(
      `${collectionPath}/${encodeURIComponent(object.name)}?${params.toString()}`,
      options
    );
  },
  updateDeepSecDataEntitlements: (role: DeepSecRoleEntitlements) =>
    apiPatch<DeepSecRoleEntitlements>(
      `/api/security/deepsec/data-entitlements/${role.role_id}`,
      {
        version: role.version,
        data_entitlements: dataEntitlementPayload(role),
      }
    ),
  previewDeepSecDataEntitlements: (
    roleId: string,
    dataEntitlements: DataEntitlement[]
  ) =>
    apiPost<DeepSecDataEntitlementPreview>(
      `/api/security/deepsec/data-entitlements/${roleId}/preview`,
      {
        data_entitlements: dataEntitlementPayload({ data_entitlements: dataEntitlements }),
      }
    ),
  applyDeepSecDataEntitlements: (
    roleId: string,
    confirmation: string,
    entitlementIds: string[] = []
  ) =>
    apiPost<{ role_id: string; status: string; entitlement_ids: string[] }>(
      `/api/security/deepsec/data-entitlements/${roleId}/apply`,
      {
        confirmation,
        entitlement_ids: entitlementIds,
      }
    ),
  updateDeepSecConfig: (dataUserPassword: string) =>
    apiPatch<DeepSecStatus>("/api/security/deepsec/config", {
      data_user_password: dataUserPassword,
    }),
  applyDeepSecStep: (version: string, step: DeepSecStep, confirmation: string) =>
    apiPost<{ version: string; step_no: number; status: string }>(
      `/api/security/deepsec/plan/${version}/steps/${step.step_no}/apply`,
      {
        checksum: step.checksum,
        confirmation,
      }
    ),
  resetDeepSecPlan: (version: string, confirmation: string) =>
    apiPost<{ version: string; status: string; step_numbers: number[] }>(
      `/api/security/deepsec/plan/${version}/reset`,
      { confirmation }
    ),
  verifyDeepSec: () => apiPost<DeepSecVerification>("/api/security/deepsec/verify"),
};
