// 3製品（RAG / NL2SQL / Agent）共通のシステム設定画面（#70）。
export {
  SYSTEM_SETTINGS_NAV_ITEMS,
  SYSTEM_SETTINGS_PATHS,
  type SystemSettingsKey,
  type SystemSettingsNavItem,
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
