import { apiGet, apiPatch, apiPost, type ApiRequestOptions } from "@/lib/api";

import type {
  CurrentUser,
  DataEntitlement,
  DeepSecPlan,
  DeepSecRoleEntitlements,
  DeepSecStep,
  DeepSecStatus,
  DeepSecVerification,
  PermissionDefinition,
  SecurityRole,
  SecurityUser,
} from "./types";

export interface UserDraft {
  login_name: string;
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
}

export const securityApi = {
  login: (loginName: string, password: string) =>
    apiPost<CurrentUser>("/api/auth/login", { login_name: loginName, password }),
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
    apiPatch<SecurityUser>(`/api/security/users/${user.user_id}`, {
      version: user.version,
      display_name: user.display_name,
      status: user.status,
      role_ids: user.role_ids,
    }),
  resetPassword: (userId: string, temporaryPassword?: string) =>
    apiPost<{ user: SecurityUser; temporary_password: string }>(
      `/api/security/users/${userId}/reset-password`,
      { temporary_password: temporaryPassword || null }
    ),
  unlockUser: (userId: string) =>
    apiPost<SecurityUser>(`/api/security/users/${userId}/unlock`),
  setUserEnabled: (user: SecurityUser, enabled: boolean) =>
    apiPost<SecurityUser>(
      `/api/security/users/${user.user_id}/${enabled ? "enable" : "disable"}`,
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
  deepSecStatus: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecStatus>("/api/security/deepsec/status", options),
  deepSecPlan: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecPlan>("/api/security/deepsec/plan", options),
  deepSecDataEntitlements: (options: ApiRequestOptions = {}) =>
    apiGet<DeepSecRoleEntitlements[]>("/api/security/deepsec/data-entitlements", options),
  updateDeepSecDataEntitlements: (role: DeepSecRoleEntitlements) =>
    apiPatch<DeepSecRoleEntitlements>(
      `/api/security/deepsec/data-entitlements/${role.role_id}`,
      {
        version: role.version,
        data_entitlements: role.data_entitlements.map(({ resource_code, scope_code, capability }) => ({
          resource_code,
          scope_code,
          capability,
        })),
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
  verifyDeepSec: () => apiPost<DeepSecVerification>("/api/security/deepsec/verify"),
};
