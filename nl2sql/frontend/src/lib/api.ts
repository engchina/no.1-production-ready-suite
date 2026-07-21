import {
  confirmDatabaseUnavailable,
  isDatabaseReadinessRequest,
  shouldConfirmDatabaseUnavailable,
} from "./database-load-error.ts";

export interface ApiEnvelope<T> {
  data: T;
  error?: string;
  request_id?: string;
}

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

export function isAbortError(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

/**
 * アプリ全体の API 境界。Cookie セッション、CSRF、認証状態イベントを一箇所で扱う。
 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  headers.set("Accept", headers.get("Accept") ?? "application/json");
  if (UNSAFE_METHODS.has(method)) {
    const csrfToken = readCookie("nl2sql_csrf");
    if (csrfToken) headers.set("X-CSRF-Token", csrfToken);
  }
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, credentials: "include" });
  } catch (cause) {
    if (isAbortError(cause)) throw cause;
    if (!isDatabaseReadinessRequest(path)) await confirmDatabaseUnavailable();
    throw cause;
  }
  if (response.status === 401) window.dispatchEvent(new CustomEvent("app-auth-unauthorized"));
  if (response.status === 403) window.dispatchEvent(new CustomEvent("app-auth-forbidden"));
  if (shouldConfirmDatabaseUnavailable(path, response.status)) {
    await confirmDatabaseUnavailable();
  }
  return response;
}

async function parseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as ApiEnvelope<T> & { error_messages?: unknown };
  if (!response.ok) {
    // 共通例外ハンドラは ApiResponse { error_messages: [...] } 形式で返す
    const errorMessages = payload.error_messages;
    const message =
      (Array.isArray(errorMessages) && errorMessages.length > 0
        ? errorMessages.map(String).join(" ")
        : "") ||
      payload.error ||
      (typeof payload === "object" && payload !== null && "detail" in payload
        ? String((payload as { detail?: unknown }).detail)
        : "API リクエストに失敗しました");
    throw new ApiError(response.status, [message]);
  }
  return payload.data;
}

export interface ApiResponseMetadata<T> {
  data: T;
  etag: string;
}

export async function apiGet<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const response = await apiFetch(path, { signal: options.signal });
  return parseJson<T>(response);
}

export async function apiGetWithMetadata<T>(
  path: string,
  options: ApiRequestOptions = {}
): Promise<ApiResponseMetadata<T>> {
  const response = await apiFetch(path, { signal: options.signal });
  const data = await parseJson<T>(response);
  return { data, etag: response.headers.get("ETag")?.replaceAll('"', "") ?? "" };
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await apiFetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  });
  return parseJson<T>(response);
}

export async function apiPatch<T>(
  path: string,
  body?: unknown,
  headers: Record<string, string> = {},
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await apiFetch(path, {
    method: "PATCH",
    headers: { Accept: "application/json", "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: options.signal,
  });
  return parseJson<T>(response);
}

export async function apiDelete<T>(
  path: string,
  headers: Record<string, string> = {},
  options: ApiRequestOptions = {}
): Promise<T> {
  const response = await apiFetch(path, {
    method: "DELETE",
    headers: { Accept: "application/json", ...headers },
    signal: options.signal,
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
export type ModelSettingsSecretSource = "environment" | "legacy_json" | "missing";
export type ModelSettingsTestStatus = "success" | "failed";
export type ModelSettingsTestTargetType =
  | "enterprise_text"
  | "enterprise_vision"
  | "embedding"
  | "rerank";
export type UploadStorageBackend = "local" | "oci";
export type DatabaseConnectionTestStatus = "success" | "failed" | "skipped";
export type OciConfigTestStatus = "success" | "failed";

export interface DatabaseStatusData {
  status: "ok" | "not_configured" | "setup_required" | "unreachable";
  check: string;
  detail: string | null;
}

export interface PersistenceStatusData {
  mode: "memory" | "oracle";
  ready: boolean;
  durable: boolean;
  writable: boolean;
  snapshot_loaded: boolean;
  reason_code: string | null;
  checked_at: string;
}

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
  secret_source: ModelSettingsSecretSource;
  legacy_secret_detected: boolean;
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

export type SystemTableSchemaStatus = "missing" | "partial" | "outdated" | "ready";
export type SystemTableOperationStatus = "idle" | "running" | "failed";
export type SystemTableOperationResult =
  | "no_op"
  | "initialized"
  | "migrated"
  | "recreated";

export interface SystemTableMissingObject {
  name: string;
  object_type: "TABLE" | "INDEX" | "SEQUENCE";
}

export interface SystemTableMetadata {
  name: string;
  exists: boolean;
  estimated_rows: number | null;
  created_at: string | null;
  last_analyzed_at: string | null;
}

export interface SystemTableOperationState {
  status: SystemTableOperationStatus;
  operation_kind: "initialize" | "recreate" | null;
  lease_expires_at: string | null;
  last_error_code: string | null;
  schema_epoch: number;
  updated_at: string | null;
}

export interface SystemTablesStatusData {
  status: SystemTableSchemaStatus;
  schema_head: number;
  applied_versions: number[];
  pending_versions: number[];
  expected_object_count: number;
  existing_object_count: number;
  expected_table_count: number;
  existing_table_count: number;
  missing_objects: SystemTableMissingObject[];
  tables: SystemTableMetadata[];
  operation_state: SystemTableOperationState;
}

export interface SystemTablesInitializeRequest {
  recreate: boolean;
  confirmation?: string;
}

export interface SystemTablesOperationData extends SystemTablesStatusData {
  operation: SystemTableOperationResult;
  dropped_object_count: number;
  created_object_count: number;
}

export type DatabaseWalletDownloadStatus = "downloaded" | "already_configured";

export interface DatabaseWalletDownloadData {
  status: DatabaseWalletDownloadStatus;
  settings: DatabaseSettingsData;
}

export interface SchemaOwnersData {
  current_owner: string;
  owners: Array<{
    owner: string;
    is_current: boolean;
    table_count: number;
    view_count: number;
  }>;
  excluded_oracle_maintained_count: number;
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
  const response = await apiFetch(path, {
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
  getDatabaseStatus: (options: ApiRequestOptions = {}) =>
    settingsRequest<DatabaseStatusData>("/api/ready/database", {
      signal: options.signal,
    }),
  getPersistenceStatus: (options: ApiRequestOptions = {}) =>
    settingsRequest<PersistenceStatusData>("/api/nl2sql/persistence", {
      signal: options.signal,
    }),
  recoverPersistence: () =>
    settingsRequest<PersistenceStatusData>("/api/nl2sql/persistence/recover", {
      method: "POST",
    }),
  getModelSettings: (options: ApiRequestOptions = {}) =>
    settingsRequest<ModelSettingsData>("/api/settings/model", {
      signal: options.signal,
    }),
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

  getDatabaseSettings: (options: ApiRequestOptions = {}) =>
    settingsRequest<DatabaseSettingsData>("/api/settings/database", {
      signal: options.signal,
    }),
  getSystemTablesStatus: (options: ApiRequestOptions = {}) =>
    settingsRequest<SystemTablesStatusData>("/api/settings/database/system-tables", {
      signal: options.signal,
    }),
  initializeSystemTables: (body: SystemTablesInitializeRequest) =>
    settingsRequest<SystemTablesOperationData>(
      "/api/settings/database/system-tables/initialize",
      jsonBody(body)
    ),
  getSchemaOwners: (options: ApiRequestOptions = {}) =>
    settingsRequest<SchemaOwnersData>("/api/schema/owners", {
      signal: options.signal,
    }),
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
  downloadDatabaseWallet: () =>
    settingsRequest<DatabaseWalletDownloadData>(
      "/api/settings/database/wallet/download",
      { method: "POST" }
    ),
  testDatabaseSettings: (body: DatabaseSettingsUpdate) =>
    settingsRequest<DatabaseConnectionTestResult>(
      "/api/settings/database/test",
      jsonBody(body)
    ),

  getAdbInfo: (options: ApiRequestOptions = {}) =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb", {
      signal: options.signal,
    }),
  updateAdbSettings: (body: AdbSettingsUpdate) =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/settings", jsonBody(body)),
  startAdb: () =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/start", { method: "POST" }),
  stopAdb: () =>
    settingsRequest<AdbInfoData>("/api/settings/database/adb/stop", { method: "POST" }),

  getUploadStorageSettings: (options: ApiRequestOptions = {}) =>
    settingsRequest<UploadStorageSettingsData>("/api/settings/upload-storage", {
      signal: options.signal,
    }),
  updateUploadStorageSettings: (body: UploadStorageSettingsUpdate) =>
    settingsRequest<UploadStorageSettingsData>("/api/settings/upload-storage", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),

  getOciSettings: (options: ApiRequestOptions = {}) =>
    settingsRequest<OciSettingsData>("/api/settings/oci", {
      signal: options.signal,
    }),
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
