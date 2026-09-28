import { History, Trash2 } from "lucide-react";
import { useState } from "react";

import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DEFAULT_PAGE_SIZE,
  type EntityAction,
  FormStatus,
  ObjectActionBar,
  Pagination,
  Skeleton,
  StatusBadge,
  useConfirm,
  TimedLoadingState,
  ListSkeleton,
} from "@engchina/production-ready-ui";

import { useAuth } from "@/components/security/AuthProvider";
import { ApiError } from "@/lib/api";
import { confidenceVariant } from "@/lib/docrag-answer";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { CAPABILITY_PERMISSIONS } from "@/lib/permissions";
import { toast } from "@/lib/toast";
import {
  useAnswerRecordSettings,
  useDeleteDocragAnswer,
  useDocragAnswer,
  useDocragAnswers,
} from "@/lib/queries";
import { useWorkspaceState } from "@/lib/workspace-state";

import { CitationCard } from "./CitationCard";
import { DocragAnswerPanel } from "./DocragAnswerPanel";

/** 回答履歴のページ（作業状態。業務ビューを変えたら 1 ページ目に戻す）。 */
interface HistoryPageState {
  businessViewId: string;
  page: number;
}

function isHistoryPageState(value: unknown): value is HistoryPageState {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.businessViewId === "string" &&
    typeof record.page === "number" &&
    Number.isInteger(record.page) &&
    record.page >= 1
  );
}

/**
 * 業務ビューの保存済み DocRAG 回答(rag_poc の回答 JSON 相当)を開き直す。
 * 一覧はサーバー側でページングし（総件数つき）、共通の Pagination で移動する（#304）。
 * 表示するのは自分の回答だけ（SYSTEM_ADMIN と rag.feedback.manage はすべての利用者の回答）。
 */
export function DocragAnswerHistory({
  businessViewId,
}: {
  businessViewId: string;
}) {
  const canSeeAll = useAuth().hasPermission(CAPABILITY_PERMISSIONS.feedbackManage);
  const [pageState, setPageState] = useWorkspaceState<HistoryPageState | null>(
    "search.historyPage",
    null,
    (value): value is HistoryPageState | null => value === null || isHistoryPageState(value),
  );
  const requestedPage = pageState?.businessViewId === businessViewId ? pageState.page : 1;
  const offset = (requestedPage - 1) * DEFAULT_PAGE_SIZE;
  const list = useDocragAnswers(businessViewId, { limit: DEFAULT_PAGE_SIZE, offset });
  const retention = useAnswerRecordSettings().data?.retention_days;
  const [selected, setSelected] = useState<string | null>(null);
  const page = list.data;
  const answers = page?.items ?? [];
  const total = page?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));
  // 削除や保存期間の経過でいまのページが空になったら、最後のページへ戻す（空の案内を出さない）。
  const movingToLastPage = Boolean(
    page && page.offset === offset && answers.length === 0 && requestedPage > totalPages,
  );
  if (movingToLastPage) setPageState({ businessViewId, page: totalPages });
  const setPage = (next: number) => {
    setSelected(null);
    setPageState({ businessViewId, page: next });
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History size={16} className="text-accent-fg" aria-hidden />
          {t("search.history.title")}
        </CardTitle>
        <CardDescription>
          {t(canSeeAll ? "search.history.descriptionAll" : "search.history.description")}
          {retention === undefined
            ? null
            : ` ${
                retention > 0
                  ? t("search.history.retention", { days: retention })
                  : t("search.history.retentionUnlimited")
              }`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {list.isPending || movingToLastPage ? (
          <TimedLoadingState
            label={t("search.history.loading")}
            operationKey="search-history-load"
            framed={false}
            testId="search-history-loading"
          >
            {/* 1 ページ分の行の高さを予約する（読み込み後に表示がずれない）。 */}
            <ListSkeleton rows={3} rowClassName="h-14" className="gap-1" />
          </TimedLoadingState>
        ) : null}
        {list.isError ? (
          <FormStatus
            tone="danger"
            message={
              list.error instanceof ApiError
                ? list.error.message
                : t("search.history.loadError")
            }
          />
        ) : null}
        {list.isSuccess && total === 0 ? (
          <p className="text-xs text-fg-muted">{t("search.history.empty")}</p>
        ) : null}
        {answers.length > 0 ? (
          <>
            <ul className="space-y-1" aria-label={t("search.history.title")}>
              {answers.map((item) => (
                <li key={item.trace_id}>
                  <Button
                    size="sm"
                    variant={selected === item.trace_id ? "secondary" : "ghost"}
                    className="h-auto w-full flex-wrap justify-start py-1.5 text-left [&>span]:whitespace-normal!"
                    aria-pressed={selected === item.trace_id}
                    onClick={() =>
                      setSelected(
                        selected === item.trace_id ? null : item.trace_id,
                      )
                    }
                  >
                    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                      <span className="break-words text-sm text-fg">
                        {item.rewritten_question || item.question}
                      </span>
                      <span className="tnum text-xs text-fg-muted">
                        {formatDateTime(item.created_at)} ·{" "}
                        {t(`search.history.surface.${item.surface}`)}
                      </span>
                    </span>
                    {item.confidence ? (
                      <StatusBadge
                        variant={confidenceVariant(item.confidence)}
                        label={t("search.docrag.confidence", {
                          value: item.confidence,
                        })}
                      />
                    ) : null}
                  </Button>
                </li>
              ))}
            </ul>
            <Pagination
              page={requestedPage}
              totalPages={totalPages}
              onPageChange={setPage}
              summary={t("pager.range", {
                start: formatNumber(offset + 1),
                end: formatNumber(offset + answers.length),
                total: formatNumber(total),
              })}
              prevLabel={t("pager.prev")}
              nextLabel={t("pager.next")}
              ariaLabel={t("search.history.pagination")}
              testId="docrag-answer-history-pagination"
            />
          </>
        ) : null}
        {selected ? (
          <SavedDocragAnswer
            // 回答ごとに作り直し、前に開いた回答の削除エラーなどを持ち越さない（#285）。
            key={selected}
            traceId={selected}
            businessViewId={businessViewId}
            onDeleted={() => setSelected(null)}
          />
        ) : null}
      </CardContent>
    </Card>
  );
}

