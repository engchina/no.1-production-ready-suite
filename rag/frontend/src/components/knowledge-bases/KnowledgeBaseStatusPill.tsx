import { type KnowledgeBaseStatus } from "@/lib/api";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** ナレッジベース状態の日本語ラベル。 */
export function knowledgeBaseStatusLabel(status: KnowledgeBaseStatus) {
  return status === "ACTIVE"
    ? t("knowledgeBases.status.ACTIVE")
    : t("knowledgeBases.status.ARCHIVED");
}

/** ナレッジベース状態を表す共通ステータスピル(一覧・詳細で共有)。 */
export function KnowledgeBaseStatusPill({ status }: { status: KnowledgeBaseStatus }) {
  return (
    <span
      className={cn(
        "inline-flex rounded-full px-2 py-0.5 text-xs font-medium",
        status === "ACTIVE" && "bg-success-bg text-success",
        status === "ARCHIVED" && "bg-muted/10 text-muted"
      )}
    >
      {knowledgeBaseStatusLabel(status)}
    </span>
  );
}
