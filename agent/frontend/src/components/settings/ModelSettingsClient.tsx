import { PageHeader } from "@production-ready/ui";
import { ModelSettingsPage } from "@production-ready/system-settings";

import { ApiError, api } from "@/lib/api";
import { draftGuardMessages } from "@/lib/leave-guard";
import { t } from "@/lib/i18n";

// 保存したら捨てる cache: Runtime の状態（既定のモデル・登録モデル。業務 Agent の編集の「既定（〈モデル〉）」と
// モデルの選択肢に使う。#1416）。
const MODEL_DEPENDENT_QUERY_KEYS = [["runtime-status"]];

/** モデル設定。画面の実体は platform の共有パッケージ（#103）。 */
export function ModelSettingsClient() {
  return (
    <div>
      <PageHeader wide title={t("nav.settingsModel")} subtitle={t("settings.model.subtitle")} />
      <ModelSettingsPage
        api={api}
        draftGuardMessages={draftGuardMessages()}
        errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
        placeholders={{ displayName: t("settings.model.displayNamePlaceholder") }}
        invalidateQueryKeys={MODEL_DEPENDENT_QUERY_KEYS}
      />
    </div>
  );
}
