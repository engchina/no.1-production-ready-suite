import { FilePlus2 } from "lucide-react";
import { type ReactNode, type RefObject, useMemo, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { ListPickerSheet, type ListPickerItem } from "@production-ready/ui";
import { ApiError, type DocumentSummary } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { listPickerLabels } from "@/lib/list-picker-labels";
import { useDocumentCandidates } from "@/lib/queries";

/** 選んだ文書の一部を選択から外す（追加できた分だけ外し、残りをもう一度追加できるようにする）。 */
export type DocumentPickerSelection = { deselect: (documentIds: readonly string[]) => void };

/**
 * 文書を選んで追加するシート（ナレッジベースの「文書を追加」#600、業務ガイドの資料 #2749。共通の `ListPickerSheet`。#5279）。
 * 数千〜数万件の文書から、文書名で検索して複数を選ぶ。候補はサーバー側の検索（`q`）と追加読み込み（100 件ずつ）で取り、
 * `unavailableReason` が理由を返す文書は選べなくする。選んだ文書は検索語を変えても残り、「選択中だけ表示」で確かめられる。
 * 候補は開いている間だけ取り、開くたびに検索語と選択を空にする。
 */
export function DocumentPickerSheet({
  open,
  id,
  testId,
  title,
  knowledgeBaseIds,
  scopePending = false,
  scopeError,
  onScopeRetry,
  unavailableReason,
  maxSelection,
  submitting = false,
  status,
  returnFocusRef,
  onSubmit,
  onClose,
}: {
  open: boolean;
  id: string;
  testId: string;
  /** シートの見出し（開くボタンと同じ語）。 */
  title: string;
  /** 候補をこのナレッジベースの文書に絞る（空なら候補は無い）。未指定なら全文書。 */
  knowledgeBaseIds?: readonly string[];
  /** 候補を絞る範囲（`knowledgeBaseIds`）を読み込んでいる間。候補を取らずに読み込み中を出す。 */
  scopePending?: boolean;
  /** 候補を絞る範囲を読み込めなかったときの文言。 */
  scopeError?: string;
  onScopeRetry?: () => void;
  /** 選べない文書の理由（「追加済み」など）。選べるなら undefined。 */
  unavailableReason?: (document: DocumentSummary) => string | undefined;
  /** 一度に選べる数の上限。 */
  maxSelection?: number;
  submitting?: boolean;
  /** 追加の結果（一部を追加できなかったときの `FormStatus` など）。確定の操作の直上に出す。 */
  status?: ReactNode;
  /** 閉じたときにフォーカスを戻す先（開くボタン）。 */
  returnFocusRef?: RefObject<HTMLElement | null>;
  onSubmit: (documents: DocumentSummary[], selection: DocumentPickerSelection) => void;
  onClose: () => void;
}) {
  const [candidateQuery, setCandidateQuery] = useState("");
  // 選んだ文書（検索語を変えても残す。名前を出すために文書ごと持つ）。
  const [selected, setSelected] = useState<Map<string, DocumentSummary>>(() => new Map());
  // 開くたびに検索語と選択を空にする（一覧の中の状態は ListPickerSheet が作り直す）。
  // 閉じる間（シートが滑って消える間）は前の内容を出したままにする。
  const [wasOpen, setWasOpen] = useState(open);
  if (wasOpen !== open) {
    setWasOpen(open);
    if (open) {
      setCandidateQuery("");
      setSelected(new Map());
    }
  }
  const scoped = knowledgeBaseIds !== undefined;
  const candidates = useDocumentCandidates(
    { q: candidateQuery || undefined, knowledgeBaseIds },
    { enabled: open && !scopePending && !scopeError && (!scoped || knowledgeBaseIds.length > 0) },
  );

  const pages = candidates.data?.pages;
  const documents = useMemo(() => {
    // offset のページングは、取込中に文書が増えると前のページと重なることがあるので、ID で重複を除く。
    const seen = new Set<string>();
    const result: DocumentSummary[] = [];
    for (const page of pages ?? []) {
      for (const document of page.items) {
        if (seen.has(document.id)) continue;
        seen.add(document.id);
        result.push(document);
      }
    }
    return result;
  }, [pages]);
  const total = pages?.[pages.length - 1]?.total ?? 0;
  const full = maxSelection !== undefined && selected.size >= maxSelection;

  const toItem = (document: DocumentSummary): ListPickerItem => {
    const reason =
      unavailableReason?.(document) ??
      (full && !selected.has(document.id)
        ? t("documentPicker.limitReached", { count: formatNumber(maxSelection ?? 0) })
        : undefined);
    return {
      key: document.id,
      label: document.file_name,
      textValue: document.file_name,
      description: [document.category_name, formatDateTime(document.uploaded_at)].filter(Boolean).join(" ・ "),
      meta: <StatusBadge status={document.status} />,
      disabled: reason !== undefined,
      disabledReason: reason,
    };
  };
  const items = documents.map(toItem);
  const selectedItems = [...selected.values()].map(toItem);
  const selectedKeys = new Set(selected.keys());
  const documentsById = new Map(documents.map((document) => [document.id, document]));

  const errorMessage = (error: unknown) =>
    error instanceof ApiError ? error.message : t("knowledgeBases.error.documents");

  const selection: DocumentPickerSelection = {
    deselect: (documentIds) =>
      setSelected((current) => {
        const copy = new Map(current);
        for (const documentId of documentIds) copy.delete(documentId);
        return copy;
      }),
  };

  return (
    <ListPickerSheet
      open={open}
      onClose={onClose}
      id={id}
      title={title}
      closeLabel={t("documentPicker.close")}
      label={t("documentPicker.listLabel")}
      items={items}
      selectedKeys={selectedKeys}
      selectedItems={selectedItems}
      onToggle={(item, next) =>
        setSelected((current) => {
          const copy = new Map(current);
          const document = documentsById.get(item.key) ?? current.get(item.key);
          if (next && document) copy.set(item.key, document);
          else copy.delete(item.key);
          return copy;
        })
      }
      onSelectMany={(targets) =>
        setSelected((current) => {
          const copy = new Map(current);
          for (const target of targets) {
            if (maxSelection !== undefined && copy.size >= maxSelection) break;
            const document = documentsById.get(target.key);
            if (document) copy.set(target.key, document);
          }
          return copy;
        })
      }
      onClearSelection={() => setSelected(new Map())}
      total={total}
      search={{
        id: `${id}-search`,
        label: t("documentPicker.search"),
        placeholder: t("documentPicker.searchPlaceholder"),
        value: candidateQuery,
        onSearch: setCandidateQuery,
      }}
      loading={scopePending || (candidates.isPending && candidates.fetchStatus !== "idle")}
      refreshing={candidates.isFetching && candidates.isPlaceholderData}
      error={
        scopeError ?? (candidates.isError && !candidates.data ? errorMessage(candidates.error) : undefined)
      }
      onRetry={scopeError ? onScopeRetry : () => void candidates.refetch()}
      hasMore={Boolean(candidates.hasNextPage)}
      loadingMore={candidates.isFetchingNextPage}
      loadMoreError={candidates.isFetchNextPageError ? errorMessage(candidates.error) : undefined}
      onLoadMore={() => void candidates.fetchNextPage()}
      labels={listPickerLabels({
        loading: t("documentPicker.loading"),
        emptyTitle: t("documentPicker.noOptions"),
        emptyHint: scoped ? t("documentPicker.noOptionsInKnowledgeBasesHint") : t("documentPicker.noOptionsHint"),
        noResultsTitle: t("documentPicker.noResults"),
        noResultsHint: t("documentPicker.noResultsHint"),
      })}
      submitLabel={t("documentPicker.submit", { count: formatNumber(selected.size) })}
      submitIcon={FilePlus2}
      submitting={submitting}
      onSubmit={() => {
        if (selected.size > 0) onSubmit([...selected.values()], selection);
      }}
      cancelLabel={t("documentPicker.cancel")}
      actionsLabel={t("documentPicker.actions")}
      status={status}
      returnFocusRef={returnFocusRef}
      testId={testId}
    />
  );
}
