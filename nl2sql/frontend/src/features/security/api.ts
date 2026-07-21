import { apiFetch, apiGet, apiPatch, apiPost } from "@/lib/api";

import type {
  AuditPage,
  AuditRecord,
  CurrentUser,
  DataEntitlement,
  DeepSecPlan,
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
  me: () => apiGet<CurrentUser>("/api/auth/me"),
  logout: () => apiPost<{ logged_out: boolean }>("/api/auth/logout"),
  changePassword: (currentPassword: string, newPassword: string) =>
    apiPost<{ changed: boolean }>("/api/auth/password/change", {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  users: () => apiGet<SecurityUser[]>("/api/security/users"),
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
  roles: (includeArchived = false) =>
    apiGet<SecurityRole[]>(`/api/security/roles?include_archived=${String(includeArchived)}`),
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
  permissions: () => apiGet<PermissionDefinition[]>("/api/security/permissions"),
  audit: () => apiGet<AuditRecord[]>("/api/security/audit"),
  auditPage: (page = 1, pageSize = 10) =>
    apiGet<AuditPage>(`/api/security/audit/page?page=${page}&page_size=${pageSize}`),
  exportAudit: () =>
    apiFetch("/api/security/audit/export.xlsx", {
      headers: {
        Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      },
    }),
  deepSecStatus: () => apiGet<DeepSecStatus>("/api/security/deepsec/status"),
  deepSecPlan: () => apiGet<DeepSecPlan>("/api/security/deepsec/plan"),
  applyDeepSecStep: (version: string, step: DeepSecStep) =>
    apiPost<{ version: string; step_no: number; status: string }>(`/api/security/deepsec/plan/${version}/steps/${step.step_no}/apply`, {
      checksum: step.checksum,
    }),
  verifyDeepSec: () => apiPost<DeepSecVerification>("/api/security/deepsec/verify"),
};
