"use client";

import { FilePlus2, Files, Unlink } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ListPagination } from "@/components/ListPagination";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { EditorTargetState } from "@/components/layout/EntityLayout";
import { useAuth } from "@/components/security/AuthProvider";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DEFAULT_PAGE_SIZE,
  FormSkeleton,
  FormStatus,
  ListSkeleton,
  offsetForPage,
  offsetPagination,
  RowActionMenu,
  SelectField,
  type SelectFieldOption,
  SearchField,
  TimedLoadingState,
} from "@engchina/production-ready-ui";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentSummary,
  type KnowledgeBaseDetail,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useAssignDocumentsToKnowledgeBase,
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

// 追加候補として一度に取得する文書数（API の上限 200 以内）。これを超える文書は名前で検索して選ぶ。
const CANDIDATE_LIMIT = 100;

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
      {/* 所属文書: 追加ツールバー(左寄せ)+ 一覧。追加操作は対象一覧の直上に置く。 */}
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
          {isActive ? (
            <DocumentAssignment knowledgeBase={kb} />
          ) : (
            <p className="rounded-md border border-border bg-surface-sunken px-3 py-2 text-sm text-fg-muted">
              {t("knowledgeBases.detail.archivedHint")}
            </p>
          )}

          <KnowledgeBaseDocuments knowledgeBase={kb} />
        </CardContent>
      </Card>

      {/* このナレッジ単体で検索の手応えを確認(業務ビュー不要)。文書追加→検証→構築設定 の流れ。 */}
      <KnowledgeBaseSearchTestPanel
        knowledgeBaseId={kb.id}
        indexedDocumentCount={kb.indexed_document_count}
        disabled={!isActive}
      />

      {/* 関係情報(GraphRAG)の俯瞰。展開時のみ subgraph を取得。 */}
      <KnowledgeBaseGraphView knowledgeBaseId={kb.id} />

      {/* 項目抽出で取り出す項目の定義(#548)。無ければ全体の既定を使う。処理の流れ（文書の追加 → 確認 →
          構築設定）の構築設定として、構築フローの直前に置く(#555)。 */}
      <KnowledgeBaseExtractionFields knowledgeBaseId={kb.id} editable={isActive} />

      {/* 3 層モデル: 文書の処理レシピ(分割/parser)は文書側の責務。KB はスコープのみで、
          構築の既定パイプライン図だけ参考表示する(per-KB 取込上書き UI は撤去)。 */}
      <KnowledgeBasePipelineCanvas config={kb.effective_adapter_config ?? kb.adapter_config} />
    </KnowledgeBaseEditor>
  );
}

