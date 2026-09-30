"use client";

import { FilePlus2, Files, Unlink, X } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ListPagination } from "@/components/ListPagination";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { StatusBadge } from "@/components/StatusBadge";
import { EditorTargetState } from "@/components/layout/EntityLayout";
import { useAuth } from "@/components/security/AuthProvider";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ClearActionButton,
  DEFAULT_PAGE_SIZE,
  FormActionBar,
  FormSkeleton,
  ListPicker,
  type ListPickerItem,
  ListSkeleton,
  ListToolbar,
  offsetForPage,
  offsetPagination,
  RowActionMenu,
  SearchField,
  TimedLoadingState,
} from "@engchina/production-ready-ui";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentSummary,
  type KnowledgeBaseDetail,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { listPickerLabels } from "@/lib/list-picker-labels";
import {
  useAssignDocumentsToKnowledgeBase,
  useDocumentCandidates,
  useDocuments,
  useKnowledgeBase,
  useRemoveDocumentFromKnowledgeBase,
} from "@/lib/queries";
import { canOpenDocumentDetail } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { useWorkspaceState } from "@/lib/workspace-state";
import { KnowledgeBaseEditor } from "./KnowledgeBaseEditor";
import { KnowledgeBaseExtractionFields } from "./KnowledgeBaseExtractionFields";
import { KnowledgeBaseGraphView } from "./KnowledgeBaseGraphView";
import { KnowledgeBasePipelineCanvas } from "./KnowledgeBasePipelineCanvas";
import { KnowledgeBaseSearchTestPanel } from "./KnowledgeBaseSearchTestPanel";

/**
 * ナレッジベース詳細ページ（`/knowledge-bases/:id`）。業務ビューのエディタと同じ構成にする（#555）:
 * PageHeader（パンくず・状態・件数・一覧へ戻る・保存する）→ 基本情報（名前・説明）→ 所属文書
 * → 検索テスト → 関係情報 → 抽出する項目 → 構築フロー（文書の追加 → 確認 → 構築設定の順）。
 */
