import { Skeleton } from "@engchina/production-ready-ui";
import { DatabaseSettingsPage } from "@engchina/production-ready-system-settings";
import { useQueryClient } from "@tanstack/react-query";

import { TimedLoadingState } from "@/components/ProcessingState";
import { SelectAiCredentialCard } from "@/components/settings/SelectAiCredentialCard";
import { ApiError, api } from "@/lib/api";
import { t } from "@/lib/i18n";
import { queryKeys } from "@/lib/queries";

/** データベース設定。画面の実体は platform の共有パッケージ（#108）。 */
export function DatabaseSettingsClient() {
  const queryClient = useQueryClient();
  return (
    <DatabaseSettingsPage
      api={api}
      errorMessage={(error) =>
        error instanceof ApiError ? error.message : undefined
      }
      connectionSecurity
      onDatabaseChanged={() =>
        queryClient.invalidateQueries({ queryKey: queryKeys.databaseStatus })
      }
      loadingFallback={
        <TimedLoadingState
          label={t("settings.database.loading")}
          operationKey="settings-database-load"
          placement="page"
          testId="settings-database-loading"
        >
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-[460px] w-full rounded-lg" />
        </TimedLoadingState>
      }
    >
      <SelectAiCredentialCard />
    </DatabaseSettingsPage>
  );
}