function DocumentAssignment({ knowledgeBase }: { knowledgeBase: KnowledgeBaseDetail }) {
  // 候補は新しい順に CANDIDATE_LIMIT 件まで。それより古い文書も選べるよう、文書名で検索して絞り込む。
  const [candidateQuery, setCandidateQuery] = useState("");
  const allDocuments = useDocuments({
    q: candidateQuery || undefined,
    limit: CANDIDATE_LIMIT,
    offset: 0,
  });
  const candidatesTruncated = (allDocuments.data?.total ?? 0) > CANDIDATE_LIMIT;
  const assign = useAssignDocumentsToKnowledgeBase();
  const [documentId, setDocumentId] = useState("");

  const options = useMemo(() => {
    const documents = allDocuments.data?.items ?? [];
    return documents.filter((document) => !documentHasKnowledgeBase(document, knowledgeBase.id));
  }, [allDocuments.data?.items, knowledgeBase.id]);

  const selectOptions = useMemo<SelectFieldOption[]>(
    () => options.map((document) => ({ value: document.id, label: document.file_name })),
    [options]
  );

  // 未選択か、選んでいた文書が候補から外れたときは、先頭の候補を選び直す（render 中に調整）。
  if (!documentId && options[0]) {
    setDocumentId(options[0].id);
  } else if (
    documentId &&
    options.length > 0 &&
    !options.some((document) => document.id === documentId)
  ) {
    setDocumentId(options[0].id);
  }

  const handleAssign = () => {
    if (!documentId) return;
    assign.mutate(
      { id: knowledgeBase.id, documentIds: [documentId] },
      {
        onSuccess: () => {
          setDocumentId("");
          toast.success(t("knowledgeBases.toast.assigned"));
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.assign")),
      }
    );
  };

  return (
    <div className="space-y-2">
      {/* 追加ツールバー: コンボボックスは幅制約し、追加ボタンを入力のすぐ隣へ左寄せ(右端に孤立させない)。 */}
      <div className="flex flex-wrap items-end gap-2">
        {/* 候補の文書を名前で絞る（入力に合わせて適用。#535）。 */}
        <SearchField
          id="knowledge-base-add-document-search"
          label={t("knowledgeBases.assignment.search")}
          placeholder={t("knowledgeBases.assignment.searchPlaceholder")}
          value={candidateQuery}
          onSearch={setCandidateQuery}
          clearLabel={t("common.clearSearch")}
          resultCountLabel={t("common.searchResultCount", { count: formatNumber(options.length) })}
          className="w-full min-w-0 sm:w-64"
        />
        <SelectField
          id="knowledge-base-add-document"
          label={t("knowledgeBases.assignment.title")}
          value={documentId}
          options={selectOptions}
          onValueChange={setDocumentId}
          placeholder={t("knowledgeBases.assignment.noOptions")}
          className="w-full min-w-0 sm:w-80"
        />
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={handleAssign}
          loading={assign.isPending}
          disabled={!documentId}
          className="shrink-0" icon={FilePlus2}>
          {t("knowledgeBases.actions.assign")}
        </Button>
      </div>
      {candidatesTruncated ? (
        <p className="text-xs text-fg-muted">
          {t("knowledgeBases.assignment.truncated", { count: formatNumber(CANDIDATE_LIMIT) })}
        </p>
      ) : null}
      {allDocuments.isError ? (
        <FormStatus
          tone="danger"
          message={
            allDocuments.error instanceof ApiError
              ? allDocuments.error.message
              : t("knowledgeBases.error.documents")
          }
        />
      ) : null}
    </div>
  );
}

function KnowledgeBaseDocuments({ knowledgeBase }: { knowledgeBase: KnowledgeBaseDetail }) {
  const confirm = useConfirm();
  // 所属文書はサーバー側でページングする（件数の上限で打ち切らない）。
  // ページは作業状態として残す（workspace-state.md）。別のナレッジベースに移ったら 1 ページ目から。
  const [documentsPage, setDocumentsPage] = useWorkspaceState(
    "knowledgeBases.documentsPage",
    { knowledgeBaseId: knowledgeBase.id, offset: 0 },
    isKnowledgeBaseDocumentsPage
  );
  const offset = documentsPage.knowledgeBaseId === knowledgeBase.id ? documentsPage.offset : 0;
  const setOffset = (next: number) => setDocumentsPage({ knowledgeBaseId: knowledgeBase.id, offset: next });
  const documents = useDocuments({
    knowledge_base_id: knowledgeBase.id,
    limit: DEFAULT_PAGE_SIZE,
    offset,
  });
  const remove = useRemoveDocumentFromKnowledgeBase();
  const page = documents.data;
  // 外した結果いまのページが空になったら、最後のページへ戻す（空の案内を出さない）。
  const outOfRange = Boolean(page && page.offset === offset && page.items.length === 0 && offset > 0);
  const lastPageOffset =
    page && page.total > 0 ? Math.floor((page.total - 1) / DEFAULT_PAGE_SIZE) * DEFAULT_PAGE_SIZE : 0;
  const movingToLastPage = outOfRange && lastPageOffset !== offset;
  if (movingToLastPage) setOffset(lastPageOffset);
  const total = page?.total ?? 0;

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
    <div className="space-y-2">
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
          <ul className="bounded-scroll-area divide-y divide-border rounded-md border border-border">
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
}

function isKnowledgeBaseDocumentsPage(value: unknown): value is KnowledgeBaseDocumentsPage {
  const page = value as KnowledgeBaseDocumentsPage;
  return (
    typeof page === "object" &&
    page !== null &&
    typeof page.knowledgeBaseId === "string" &&
    Number.isInteger(page.offset) &&
    page.offset >= 0
  );
}