export function KnowledgeBaseDetailClient({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const detail = useKnowledgeBase(knowledgeBaseId);
  const navigate = useNavigate();
  const backToList = (options?: { replace?: boolean }) =>
    navigate(APP_ROUTES.knowledgeBases, { replace: options?.replace });

  if (!detail.data) {
    return (
      <EditorTargetState
        id={knowledgeBaseId}
        listLabel={t("nav.knowledgeBases")}
        listHref={APP_ROUTES.knowledgeBases}
        error={detail.error}
        loadingLabel={t("knowledgeBases.detail.loading")}
        loadingTestId="knowledge-base-detail-loading"
        errorFallback={t("knowledgeBases.error.load")}
        skeleton={
          <>
            <FormSkeleton fields={2} actions={false} />
            <ListSkeleton rows={3} rowClassName="h-11" />
          </>
        }
        onBack={() => backToList()}
        onRetry={() => void detail.refetch()}
      />
    );
  }

  const kb = detail.data;
  const isActive = kb.status === "ACTIVE";

  return (
    <KnowledgeBaseEditor
      initial={kb}
      onBack={() => backToList()}
      // アーカイブした対象へ戻らないよう、一覧へ履歴を積まずに戻る（業務ビューと同じ）。
      onArchived={() => backToList({ replace: true })}
    >
      {/* 所属文書: ツールバー（左に検索、右に「文書を追加」）+ 一覧 + ページ送り（#600）。 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Files className="size-4 text-fg-muted" aria-hidden />
            {t("knowledgeBases.documents.title")}
            <span className="tnum rounded-md bg-surface-hover px-2 py-0.5 text-xs font-medium text-fg-muted">
              {kb.document_count}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isActive ? null : (
            <p className="rounded-md border border-border bg-surface-sunken px-3 py-2 text-sm text-fg-muted">
              {t("knowledgeBases.detail.archivedHint")}
            </p>
          )}

          <KnowledgeBaseDocuments knowledgeBase={kb} canAssign={isActive} />
        </CardContent>
      </Card>

      {/* 関係情報(GraphRAG)の俯瞰。展開時のみ subgraph を取得。 */}
      <KnowledgeBaseGraphView knowledgeBaseId={kb.id} />

      {/* 項目抽出で取り出す項目の定義(#548)。無ければ全体の既定を使う。構築設定として、構築フローの直前に置く(#555)。 */}
      <KnowledgeBaseExtractionFields knowledgeBaseId={kb.id} editable={isActive} />

      {/* 3 層モデル: 文書の処理レシピ(分割/parser)は文書側の責務。KB はスコープのみで、
          構築の既定パイプライン図だけ参考表示する(per-KB 取込上書き UI は撤去)。 */}
      <KnowledgeBasePipelineCanvas config={kb.effective_adapter_config ?? kb.adapter_config} />

      {/* このナレッジ単体で検索の手応えを確認する(業務ビュー不要)。構築の設定(関係情報・抽出する項目・
          パイプライン図)を見た後に、最後に検索で確かめる流れにするため、いちばん下に置く(#616)。 */}
      <KnowledgeBaseSearchTestPanel
        knowledgeBaseId={kb.id}
        indexedDocumentCount={kb.indexed_document_count}
        disabled={!isActive}
      />
    </KnowledgeBaseEditor>
  );
}

/**
 * 「文書を追加」（#600）。数千〜数万件の文書から、文書名で検索して複数を選び、一度に追加する。
 * 候補はサーバー側の検索（`q`）と追加読み込み（100 件ずつ）で取り、すでに所属している文書は「追加済み」で選べなくする。
 * 選んだ文書は検索語を変えても残り、「選択中だけ表示」で確かめられる。
 */
function DocumentAssignment({
  id,
  knowledgeBase,
  onClose,
}: {
  id: string;
  knowledgeBase: KnowledgeBaseDetail;
  onClose: (options?: { assigned?: boolean }) => void;
}) {
  const [candidateQuery, setCandidateQuery] = useState("");
  const candidates = useDocumentCandidates({ q: candidateQuery || undefined });
  const assign = useAssignDocumentsToKnowledgeBase();
  // 選んだ文書（検索語を変えても残す。名前を出すために文書ごと持つ）。
  const [selected, setSelected] = useState<Map<string, DocumentSummary>>(() => new Map());

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

  const toItem = (document: DocumentSummary): ListPickerItem => {
    const assigned = documentHasKnowledgeBase(document, knowledgeBase.id);
    return {
      key: document.id,
      label: document.file_name,
      textValue: document.file_name,
      description: [document.category_name, formatDateTime(document.uploaded_at)].filter(Boolean).join(" ・ "),
      meta: <StatusBadge status={document.status} />,
      disabled: assigned,
      disabledReason: assigned ? t("knowledgeBases.assignment.alreadyAssigned") : undefined,
    };
  };
  const items = documents.map(toItem);
  const selectedItems = [...selected.values()].map(toItem);
  const selectedKeys = new Set(selected.keys());
  const documentsById = new Map(documents.map((document) => [document.id, document]));

  const errorMessage = (error: unknown) =>
    error instanceof ApiError ? error.message : t("knowledgeBases.error.documents");

  const handleAssign = () => {
    const documentIds = [...selected.keys()];
    if (documentIds.length === 0) return;
    assign.mutate(
      { id: knowledgeBase.id, documentIds },
      {
        onSuccess: () => {
          setSelected(new Map());
          toast.success(t("knowledgeBases.toast.assigned"));
          onClose({ assigned: true });
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.assign")),
      }
    );
  };

  return (
    <ListPicker
      id={id}
      title={t("knowledgeBases.assignment.panelTitle")}
      headingLevel={3}
      description={t("knowledgeBases.assignment.hint")}
      label={t("knowledgeBases.assignment.listLabel")}
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
            const document = documentsById.get(target.key);
            if (document) copy.set(target.key, document);
          }
          return copy;
        })
      }
      onClearSelection={() => setSelected(new Map())}
      total={total}
      search={{
        id: "knowledge-base-add-document-search",
        label: t("knowledgeBases.assignment.search"),
        placeholder: t("knowledgeBases.assignment.searchPlaceholder"),
        value: candidateQuery,
        onSearch: setCandidateQuery,
        autoFocus: true,
      }}
      loading={candidates.isPending}
      refreshing={candidates.isFetching && candidates.isPlaceholderData}
      error={candidates.isError && !candidates.data ? errorMessage(candidates.error) : undefined}
      onRetry={() => void candidates.refetch()}
      hasMore={Boolean(candidates.hasNextPage)}
      loadingMore={candidates.isFetchingNextPage}
      loadMoreError={candidates.isFetchNextPageError ? errorMessage(candidates.error) : undefined}
      onLoadMore={() => void candidates.fetchNextPage()}
      labels={listPickerLabels({
        loading: t("knowledgeBases.assignment.loading"),
        emptyTitle: t("knowledgeBases.assignment.noOptions"),
        emptyHint: t("knowledgeBases.assignment.noOptionsHint"),
        noResultsTitle: t("knowledgeBases.assignment.noResults"),
        noResultsHint: t("knowledgeBases.assignment.noResultsHint"),
      })}
      actions={
        <FormActionBar
          ariaLabel={t("knowledgeBases.assignment.title")}
          primaryActions={[
            {
              id: "assign",
              label: t("knowledgeBases.assignment.submit", { count: formatNumber(selected.size) }),
              icon: FilePlus2,
              loading: assign.isPending,
              disabled: selected.size === 0,
              onClick: handleAssign,
              testId: "knowledge-base-add-documents-submit",
            },
          ]}
          secondaryActions={[
            {
              id: "close",
              label: t("knowledgeBases.assignment.close"),
              icon: X,
              disabled: assign.isPending,
              onClick: () => onClose(),
            },
          ]}
        />
      }
      testId="knowledge-base-add-documents"
    />
  );
}

