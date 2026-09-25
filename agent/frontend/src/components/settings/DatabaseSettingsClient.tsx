import { DatabaseSettingsPage } from "@engchina/production-ready-system-settings";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api";
import { queryKeys } from "@/lib/queries";

/** データベース設定。画面の実体は platform の共有パッケージ（#108）。 */
export function DatabaseSettingsClient() {
  const queryClient = useQueryClient();
  return (
    <DatabaseSettingsPage
      api={api}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
      onDatabaseChanged={() =>
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboardSummary })
      }
    />
  );
}
