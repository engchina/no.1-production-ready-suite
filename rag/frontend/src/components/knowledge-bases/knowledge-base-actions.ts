import type { EntityAction } from "@engchina/production-ready-ui";
import { Archive } from "lucide-react";

import { useConfirm } from "@/components/ui/confirm-dialog";
import { ApiError, DEFAULT_KNOWLEDGE_BASE_NAME, type KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useArchiveKnowledgeBase } from "@/lib/queries";
import { toast } from "@/lib/toast";

type KnowledgeBaseTarget = Pick<KnowledgeBaseSummary, "id" | "name" | "status">;

/**
 * ナレッジベース 1 件に対する操作（buttons.md §5.1）。一覧の行（RowActionMenu）と
 * 詳細（ObjectActionBar）で同じ定義を使う。アーカイブは danger の項目として確認を通す。
 */
export function useKnowledgeBaseActions() {
  const confirm = useConfirm();
  const archive = useArchiveKnowledgeBase();

  const handleArchive = async (knowledgeBase: KnowledgeBaseTarget) => {
    const ok = await confirm({
      title: t("knowledgeBases.confirm.archive.title"),
      description: t("knowledgeBases.confirm.archive.description", {
        name: knowledgeBase.name,
      }),
      confirmLabel: t("knowledgeBases.actions.archive"),
      tone: "danger",
      dismissOnOverlay: false,
    });
    if (!ok) return;
    archive.mutate(knowledgeBase.id, {
      onSuccess: () => toast.success(t("knowledgeBases.toast.archived")),
      onError: (error) =>
        toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.archive")),
    });
  };

  return (knowledgeBase: KnowledgeBaseTarget): EntityAction[] => {
    const isDefault = knowledgeBase.name === DEFAULT_KNOWLEDGE_BASE_NAME;
    return [
      {
        id: "archive",
        label: t("knowledgeBases.actions.archive"),
        // DEFAULT は無効の理由を名前で伝える（読み上げでも理由が分かるように）。
        ariaLabel: isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined,
        icon: Archive,
        tone: "danger",
        visible: knowledgeBase.status !== "ARCHIVED",
        disabled: isDefault,
        loading: archive.isPending && archive.variables === knowledgeBase.id,
        testId: `knowledge-base-archive-${knowledgeBase.id}`,
        onSelect: () => handleArchive(knowledgeBase),
      },
    ];
  };
}