/** 保存済み DocRAG 回答 1 件(質問・回答・根拠パネル・引用)。 */
export function SavedDocragAnswer({
  traceId,
  businessViewId,
  showAnswer = true,
  onDeleted,
}: {
  traceId: string;
  businessViewId: string;
  showAnswer?: boolean;
  onDeleted?: () => void;
}) {
  const detail = useDocragAnswer(traceId);
  const remove = useDeleteDocragAnswer();
  const confirm = useConfirm();

  async function handleDelete() {
    const confirmed = await confirm({
      title: t("search.history.deleteTitle"),
      description: t("search.history.deleteDescription"),
      confirmLabel: t("common.delete"),
      tone: "danger",
    });
    if (!confirmed) return;
    remove.mutate(traceId, {
      onSuccess: () => {
        toast.success(t("search.history.deleted"));
        onDeleted?.();
      },
    });
  }

  if (detail.isPending) {
    return (
      <TimedLoadingState
        label={t("search.history.detailLoading")}
        operationKey="search-history-detail-load"
        framed={false}
        testId="search-history-detail-loading"
      >
        <Skeleton className="h-24 w-full" />
      </TimedLoadingState>
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <FormStatus
        tone="danger"
        message={
          detail.error instanceof ApiError
            ? detail.error.message
            : t("search.history.loadError")
        }
      />
    );
  }
  const record = detail.data;
  // 保存された回答 1 件の操作。危険な操作だけなので ObjectActionBar の「その他の操作」に入り、
  // 確定は確認ダイアログで行う（buttons.md §5.1）。
  const actions: EntityAction[] = [
    {
      id: "delete",
      label: t("search.history.delete"),
      icon: Trash2,
      tone: "danger",
      disabled: remove.isPending,
      loading: remove.isPending,
      testId: `docrag-answer-delete-${record.trace_id}`,
      onSelect: () => void handleDelete(),
    },
  ];
  return (
    <div className="space-y-3 rounded-md border border-border bg-surface-sunken p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 flex-1 break-words text-xs text-fg-muted">
          {showAnswer
            ? t("search.history.question", { question: record.question })
            : t("search.history.savedAt", { value: formatDateTime(record.created_at) })}
        </p>
        <ObjectActionBar
          actions={actions}
          ariaLabel={t("common.objectActions.aria", { name: t("search.history.objectName") })}
          moreLabel={t("common.objectActions.more")}
          testId="docrag-answer-actions"
        />
      </div>
      {showAnswer ? (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">
          {record.answer}
        </p>
      ) : null}
      <DocragAnswerPanel
        docrag={record.docrag}
        traceId={record.evaluation_available ? record.trace_id : null}
        evaluation={record.evaluation}
      />
      {record.citations.length > 0 ? (
        <ul className="space-y-2">
          {record.citations.map((chunk, index) => (
            <CitationCard
              key={chunk.chunk_id}
              chunk={chunk}
              index={index}
              traceId={record.trace_id}
              businessViewId={businessViewId}
              sourceSurface={record.surface}
            />
          ))}
        </ul>
      ) : null}
      {remove.isError ? (
        <FormStatus
          tone="danger"
          message={
            remove.error instanceof ApiError
              ? remove.error.message
              : t("search.history.deleteError")
          }
        />
      ) : null}
    </div>
  );
}
