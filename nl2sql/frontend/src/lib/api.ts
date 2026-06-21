export interface ApiEnvelope<T> {
  data: T;
  error?: string;
  request_id?: string;
}

async function parseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiEnvelope<T>;
  if (!response.ok) {
    const message =
      payload.error ||
      (typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : "API リクエストに失敗しました");
    throw new Error(message);
  }
  return payload.data;
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  return parseJson<T>(response);
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parseJson<T>(response);
}

export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "PATCH",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return parseJson<T>(response);
}

export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type ModelSettingsCheckStatus = "ok" | "missing" | "invalid";
export type ModelSettingsTestStatus = "success" | "failed";
export type ModelSettingsTestTargetType =
  | "enterprise_text"
  | "enterprise_vision"
  | "embedding"
  | "rerank";
export type UploadStorageBackend = "local" | "oci";
export type DatabaseConnectionTestStatus = "success" | "failed" | "skipped";
export type OciConfigTestStatus = "success" | "failed";

export interface SettingsApiResponse<T> {
  data: T | null;
  error_messages: string[];
  warning_messages: string[];
}

export interface EnterpriseAiConfiguredModel {
  model_id: string;
  display_name: string;
  vision_enabled: boolean;
}

export type EnterpriseAiVlmInputMode = "auto" | "files_api" | "inline_image";

export interface EnterpriseAiModelSettings {
  endpoint: string;
  project_ocid: string;
  api_key: string;
  has_api_key: boolean;
  clear_api_key: boolean;
  models: EnterpriseAiConfiguredModel[];
  default_model_id: string;
  api_path: string;
  vlm_input_mode: EnterpriseAiVlmInputMode;
  text_payload_template: string;
  vision_payload_template: string;
  text_response_path: string;
  vision_response_path: string;
  timeout_seconds: number;
  max_retries: number;
}

export interface GenerativeAiModelSettings {
  embedding_model: string;
  embedding_dim: number;
  rerank_model: string;
}

export interface ModelSettingsPayload {
  enterprise_ai: EnterpriseAiModelSettings;
  generative_ai: GenerativeAiModelSettings;
}

export interface ModelSettingsData {
  settings: ModelSettingsPayload;
  checks: Record<"enterprise_ai" | "generative_ai" | "embedding_dim", ModelSettingsCheckStatus>;
  model_settings_file: string;
  source: "runtime";
}

export interface ModelSettingsTestRequest {
  settings: ModelSettingsPayload;
  target_type: ModelSettingsTestTargetType;
  model_id: string;
  vision_enabled: boolean;
}

export interface ModelSettingsTestResult {
  status: ModelSettingsTestStatus;
  target_type: ModelSettingsTestTargetType;
  model_id: string;
  message: string;
  troubleshooting: string[];
  raw_error: string | null;
  error_type: string | null;
  elapsed_ms: number;
  checked_at: string;
  details: Record<string, string | number | boolean | null>;
}

export interface DatabaseSettingsData {
  user: string;
  dsn: string;
  wallet_dir: string;
  wallet_uploaded: boolean;
  available_services: string[];
  has_password: boolean;
  has_wallet_password: boolean;
  readiness: string;
  embedding_dimension: number;
  vector_column: string;
  adb_ocid: string;
  region: string;
  config_source: "runtime";
}

export type AdbOperationStatus =
  | "success"
  | "not_configured"
  | "error"
  | "accepted"
  | "already_available"
  | "already_stopped"
  | "cannot_start"
  | "cannot_stop";

export interface AdbInfoData {
  status: AdbOperationStatus;
  message: string;
  id: string | null;
  display_name: string | null;
  lifecycle_state: string | null;
  db_name: string | null;
  cpu_core_count: number | null;
  data_storage_size_in_tbs: number | null;
  region: string | null;
}

export interface AdbSettingsUpdate {
  adb_ocid: string;
  region: string;
}

export interface DatabaseSettingsUpdate {
  user: string;
  dsn: string;
  wallet_dir: string;
  password?: string;
  wallet_password?: string;
  clear_password?: boolean;
  clear_wallet_password?: boolean;
}

export interface DatabaseConnectionTestResult {
  status: DatabaseConnectionTestStatus;
  readiness: string;
  message: string;
  elapsed_ms: number;
  troubleshooting: string[];
  details: Record<string, string | number | boolean | null>;
  checked_at: string;
  error_type: string | null;
}

export interface UploadStorageSettingsData {
  backend: UploadStorageBackend;
  local_storage_dir: string;
  object_storage_region: string;
  object_storage_namespace: string;
  object_storage_bucket: string;
  readiness: string;
  max_upload_bytes: number;
  config_source: "runtime";
}

export interface UploadStorageSettingsUpdate {
  backend: UploadStorageBackend;
  local_storage_dir: string;
  object_storage_namespace?: string;
  object_storage_bucket: string;
}

export type OciConfigField =
  | "user"
  | "fingerprint"
  | "tenancy"
  | "region"
  | "key_file";

export interface OciConfigReadRequest {
  config_file: string;
  profile: string;
}

export interface OciConfigReadData {
  profile: string;
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
  key_file: string;
  applied_fields: OciConfigField[];
}

