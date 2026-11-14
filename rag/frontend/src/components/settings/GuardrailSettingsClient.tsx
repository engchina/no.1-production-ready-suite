import { GuardrailSettingsPage } from "@production-ready/system-settings";

import { ApiError, api } from "@/lib/api";
import { draftGuardMessages } from "@/lib/leave-guard";

/** 画面と設定の契約は platform が持つ（3製品で1つの設定。#5061）。 */
export function GuardrailSettingsClient() {
  return (
    <GuardrailSettingsPage
      api={{
        get: (options) => api.getGuardrailSettings(options),
        update: (payload) => api.updateGuardrailSettings(payload),
      }}
      draftGuardMessages={draftGuardMessages()}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
    />
  );
}
