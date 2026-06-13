import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n";

export default function SettingsDatabasePage() {
  return (
    <div>
      <PageHeader title={t("nav.settingsDatabase")} subtitle="Oracle 26ai 接続を設定します。" />
      <div className="p-8 text-sm text-muted">（実装予定）</div>
    </div>
  );
}
