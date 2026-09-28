import type { EntityAction } from "@engchina/production-ready-ui";
import { Archive, Pencil } from "lucide-react";

import { useAuth } from "@/components/security/AuthProvider";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ApiError, DEFAULT_KNOWLEDGE_BASE_NAME, type KnowledgeBaseSummary } from "@/lib/api";
import { t } from "@/lib/i18n";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import { useArchiveKnowledgeBase } from "@/lib/queries";
import { toast } from "@/lib/toast";

type KnowledgeBaseTarget = Pick<KnowledgeBaseSummary, "id" | "name" | "status">;

// API（KnowledgeBaseCreateRequest / UpdateRequest）の上限。超えると 422 の英語の検証メッセージに
// なるため入力で止める。
export const NAME_MAX_LENGTH = 256;
export const DESCRIPTION_MAX_LENGTH = 2000;

/** 名前の検証（作成・編集で共通）。DEFAULT は予約名。 */
export function validateKnowledgeBaseName(name: string) {
  const cleaned = name.trim();
  if (!cleaned) return t("knowledgeBases.validation.nameRequired");
  if (cleaned.toUpperCase() === DEFAULT_KNOWLEDGE_BASE_NAME) {
    return t("knowledgeBases.validation.nameReserved");
  }
  return null;
}

/**
 * ナレッジベース 1 件に対する操作（buttons.md §5.1）。一覧の行（RowActionMenu）と
 * 詳細（ObjectActionBar）で同じ定義を使う。アーカイブは danger の項目として確認を通す。
 * アーカイブはナレッジベース管理（`rag.knowledge_bases.manage`）の権限がある利用者だけに出す（#214）。
 */
export function useKnowledgeBaseActions({
  onEdit,
}: {
  /** 詳細で名前・説明を編集する（渡したときだけ「編集」を出す。#302）。 */
  onEdit?: (knowledgeBase: KnowledgeBaseTarget) => void;
} = {}) {
  const confirm = useConfirm();
  const archive = useArchiveKnowledgeBase();
  const canManage = useAuth().hasPermission(CAPABILITY_PERMISSIONS.knowledgeBasesManage);

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
        id: "edit",
        label: t("knowledgeBases.actions.edit"),
        icon: Pencil,
        // アーカイブ済みは変更できない（業務ビューと同じ扱い）。
        visible: Boolean(onEdit) && knowledgeBase.status !== "ARCHIVED",
        testId: `knowledge-base-edit-${knowledgeBase.id}`,
        onSelect: () => onEdit?.(knowledgeBase),
      },
      {
        id: "archive",
        label: t("knowledgeBases.actions.archive"),
        // DEFAULT は無効の理由を名前で伝える（読み上げでも理由が分かるように）。
        ariaLabel: isDefault ? t("knowledgeBases.default.archiveDisabled") : undefined,
        icon: Archive,
        tone: "danger",
        visible: canManage && knowledgeBase.status !== "ARCHIVED",
        disabled: isDefault,
        loading: archive.isPending && archive.variables === knowledgeBase.id,
        testId: `knowledge-base-archive-${knowledgeBase.id}`,
        onSelect: () => handleArchive(knowledgeBase),
      },
    ];
  };
}
