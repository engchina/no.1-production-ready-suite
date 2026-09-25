/** モデル設定 API の型（backend の pr_system_settings.model と同じ形。#103）。 */
export type EnterpriseAiVlmInputMode = "auto" | "files_api" | "inline_image";
export type ModelSettingsSecretSource =
  "environment" | "legacy_json" | "missing";
export type ModelSettingsTestStatus = "success" | "failed";
export type ModelSettingsTestTargetType =
  "enterprise_text" | "enterprise_vision" | "embedding" | "rerank";

export interface EnterpriseAiConfiguredModel {
  model_id: string;
  display_name: string;
  vision_enabled: boolean;
}

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
