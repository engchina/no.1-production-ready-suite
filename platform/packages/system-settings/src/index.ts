// 3製品（RAG / NL2SQL / Agent）共通のシステム設定画面（#70）。
export { SYSTEM_SETTINGS_PATHS } from "./paths";

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
