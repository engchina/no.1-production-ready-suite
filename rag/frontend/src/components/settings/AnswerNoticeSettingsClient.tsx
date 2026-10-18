import { AnswerNoticeSettingsPage } from "@production-ready/system-settings";

import { ApiError, api } from "@/lib/api";
import { draftGuardMessages } from "@/lib/leave-guard";

/** 回答の注意書き（3 製品で共通の文）。画面の実体は platform の共有パッケージ（#2405）。 */
export function AnswerNoticeSettingsClient() {
  return (
    <AnswerNoticeSettingsPage
      api={{
        get: (options) => api.getAnswerNoticeSettings(options),
        update: (payload) => api.updateAnswerNoticeSettings(payload),
      }}
      draftGuardMessages={draftGuardMessages()}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
    />
  );
}
