import { PageHeader } from "@/components/PageHeader";
import { t } from "@/lib/i18n";

export default function CategoryManagementPage() {
  return (
    <div>
      <PageHeader title={t("nav.categoryManagement")} subtitle="作成済みの伝票分類を編集・整理します。" />
      <div className="p-8 text-sm text-muted">（実装予定）</div>
    </div>
  );
}
