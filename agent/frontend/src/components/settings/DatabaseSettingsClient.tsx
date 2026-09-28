import { DatabaseSettingsPage } from "@engchina/production-ready-system-settings";

import { ApiError, api } from "@/lib/api";

/** データベース設定。画面の実体は platform の共有パッケージ（#108）。 */
export function DatabaseSettingsClient() {
  return (
    <DatabaseSettingsPage
      api={api}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
    />
  );
}
