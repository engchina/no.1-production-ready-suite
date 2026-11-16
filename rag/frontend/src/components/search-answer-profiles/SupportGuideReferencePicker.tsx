import { DocumentPickerSheet } from "@/components/documents/DocumentPickerSheet";
import { t } from "@/lib/i18n";
import { initialLoadError, useSearchAnswerProfile } from "@/lib/queries";
import { referenceRowFromDocument, type ReferenceForm } from "@/lib/support-guide-form";

/**
 * 業務ガイドの資料を選んで足すシート（ナレッジベースの「文書を追加」と同じ `DocumentPickerSheet`。#2749・#5279）。
 * 候補はプロファイルのナレッジベースの文書（公開の前の検証と同じ範囲）に絞り、すでに入れた文書は「追加済み」で選べなくする。
 * 足すのはフォームの中だけ（保存は別）なので、足したらシートを閉じ、足した行がフォームに出る。
 */
export function SupportGuideReferencePicker({
  open,
  id,
  testId,
  title,
  searchAnswerProfileId,
  references,
  maxSelection,
  onAdd,
  onClose,
}: {
  open: boolean;
  id: string;
  testId: string;
  /** シートの見出し（開くボタンと同じ語）。 */
  title: string;
  searchAnswerProfileId: string;
  /** すでに入れた資料（同じ文書は選べなくする）。 */
  references: readonly ReferenceForm[];
  maxSelection: number;
  onAdd: (references: ReferenceForm[]) => void;
  onClose: () => void;
}) {
  const profile = useSearchAnswerProfile(searchAnswerProfileId);
  const loadError = initialLoadError(profile);
  const added = new Set(references.map((reference) => reference.documentId));
  return (
    <DocumentPickerSheet
      open={open}
      id={id}
      testId={testId}
      title={title}
      knowledgeBaseIds={profile.data?.config.knowledge_base_ids ?? []}
      scopePending={profile.isPending}
      scopeError={loadError || (!profile.isPending && !profile.data) ? t("supportGuides.referencePicker.loadError") : undefined}
      onScopeRetry={() => void profile.refetch()}
      unavailableReason={(document) => (added.has(document.id) ? t("documentPicker.alreadyAdded") : undefined)}
      maxSelection={maxSelection}
      onSubmit={(documents) => onAdd(documents.map(referenceRowFromDocument))}
      onClose={onClose}
    />
  );
}