function KnowledgeBaseDocuments({
  knowledgeBase,
  canAssign,
}: {
  knowledgeBase: KnowledgeBaseDetail;
  canAssign: boolean;
}) {
  const confirm = useConfirm();
  // 所属文書はサーバー側でページングする（件数の上限で打ち切らない）。一覧の中を文書名で検索できる（#600）。
  // 検索語とページは作業状態として残す（workspace-state.md）。別のナレッジベースに移ったら 1 ページ目・検索なしから。
  const [documentsPage, setDocumentsPage] = useWorkspaceState(
    "knowledgeBases.documentsPage",
    { knowledgeBaseId: knowledgeBase.id, offset: 0, q: "" },
    isKnowledgeBaseDocumentsPage
  );
  const sameKnowledgeBase = documentsPage.knowledgeBaseId === knowledgeBase.id;
  const offset = sameKnowledgeBase ? documentsPage.offset : 0;
  const query = sameKnowledgeBase ? documentsPage.q ?? "" : "";
  const setOffset = (next: number) =>
    setDocumentsPage({ knowledgeBaseId: knowledgeBase.id, offset: next, q: query });
  // 検索語が変わったら 1 ページ目へ戻す（page-archetypes.md「一覧の絞り込みの検索」5）。
  const setQuery = (next: string) => setDocumentsPage({ knowledgeBaseId: knowledgeBase.id, offset: 0, q: next });
  const documents = useDocuments({
    knowledge_base_id: knowledgeBase.id,
    q: query || undefined,
    limit: DEFAULT_PAGE_SIZE,
    offset,
  });
  const remove = useRemoveDocumentFromKnowledgeBase();
  const [assigning, setAssigning] = useState(false);
  const assignButtonRef = useRef<HTMLButtonElement | null>(null);
  const page = documents.data;
  // 外した結果いまのページが空になったら、最後のページへ戻す（空の案内を出さない）。
  const outOfRange = Boolean(page && page.offset === offset && page.items.length === 0 && offset > 0);
  const lastPageOffset =
    page && page.total > 0 ? Math.floor((page.total - 1) / DEFAULT_PAGE_SIZE) * DEFAULT_PAGE_SIZE : 0;
  const movingToLastPage = outOfRange && lastPageOffset !== offset;
  if (movingToLastPage) setOffset(lastPageOffset);
  const total = page?.total ?? 0;

  const closeAssignment = () => {
    setAssigning(false);
    // 閉じたら起点の「文書を追加」へフォーカスを戻す（位置を失わない）。
    requestAnimationFrame(() => assignButtonRef.current?.focus());
  };

  const handleRemove = async (document: DocumentSummary) => {
    const ok = await confirm({
      title: t("knowledgeBases.confirm.remove.title"),
      description: t("knowledgeBases.confirm.remove.description", {
        fileName: document.file_name,
        name: knowledgeBase.name,
      }),
      confirmLabel: t("knowledgeBases.actions.remove"),
      tone: "warning",
    });
    if (!ok) return;
    remove.mutate(
      { knowledgeBaseId: knowledgeBase.id, documentId: document.id },
      {
        onSuccess: () => toast.success(t("knowledgeBases.toast.removed")),
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.remove")),
      }
    );
  };

  return (
    <div className="space-y-3">
      {/* 一覧のツールバー: 左に検索、右に一覧への操作（page-archetypes.md「一覧のツールバー」）。 */}
      <ListToolbar
        search={
          <SearchField
            id="knowledge-base-documents-search"
            label={t("knowledgeBases.documents.search")}
            labelHidden
            placeholder={t("knowledgeBases.documents.searchPlaceholder")}
            value={query}
            onSearch={setQuery}
            clearLabel={t("common.clearSearch")}
            resultCountLabel={t("common.searchResultCount", { count: formatNumber(total) })}
          />
        }
        actions={
          canAssign ? (
            <Button
              ref={assignButtonRef}
              type="button"
              variant="secondary"
              icon={FilePlus2}
              aria-expanded={assigning}
              aria-controls={assigning ? "knowledge-base-add-documents" : undefined}
              onClick={() => (assigning ? closeAssignment() : setAssigning(true))}
              data-testid="knowledge-base-add-documents-toggle"
            >
              {t("knowledgeBases.assignment.title")}
            </Button>
          ) : undefined
        }
        testId="knowledge-base-documents-toolbar"
      />
      {canAssign && assigning ? (
        <DocumentAssignment id="knowledge-base-add-documents" knowledgeBase={knowledgeBase} onClose={closeAssignment} />
      ) : null}
      {documents.isError ? (
        <ErrorState
          message={
            documents.error instanceof ApiError
              ? documents.error.message
              : t("knowledgeBases.error.documents")
          }
          onRetry={() => void documents.refetch()}
        />
      ) : documents.isPending || movingToLastPage ? (
        <KnowledgeBaseDocumentsSkeleton />
      ) : documents.data.items.length > 0 ? (
        <>
          <ul
            className="bounded-scroll-area divide-y divide-border rounded-md border border-border"
            aria-busy={documents.isPlaceholderData || undefined}
            data-testid="knowledge-base-documents-list"
          >
            {documents.data.items.map((document) => (
              <KnowledgeBaseDocumentRow
                key={document.id}
                document={document}
                onRemove={() => void handleRemove(document)}
                removing={remove.isPending && remove.variables?.documentId === document.id}
              />
            ))}
          </ul>
          <ListPagination
            {...offsetPagination({
              offset,
              limit: DEFAULT_PAGE_SIZE,
              total,
              count: documents.data.items.length,
            })}
            onPageChange={(next) => setOffset(offsetForPage(next, DEFAULT_PAGE_SIZE))}
            testId="knowledge-base-documents-pagination"
          />
        </>
      ) : query ? (
        <EmptyState
          title={t("knowledgeBases.documents.noResults.title")}
          hint={t("knowledgeBases.documents.noResults.hint")}
          action={<ClearActionButton label={t("common.clearSearch")} onClick={() => setQuery("")} />}
        />
      ) : (
        <EmptyState
          title={t("knowledgeBases.documents.empty.title")}
          hint={t("knowledgeBases.documents.empty.hint")}
        />
      )}
    </div>
  );
}

