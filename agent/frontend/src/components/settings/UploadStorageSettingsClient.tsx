import { UploadStorageSettingsPage } from "@engchina/production-ready-system-settings";
import { useNavigate } from "react-router-dom";

import { ApiError, api } from "@/lib/api";
import { APP_ROUTES } from "@/lib/routes";

/** アップロード保存先設定。画面の実体は platform の共有パッケージ（#97）。 */
export function UploadStorageSettingsClient() {
  const navigate = useNavigate();
  return (
    <UploadStorageSettingsPage
      api={{
        get: () => api.getUploadStorageSettings(),
        update: (payload) => api.updateUploadStorageSettings(payload),
      }}
      onOpenOciSettings={() => navigate(APP_ROUTES.settingsOci)}
      placeholders={{ localStorageDir: "/u01/data/production-ready-agent" }}
      errorMessage={(error) => (error instanceof ApiError ? error.message : undefined)}
    />
  );
}
