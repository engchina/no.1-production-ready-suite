// 3製品（RAG / NL2SQL / Agent）共通のシステム設定画面（#70）。
export {
  SYSTEM_SETTINGS_NAV_ITEMS,
  SYSTEM_SETTINGS_PATHS,
  USER_ROLE_NAV_ITEMS,
  USER_ROLE_PATHS,
  type SystemSettingsKey,
  type SystemSettingsNavItem,
  type UserRoleKey,
  type UserRoleNavItem,
} from "./paths";

// 外観（#95）
export { AppearanceSettingsPage, type AppearanceSettingsPageProps } from "./appearance/AppearanceSettingsPage";
export { APPEARANCE_MESSAGES, type AppearanceMessages } from "./appearance/messages";

// 未保存変更の離脱ガード（NL2SQL から移設。#97）
export { useUnsavedChangesGuard } from "./guards/useUnsavedChangesGuard";
export {
  DRAFT_GUARD_MESSAGES,
  useSettingsDraftGuard,
  type DraftGuardMessages,
} from "./guards/useSettingsDraftGuard";

// アップロード保存先（#97）
export {
  DEFAULT_OCI_REGION_OPTIONS,
  UploadStorageSettingsPage,
  validateUploadStorageForm,
  type UploadStorageSettingsPageProps,
} from "./upload-storage/UploadStorageSettingsPage";
export { UPLOAD_STORAGE_MESSAGES, type UploadStorageMessages } from "./upload-storage/messages";
export {
  UPLOAD_STORAGE_QUERY_KEY,
  type UploadStorageApi,
  type UploadStorageBackend,
  type UploadStorageSettingsData,
  type UploadStorageSettingsUpdate,
} from "./upload-storage/types";

// OCI 認証（#100）
export { OciSettingsPage, type OciSettingsPageProps } from "./oci/OciSettingsPage";
export { OCI_MESSAGES, type OciMessageKey } from "./oci/messages";
export type {
  OciConfigField,
  OciConfigReadData,
  OciConfigReadRequest,
  OciConfigTestResult,
  OciConfigTestStage,
  OciConfigTestStageKey,
  OciConfigTestStageStatus,
  OciConfigTestStatus,
  OciObjectStorageNamespaceData,
  OciObjectStorageNamespaceRequest,
  OciObjectStorageSettingsUpdate,
  OciPrivateKeyUploadData,
  OciSettingsApi,
  OciSettingsData,
  OciSettingsUpdate,
} from "./oci/types";
export {
  DEFAULT_OCI_SETTINGS,
  normalizeOciSettingsDraft,
  validateOciSettingsDraft,
  type OciSettingsDraft,
  type OciSettingsField,
  type OciValidationCode,
  type OciValidationResult,
} from "./oci/ociSettings";

// モデル設定（#103）
export { ModelSettingsPage, type ModelSettingsPageProps } from "./model/ModelSettingsPage";
export { MODEL_MESSAGES, type ModelMessageKey } from "./model/messages";
export {
  MODEL_SETTINGS_QUERY_KEY,
  type EnterpriseAiConfiguredModel,
  type EnterpriseAiModelSettings,
  type EnterpriseAiVlmInputMode,
  type GenerativeAiModelSettings,
  type ModelSettingsApi,
  type ModelSettingsData,
  type ModelSettingsPayload,
  type ModelSettingsSecretSource,
  type ModelSettingsTestRequest,
  type ModelSettingsTestResult,
  type ModelSettingsTestStatus,
  type ModelSettingsTestTargetType,
} from "./model/types";

// データベース設定（#108）
export { DatabaseSettingsPage, type DatabaseSettingsPageProps } from "./database/DatabaseSettingsPage";
export { DATABASE_MESSAGES, type DatabaseMessageKey } from "./database/messages";
export type { DatabaseChangedHandler } from "./database/hooks";
export {
  ADB_INFO_QUERY_KEY,
  DATABASE_SETTINGS_QUERY_KEY,
  type AdbInfoData,
  type AdbOperationStatus,
  type AdbSettingsUpdate,
  type DatabaseConnectionSecurity,
  type DatabaseConnectionTestResult,
  type DatabaseConnectionTestStatus,
  type DatabasePasswordRevealData,
  type DatabaseSettingsApi,
  type DatabaseSettingsData,
  type DatabaseSettingsUpdate,
  type DatabaseWalletDownloadData,
} from "./database/types";

// ユーザー管理・ロール管理（NL2SQL から移設。#206）
export { UserManagementPage, type UserManagementPageProps } from "./users-roles/UserManagementPage";
export {
  RoleManagementPage,
  RoleStatusBadges,
  type RoleManagementPageProps,
} from "./users-roles/RoleManagementPage";
export { USERS_ROLES_MESSAGES, type UsersRolesMessageKey } from "./users-roles/messages";
export { FormActionBar, entityActionToFormAction } from "./users-roles/FormActionBar";
export {
  SECURITY_LIST_FOCUS_CLASS,
  SECURITY_LIST_SCROLL_CLASS,
  SECURITY_TABLE_ROW_CLASS,
  SECURITY_TABLE_VISIBLE_ROWS,
  SecurityDetailField,
  SecurityEmptySelection,
  SecurityIdentityLines,
  SecurityManagementPanelShell,
  SecurityManagementStatusBar,
  SecurityPanelHeader,
  SecuritySearchField,
  identityInlineLabel,
  identitySecondaryName,
  securityFilteredCount,
  type SecurityManagementMetric,
} from "./users-roles/shared";
export {
  SYSTEM_ADMIN_ROLE_CODE,
  type ApiErrorDetails,
  type ApiFieldProblem,
  type AssignedRole,
  type DescribeApiError,
  type RoleDraft,
  type RoleManagementApi,
  type SecurityRole,
  type SecurityUser,
  type UserDraft,
  type UserManagementApi,
  type UserWithTemporaryPassword,
} from "./users-roles/types";
