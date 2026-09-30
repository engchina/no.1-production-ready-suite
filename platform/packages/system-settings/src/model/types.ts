/** モデル設定 API の型（backend の pr_system_settings.model と同じ形。#103）。 */
export type EnterpriseAiVlmInputMode = "auto" | "files_api" | "inline_image";
export type ModelSettingsSecretSource =
  "environment" | "legacy_json" | "missing";
export type ModelSettingsTestStatus = "success" | "failed";
export type ModelSettingsTestTargetType =
  "enterprise_text" | "enterprise_vision" | "embedding" | "rerank";

/**
 * OCI Enterprise AI の接続の ID（#533）。接続ごとに backend の Settings の属性・env 名が決まっている。
 * 画面の名前は「プライマリ接続」「セカンダリ接続」（#542）。
 */
export type EnterpriseAiConnectionId = "primary" | "secondary";
/** 接続の並び（プライマリ接続が既定）。backend の ENTERPRISE_AI_CONNECTION_IDS と同じ。 */
export const ENTERPRISE_AI_CONNECTION_IDS: readonly EnterpriseAiConnectionId[] = [
  "primary",
  "secondary",
];
/** 接続の上限（backend の MAX_ENTERPRISE_AI_CONNECTIONS と同じ）。 */
export const MAX_ENTERPRISE_AI_CONNECTIONS = ENTERPRISE_AI_CONNECTION_IDS.length;

export interface EnterpriseAiConfiguredModel {
  model_id: string;
  display_name: string;
  vision_enabled: boolean;
  /** 呼び出しに使う接続（#533）。未指定はプライマリ接続。存在しない接続を指すと保存時に欄のエラー。 */
  connection_id?: string;
}

/**
 * OCI Enterprise AI の接続 1 件（#533）。`api_key` は書き込み専用で、応答では空。
 * 表示名は持たない（#542。画面は ID から「プライマリ接続」「セカンダリ接続」と表示する）。
 */
export interface EnterpriseAiConnectionSettings {
  connection_id: EnterpriseAiConnectionId;
  endpoint: string;
  project_ocid: string;
  api_key: string;
  has_api_key: boolean;
  clear_api_key: boolean;
}

export interface EnterpriseAiModelSettings {
  /** 1 件目（プライマリ接続）が既定。2 件目はセカンダリ接続（任意。#533）。 */
  connections: EnterpriseAiConnectionSettings[];
  models: EnterpriseAiConfiguredModel[];
  /** 画像を扱わない処理の既定。空なら既定の Vision モデルを使う（#499）。 */
  default_text_model_id: string;
  /** 画像を読む処理の既定。モデルを登録したら必須で、Vision 対応のモデルに限る（#499）。 */
  default_vision_model_id: string;
  api_path: string;
  vlm_input_mode: EnterpriseAiVlmInputMode;
  text_payload_template: string;
  vision_payload_template: string;
  text_response_path: string;
  vision_response_path: string;
  timeout_seconds: number;
  max_retries: number;
  llm_max_output_tokens: number;
  vlm_max_output_tokens: number;
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

/** 製品側の API 関数（fetch wrapper・認証・エラー形式は製品ごとに異なるため注入する）。 */
export interface ModelSettingsApi {
  getModelSettings: (options?: {
    signal?: AbortSignal;
  }) => Promise<ModelSettingsData>;
  updateModelSettings: (
    payload: ModelSettingsPayload,
  ) => Promise<ModelSettingsData>;
  testModelSettings: (
    request: ModelSettingsTestRequest,
  ) => Promise<ModelSettingsTestResult>;
}

/** 3製品で共通の React Query key（RAG の文書画面も同じ key でモデル設定を読む）。 */
export const MODEL_SETTINGS_QUERY_KEY = ["settings", "model"] as const;
