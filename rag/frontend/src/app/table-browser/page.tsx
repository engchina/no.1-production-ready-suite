import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n";

export default function TableBrowserPage() {
  return (
    <div>
      <PageHeader title={t("nav.tableBrowser")} subtitle="登録済みデータをテーブル形式で参照します。" />
      <div className="p-8 text-sm text-muted">（実装予定）</div>
    </div>
  );
}
