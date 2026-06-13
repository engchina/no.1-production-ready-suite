import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n";

export default function SettingsModelPage() {
  return (
    <div>
      <PageHeader
        title={t("nav.settingsModel")}
        subtitle="OCI Enterprise AI（LLM/VLM）と OCI Generative AI（埋め込み/リランク）のモデルを設定します。"
      />
      <div className="p-8 text-sm text-muted">（実装予定）</div>
    </div>
  );
}
