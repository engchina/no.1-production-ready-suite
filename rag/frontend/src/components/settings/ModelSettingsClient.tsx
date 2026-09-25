import { PageHeader } from "@engchina/production-ready-ui";
import { ModelSettingsPage } from "@engchina/production-ready-system-settings";

import { ApiError, api } from "@/lib/api";
import { draftGuardMessages } from "@/lib/leave-guard";
import { t } from "@/lib/i18n";

/** モデル設定。画面の実体は platform の共有パッケージ（#103）。 */
export function ModelSettingsClient() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
      <ModelSettingsPage
        api={api}
        draftGuardMessages={draftGuardMessages()}
        errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
        placeholders={{ displayName: "業務 RAG 標準" }}
      />
    </div>
  );
}
