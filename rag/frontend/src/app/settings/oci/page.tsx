import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n";

export default function SettingsOciPage() {
  return (
    <div>
      <PageHeader title={t("nav.settingsOci")} subtitle="OCI 認証情報を設定します。" />
      <div className="p-8 text-sm text-muted">（実装予定）</div>
    </div>
  );
}