/** 所属文書 1 行。3 層モデルでは KB はスコープのみ。レシピ/チャンク構成は文書詳細で扱う。 */
function KnowledgeBaseDocumentRow({
  document,
  onRemove,
  removing,
}: {
  document: DocumentSummary;
  onRemove: () => void;
  removing: boolean;
}) {
  // 文書の詳細を開けない利用者（KB の権限だけ）には、名前をリンクにしない（#303）。
  const canOpenDetail = canOpenDocumentDetail(useAuth().hasPermission);
  return (
    <li className="flex items-center gap-2 px-3 py-2">
      <Files className="size-4 shrink-0 text-fg-muted" aria-hidden />
      {canOpenDetail ? (
        <Link
          to={`${APP_ROUTES.documents}/${document.id}`}
          className="min-w-0 flex-1 truncate text-sm font-medium text-accent-fg hover:underline"
          title={document.file_name}
        >
          {document.file_name}
        </Link>
      ) : (
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-fg" title={document.file_name}>
          {document.file_name}
        </span>
      )}
      <RowActionMenu
        actions={[
          {
            id: "remove",
            label: t("knowledgeBases.actions.remove"),
            icon: Unlink,
            loading: removing,
            onSelect: onRemove,
          },
        ]}
        ariaLabel={t("common.objectActions.aria", { name: document.file_name })}
        loading={removing}
        testId={`knowledge-base-document-actions-${document.id}`}
      />
    </li>
  );
}

function documentHasKnowledgeBase(document: DocumentSummary, knowledgeBaseId: string) {
  return document.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId);
}

function KnowledgeBaseDocumentsSkeleton() {
  return (
    <TimedLoadingState
      label={t("knowledgeBases.documents.loading")}
      operationKey="knowledge-base-documents-load"
      testId="knowledge-base-documents-loading"
    >
      <ListSkeleton rowClassName="h-11" />
    </TimedLoadingState>
  );
}

interface KnowledgeBaseDocumentsPage {
  knowledgeBaseId: string;
  offset: number;
  /** 所属文書の検索語（#600）。前の版で保存した値には無い。 */
  q?: string;
}

function isKnowledgeBaseDocumentsPage(value: unknown): value is KnowledgeBaseDocumentsPage {
  const page = value as KnowledgeBaseDocumentsPage;
  return (
    typeof page === "object" &&
    page !== null &&
    typeof page.knowledgeBaseId === "string" &&
    Number.isInteger(page.offset) &&
    page.offset >= 0 &&
    (page.q === undefined || typeof page.q === "string")
  );
}
