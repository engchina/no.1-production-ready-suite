"use client";

import { FilePlus2, Files, Save, Unlink } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "@/components/StateViews";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DEFAULT_PAGE_SIZE,
  FormStatus,
  ObjectActionBar,
  Pagination,
  RowActionMenu,
  SelectField,
  type SelectFieldOption,
  TextField,
} from "@engchina/production-ready-ui";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  DEFAULT_KNOWLEDGE_BASE_NAME,
  type DocumentSummary,
  type KnowledgeBaseDetail,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import {
  useAssignDocumentsToKnowledgeBase,
  useDocuments,
  useKnowledgeBase,
  useRemoveDocumentFromKnowledgeBase,
  useUpdateKnowledgeBase,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { KnowledgeBaseGraphView } from "./KnowledgeBaseGraphView";
import { KnowledgeBasePipelineCanvas } from "./KnowledgeBasePipelineCanvas";
import { KnowledgeBaseSearchTestPanel } from "./KnowledgeBaseSearchTestPanel";
import { KnowledgeBaseStatusPill } from "./KnowledgeBaseStatusPill";
import {
  DESCRIPTION_MAX_LENGTH,
  NAME_MAX_LENGTH,
  useKnowledgeBaseActions,
  validateKnowledgeBaseName,
} from "./knowledge-base-actions";

// 追加候補として一度に取得する文書数（API の上限 200 以内）。これを超える文書は名前で検索して選ぶ。
const CANDIDATE_LIMIT = 100;

/** ナレッジベース詳細ページ。概要・所属文書・構築設定(構築フロー + フォーム)を全幅で扱う。 */
export function KnowledgeBaseDetailClient({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const detail = useKnowledgeBase(knowledgeBaseId);
  // 概要の名前・説明をその場で編集する（#302）。
  const [editing, setEditing] = useState(false);
  // 一覧の行（RowActionMenu）と同じ操作の定義を ObjectActionBar に渡す（buttons.md §5.1）。
  // 編集は詳細だけに出す（一覧からは名前のリンクで詳細へ移る）。
  const knowledgeBaseActions = useKnowledgeBaseActions({ onEdit: () => setEditing(true) });

  if (detail.isPending) {
    return (
      <Card className="h-64 animate-pulse" role="status" aria-label={t("knowledgeBases.detail.loading")} />
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <ErrorState
        message={
          detail.error instanceof ApiError ? detail.error.message : t("knowledgeBases.error.load")
        }
        onRetry={() => void detail.refetch()}
      />
    );
  }

  const kb = detail.data;
  const isActive = kb.status === "ACTIVE";

  return (
    <div className="space-y-5">
      {/* 概要: 名称・状態・メトリクス(この KB が何か) */}
      <Card>
        <CardContent className="space-y-5 pt-6">
          {editing && isActive ? (
            <KnowledgeBaseEditForm knowledgeBase={kb} onDone={() => setEditing(false)} />
          ) : (
          <div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-3">
                <h1 className="min-w-0 truncate text-xl font-semibold text-fg">{kb.name}</h1>
                <KnowledgeBaseStatusPill status={kb.status} />
              </div>
              <ObjectActionBar
                actions={knowledgeBaseActions(kb)}
                ariaLabel={t("common.objectActions.aria", { name: kb.name })}
                moreLabel={t("common.objectActions.more")}
                testId="knowledge-base-detail-actions"
              />
            </div>
            {kb.description ? <p className="mt-1 text-sm text-fg-muted">{kb.description}</p> : null}
          </div>
          )}

          <div className="grid grid-cols-3 gap-2">
            <Metric label={t("knowledgeBases.metric.documents")} value={kb.document_count} />
            <Metric label={t("knowledgeBases.metric.indexed")} value={kb.indexed_document_count} />
            <Metric label={t("knowledgeBases.metric.errors")} value={kb.error_document_count} />
          </div>
        </CardContent>
      </Card>

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

      {/* 3 層モデル: 文書の処理レシピ(分割/parser)は文書側の責務。KB はスコープのみで、
          構築の既定パイプライン図だけ参考表示する(per-KB 取込上書き UI は撤去)。 */}
      <KnowledgeBasePipelineCanvas config={kb.effective_adapter_config ?? kb.adapter_config} />
    </div>
  );
}

/**
 * 名前・説明の編集フォーム（#302）。DEFAULT は改名できない（名前は読み取り専用で説明だけ送る）。
 * 同名（アーカイブ済みを含む）は backend が 409 と理由を返すので、名前の欄の下に出す。
 */
function KnowledgeBaseEditForm({
  knowledgeBase,
  onDone,
}: {
  knowledgeBase: KnowledgeBaseDetail;
  onDone: () => void;
}) {
  const update = useUpdateKnowledgeBase();
  const isDefault = knowledgeBase.name === DEFAULT_KNOWLEDGE_BASE_NAME;
  const [name, setName] = useState(knowledgeBase.name);
  const [description, setDescription] = useState(knowledgeBase.description ?? "");
  const [touched, setTouched] = useState(false);
  const dirty =
    name.trim() !== knowledgeBase.name ||
    description.trim() !== (knowledgeBase.description ?? "");
  // 保存していない変更があるときだけ離脱を確認する。
  useLeaveGuard(dirty);

  const nameError = !isDefault && touched ? validateKnowledgeBaseName(name) : null;
  const serverError = update.isError
    ? update.error instanceof ApiError
      ? update.error.message
      : t("knowledgeBases.error.update")
    : null;
  // 同名の KB がある（409）ときは名前の欄の下に理由を出す。それ以外はフォームの下に出す。
  const conflict = update.error instanceof ApiError && update.error.status === 409;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setTouched(true);
    if (!isDefault && validateKnowledgeBaseName(name)) return;
    if (!dirty) {
      onDone();
      return;
    }
    update.mutate(
      {
        id: knowledgeBase.id,
        payload: {
          ...(isDefault ? {} : { name: name.trim() }),
          description: description.trim() || null,
        },
      },
      {
        onSuccess: () => {
          toast.success(t("knowledgeBases.toast.updated"));
          onDone();
        },
      }
    );
  };

  return (
    <form
      onSubmit={handleSubmit}
      onKeyDown={(event) => {
        // 変更がなければ Esc で閉じる（変更があるときはキャンセルのボタンで明示的に閉じる）。
        if (event.key === "Escape" && !dirty && !update.isPending) onDone();
      }}
      className="space-y-4"
      aria-labelledby="knowledge-base-edit-title"
      data-testid="knowledge-base-edit-form"
    >
      <h1 id="knowledge-base-edit-title" className="text-xl font-semibold text-fg">
        {t("knowledgeBases.edit.title")}
      </h1>
      <div className="grid gap-3 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
        <TextField
            id="knowledge-base-edit-name"
            label={t("knowledgeBases.field.name")}
            required={!isDefault}
            requiredLabel={t("common.required")}
            value={name}
            onValueChange={setName}
            onBlur={() => setTouched(true)}
            readOnly={isDefault}
            aria-readonly={isDefault || undefined}
            helper={isDefault ? t("knowledgeBases.edit.defaultNameFixed") : undefined}
            error={nameError ?? (conflict && serverError ? serverError : undefined)}
            maxLength={NAME_MAX_LENGTH}
            autoFocus={!isDefault}
            inputClassName={isDefault ? "cursor-default text-fg-muted" : undefined}
          />
        <TextField
          id="knowledge-base-edit-description"
          label={t("knowledgeBases.field.description")}
          value={description}
          onValueChange={setDescription}
          maxLength={DESCRIPTION_MAX_LENGTH}
          autoFocus={isDefault}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button type="submit" icon={Save} loading={update.isPending}>
          {t("knowledgeBases.actions.save")}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={onDone}
          disabled={update.isPending}
        >
          {t("knowledgeBases.actions.cancel")}
        </Button>
        {serverError && !conflict ? <FormStatus tone="danger" message={serverError} /> : null}
      </div>
    </form>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-surface-sunken p-3">
      <p className="text-xs text-fg-muted">{label}</p>
      <p className="tnum mt-1 text-lg font-semibold text-fg">{formatNumber(value)}</p>
    </div>
  );
}

function DocumentAssignment({ knowledgeBase }: { knowledgeBase: KnowledgeBaseDetail }) {
  // 候補は新しい順に CANDIDATE_LIMIT 件まで。それより古い文書も選べるよう、文書名で検索して絞り込む。
  const [candidateSearch, setCandidateSearch] = useState("");
  const [candidateQuery, setCandidateQuery] = useState("");
  const allDocuments = useDocuments({
    q: candidateQuery || undefined,
    limit: CANDIDATE_LIMIT,
    offset: 0,
  });
  const candidatesTruncated = (allDocuments.data?.total ?? 0) > CANDIDATE_LIMIT;
  const applyCandidateSearch = () => setCandidateQuery(candidateSearch.trim());
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
        <TextField
          id="knowledge-base-add-document-search"
          type="search"
          label={t("knowledgeBases.assignment.search")}
          placeholder={t("knowledgeBases.assignment.searchPlaceholder")}
          value={candidateSearch}
          onValueChange={setCandidateSearch}
          onKeyDown={(event) => {
            if (event.key === "Enter") applyCandidateSearch();
          }}
          onBlur={applyCandidateSearch}
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
          buttonClassName="h-9"
        />
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={handleAssign}
          loading={assign.isPending}
          disabled={!documentId}
          className="h-9 shrink-0" icon={FilePlus2}>
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
  const [offset, setOffset] = useState(0);
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
  const totalPages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));

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
          <Pagination
            page={Math.floor(offset / DEFAULT_PAGE_SIZE) + 1}
            totalPages={totalPages}
            onPageChange={(next) => setOffset((next - 1) * DEFAULT_PAGE_SIZE)}
            summary={t("pager.range", {
              start: formatNumber(offset + 1),
              end: formatNumber(offset + documents.data.items.length),
              total: formatNumber(total),
            })}
            prevLabel={t("pager.prev")}
            nextLabel={t("pager.next")}
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
  return (
    <li className="flex items-center gap-2 px-3 py-2">
      <Files className="size-4 shrink-0 text-fg-muted" aria-hidden />
      <Link
        to={`${APP_ROUTES.documents}/${document.id}`}
        className="min-w-0 flex-1 truncate text-sm font-medium text-accent-fg hover:underline"
        title={document.file_name}
      >
        {document.file_name}
      </Link>
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
    <div className="space-y-2" role="status" aria-label={t("knowledgeBases.documents.loading")}>
      <div className="h-9 rounded-md bg-surface-sunken" />
      <div className="h-9 rounded-md bg-surface-sunken" />
      <div className="h-9 rounded-md bg-surface-sunken" />
    </div>
  );
}
