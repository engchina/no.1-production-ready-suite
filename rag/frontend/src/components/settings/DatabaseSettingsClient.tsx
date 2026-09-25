import { DatabaseSettingsPage } from "@engchina/production-ready-system-settings";
import { useQueryClient } from "@tanstack/react-query";

import { CardErrorBoundary } from "@/components/CardErrorBoundary";
import { SystemTablesCard } from "@/components/settings/SystemTablesCard";
import { ApiError, api } from "@/lib/api";
import { t } from "@/lib/i18n";
import { queryKeys } from "@/lib/queries";

/** データベース設定。画面の実体は platform の共有パッケージ（#108）。 */
export function DatabaseSettingsClient() {
  const queryClient = useQueryClient();
  return (
    <DatabaseSettingsPage
      api={api}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
      onDatabaseChanged={async () => {
        await queryClient.invalidateQueries({ queryKey: queryKeys.databaseStatus });
        void queryClient.invalidateQueries({ queryKey: queryKeys.dashboardSummary });
      }}
    >
      {/* カードごとに描画例外を閉じ込め、1 枚の失敗で他カードが消えないようにする(#67)。 */}
      <CardErrorBoundary label={t("settings.database.systemTables.title")}>
        <SystemTablesCard />
      </CardErrorBoundary>
    </DatabaseSettingsPage>
  );
}
