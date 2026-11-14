export interface GuardrailSettingsData {
  input_enabled: boolean;
  output_enabled: boolean;
  oci_configured: boolean;
  oci_warning_code?: "oci_guardrails_compartment_missing" | "oci_guardrails_credentials_invalid" | null;
}
export interface GuardrailSettingsUpdate { input_enabled: boolean; output_enabled: boolean }
export interface GuardrailSettingsApi {
  get: (options?: { signal?: AbortSignal }) => Promise<GuardrailSettingsData>;
  update: (payload: GuardrailSettingsUpdate) => Promise<GuardrailSettingsData>;
}
export const GUARDRAIL_SETTINGS_QUERY_KEY = ["settings", "guardrail"] as const;