export interface OciSettingsUpdate {
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
}

export interface OciSettingsData {
  config_file: string;
  profile: string;
  user: string;
  fingerprint: string;
  tenancy: string;
  region: string;
  key_file: string;
  key_file_exists: boolean;
  config_file_exists: boolean;
  config_source: "runtime";
}

export interface OciObjectStorageSettingsUpdate {
  object_storage_region: string;
  object_storage_namespace: string;
}

export interface OciConfigTestResult {
  status: OciConfigTestStatus;
  profile: string;
  config_file: string;
  key_file: string;
  config_file_exists: boolean;
  key_file_exists: boolean;
  missing_fields: OciConfigField[];
  permission_issues: string[];
  oci_directory_mode: string | null;
  config_file_mode: string | null;
  key_file_mode: string | null;
  message: string;
  checked_at: string;
  error_type: string | null;
}

export interface OciObjectStorageNamespaceRequest {
  config_file: string;
  profile: string;
  region: string;
}

export interface OciObjectStorageNamespaceData {
  namespace: string;
}

export interface OciPrivateKeyUploadData {
  key_file: string;
  saved: boolean;
}

/** API 由来のエラー。`messages` は日本語のユーザー向け文言。 */
export class ApiError extends Error {
  readonly status: number;
  readonly messages: string[];

  constructor(status: number, messages: string[]) {
    super(messages[0] ?? `APIエラー (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.messages = messages.length > 0 ? messages : [`APIエラー (${status})`];
  }
}

async function parseSettingsEnvelope<T>(response: Response): Promise<SettingsApiResponse<T>> {
  try {
    const payload = (await response.json()) as Partial<SettingsApiResponse<T>> &
      Partial<ApiEnvelope<T>> & { detail?: unknown };
    const errorMessages =
      payload.error_messages ??
      (payload.error ? [payload.error] : payload.detail ? [String(payload.detail)] : []);
    return {
      data: payload.data ?? null,
      error_messages: errorMessages,
      warning_messages: payload.warning_messages ?? [],
    };
  } catch {
    return { data: null, error_messages: [], warning_messages: [] };
  }
}

async function settingsRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const envelope = await parseSettingsEnvelope<T>(response);
  if (!response.ok) {
    throw new ApiError(
      response.status,
      envelope.error_messages.length > 0
        ? envelope.error_messages
        : [`APIエラー (${response.status})`]
    );
  }
  return envelope.data as T;
}

function jsonBody(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  getModelSettings: () => settingsRequest<ModelSettingsData>("/api/settings/model"),
  updateModelSettings: (body: ModelSettingsPayload) =>
    settingsRequest<ModelSettingsData>("/api/settings/model", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  checkModelSettings: (body: ModelSettingsPayload) =>
    settingsRequest<ModelSettingsData>("/api/settings/model/check", jsonBody(body)),
  testModelSettings: (body: ModelSettingsTestRequest) =>
    settingsRequest<ModelSettingsTestResult>("/api/settings/model/test", jsonBody(body)),

  getDatabaseSettings: () => settingsRequest<DatabaseSettingsData>("/api/settings/database"),
  updateDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    settingsRequest<DatabaseSettingsData>("/api/settings/database", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  uploadDatabaseWallet: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return settingsRequest<DatabaseSettingsData>("/api/settings/database/wallet", {
      method: "POST",
      body: form,
    });
  },
  testDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    settingsRequest<DatabaseConnectionTestResult>(
      "/api/settings/database/test",
      jsonBody(body)
    ),

  getAdbInfo: () => settingsRequest<AdbInfoData>("/api/settings/database/adb"),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/start", { method: "POST" }),
  stopAdb: () =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  getUploadStorageSettings: () =>
    settingsRequest<UploadStorageSettingsData>("/api/settings/upload-storage"),
  updateUploadStorageSettings: (body: UploadStorageSettingsUpdate) =>
    settingsRequest<UploadStorageSettingsData>("/api/settings/upload-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  getOciSettings: () => settingsRequest<OciSettingsData>("/api/settings/oci"),
  updateOciSettings: (body: OciSettingsUpdate) =>
    settingsRequest<OciSettingsData>("/api/settings/oci", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  updateOciObjectStorageSettings: (body: OciObjectStorageSettingsUpdate) =>
    settingsRequest<UploadStorageSettingsData>("/api/settings/oci/object-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  readOciConfig: (body: OciConfigReadRequest) =>
    settingsRequest<OciConfigReadData>("/api/settings/oci/config/read", jsonBody(body)),
  testOciConfig: () =>
    settingsRequest<OciConfigTestResult>("/api/settings/oci/config/test", {
      method: "POST",
    }),
  readOciObjectStorageNamespace: (body: OciObjectStorageNamespaceRequest) =>
    settingsRequest<OciObjectStorageNamespaceData>(
      "/api/settings/oci/object-storage/namespace",
      jsonBody(body)
    ),
  uploadOciPrivateKey: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return settingsRequest<OciPrivateKeyUploadData>("/api/settings/oci/key-file", {
      method: "POST",
      body: form,
    });
  },
};
