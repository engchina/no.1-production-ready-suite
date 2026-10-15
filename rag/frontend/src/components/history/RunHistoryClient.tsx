"use client";

/**
 * 実行履歴（`/history`・`/history/<run_id>`。#1890・#1955）。チャットと RAG 検索の実行を一覧し、全幅の詳細で
 * 回答・根拠・回答の検証・処理の経過を確かめる。
 *
 * - 型は 3 製品で共通（UX 契約 run-history.md）。一覧は `RunHistoryToolbar`（検索 → 状態 → 種類 → 期間 → 実行者）
 *   → `RunHistoryTable` → `OffsetPagination`、詳細は `RunDetailLayout`（概要 → 回答 → 処理の経過 → 根拠 →
 *   フィードバック → 技術的な詳細）を縦に 1 枚で並べる。RAG が持つのはデータの取得と節の中身（回答・根拠）だけ。
 * - 詳細は URL の run_id を唯一の情報源にし、戻ると一覧の検索・絞り込み・ページ・開いた行を保つ（workspace-state.md）。
 * - 見え方は backend が決める（system_admin は全員。それ以外は自分が実行した実行と、自分の会話で
 *   他の人〔system_admin〕が実行した実行。#1889・#1954）。「実行者」の列と絞り込みは、他の人の実行が
 *   見える利用者（system_admin と、他の人が実行した行を持つ利用者）だけに出す。
 */

import {
  Banner,
  ButtonLink,
  type DataTableColumn,
  DEFAULT_PAGE_SIZE,
  DataTable,
  InfoTip,
  OffsetPagination,
  PageBody,
  PageHeader,
  RUN_EXECUTOR_FILTERS,
  RUN_PERIODS,
  RunDetailLayout,
  RunDetailSection,
  RunDetailSkeleton,
  RunFactList,
  type RunExecutorFilter,
  type RunFact,
  type RunHistoryItem,
  type RunPeriod,
  type RunStatusFilter,
  RunHistoryTable,
  RunHistoryToolbar,
  RunSummary as RunSummaryCard,
  RunTimeline,
  SearchField,
  StatusBadge,
  TableSkeleton,
  TimedLoadingState,
  ClearActionButton,
  Card,
  formatRunDateTime,
  useActionPending,
} from "@production-ready/ui";
import { ExternalLink, MessagesSquare, RefreshCw } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "@/components/security/AuthProvider";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { AnswerDetailsPanel } from "@/components/search/AnswerDetailsPanel";
import { AnswerText } from "@/components/search/AnswerText";
import { CitationList } from "@/components/search/CitationList";
import { answerStageLabel } from "@/lib/answer-progress";
import { citationCountLabel } from "@/lib/chunk-metadata";
import { evidenceGroups } from "@/lib/evidence-groups";
import { api, type RunDetail, type RunFeedback, type RunSummary } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { isPageSize, listCountProps } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { useListTotal, useRunHistory, useRunHistoryDetail } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { useWorkspaceState } from "@/lib/workspace-state";

import {
  RUN_STATUS_OPTIONS,
  RUN_SURFACE_FILTERS,
  runConversationOwner,
  runExecutorLabel,
  runExecutorParam,
  runOutcomeView,
  runProfileLabel,
  runStartedAt,
  runStatusLabels,
  runStatusParam,
  runSurfaceLabel,
  runTimelineStepLabels,
  runTimelineSteps,
  runVerificationView,
  toRunStatus,
  type RunSurfaceFilter,
} from "./run-history-view";

const QUERY_MAX_LENGTH = 200;

interface RunHistoryView {
  q: string;
  status: RunStatusFilter;
  surface: RunSurfaceFilter;
  period: RunPeriod;
  /** 実行者の絞り込み（#1954。既定はすべて）。 */
  executor: RunExecutorFilter;
  offset: number;
  /** 1 ページの件数（表示件数。#1891）。 */
  limit: number;
  /** 最後に詳細を開いた実行（戻ったときに行に印を付ける）。 */
  selected: string | null;
}

const INITIAL_VIEW: RunHistoryView = {
  q: "",
  status: "all",
  surface: "all",
  period: "all",
  executor: "all",
  offset: 0,
  limit: DEFAULT_PAGE_SIZE,
  selected: null,
};

const STATUS_FILTERS: readonly string[] = ["all", ...RUN_STATUS_OPTIONS];

function isRunHistoryView(value: unknown): value is RunHistoryView {
  const view = value as RunHistoryView;
  return (
    typeof view === "object" &&
    view !== null &&
    typeof view.q === "string" &&
    STATUS_FILTERS.includes(view.status) &&
    (RUN_SURFACE_FILTERS as readonly string[]).includes(view.surface) &&
    (RUN_PERIODS as readonly string[]).includes(view.period) &&
    (RUN_EXECUTOR_FILTERS as readonly string[]).includes(view.executor) &&
    Number.isInteger(view.offset) &&
    view.offset >= 0 &&
    isPageSize(view.limit) &&
    (view.selected === null || typeof view.selected === "string")
  );
}

export function runDetailPath(runId: string): string {
  return `${APP_ROUTES.history}/${encodeURIComponent(runId)}`;
}

/** 一覧の行 → 共通の行の形。 */
function toItem(run: RunSummary): RunHistoryItem {
  return {
    id: run.run_id,
    title: run.question || t("history.question.withheld"),
    target: runProfileLabel(run),
    href: runDetailPath(run.run_id),
    status: toRunStatus(run.status),
    kind: runSurfaceLabel(run.surface),
    executor: { name: runExecutorLabel(run), isSelf: run.executed_by_me },
    startedAt: runStartedAt(run),
    durationMs: run.elapsed_ms,
  };
}

/** 一覧（`/history`）。 */
export function RunHistoryClient() {
  const navigate = useNavigate();
  const seesAllUsers = useAuth().user?.is_system_admin === true;
  // 検索語・絞り込み・ページ・開いた行は、詳細へ移って戻っても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("history.view", INITIAL_VIEW, isRunHistoryView);
  const query = useRunHistory({
    q: view.q || undefined,
    surface: view.surface === "all" ? undefined : view.surface,
    status: runStatusParam(view.status),
    executor: runExecutorParam(view.executor),
    period: view.period,
    limit: view.limit,
    offset: view.offset,
  });
  const manualRefresh = useActionPending();
  const page = query.data;
  const items = page?.items ?? [];
  const filtered =
    Boolean(view.q) ||
    view.status !== "all" ||
    view.surface !== "all" ||
    view.period !== "all" ||
    view.executor !== "all";
  // 件数の「N 件（全 M 件）」の M（条件を外した、見える範囲の全件）。絞り込み中だけ 1 件ぶんの取得で数える（#1891）。
  const allTotal = useListTotal(
    "run-history",
    {},
    async () => (await api.listRuns({ limit: 1, offset: 0 })).total,
    filtered
  );
  // 一般の利用者に、他の人が実行した行（自分の会話で system_admin が実行した行）があるか（#1954）。
  // あるときだけ「実行者」の列と絞り込みを出す（system_admin は常に出す）。
  const othersTotal = useListTotal(
    "run-history",
    { executor: "others" },
    async () => (await api.listRuns({ limit: 1, offset: 0, executor: "others" })).total,
    !seesAllUsers
  );
  const showExecutor = seesAllUsers || view.executor !== "all" || (othersTotal.data ?? 0) > 0;
  // 条件を変えたら 1 ページ目へ戻す（変わらない Enter・blur ではページを保つ）。
  const update = (patch: Partial<Pick<RunHistoryView, "q" | "status" | "surface" | "period" | "executor">>) =>
    setView((current) => {
      const next = { ...current, ...patch };
      const changed =
        next.q !== current.q ||
        next.status !== current.status ||
        next.surface !== current.surface ||
        next.period !== current.period ||
        next.executor !== current.executor;
      return changed ? { ...next, offset: 0 } : current;
    });
  const clearFilters = () =>
    setView((current) => ({ ...INITIAL_VIEW, limit: current.limit, selected: current.selected }));
  const open = (run: RunSummary) => {
    setView((current) => ({ ...current, selected: run.run_id }));
    navigate(runDetailPath(run.run_id));
  };
  const statusLabels = runStatusLabels();

  return (
    <div>
      <PageHeader
        wide
        title={t("nav.history")}
        subtitle={t("history.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("history.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => query.refetch()),
          },
        ]}
      />
      <PageBody wide className="grid grid-cols-1 gap-5">
        <p className="text-sm text-fg-muted" data-testid="run-history-scope">
          {seesAllUsers ? t("history.scope.all") : t("history.scope.own")}{" "}
          {t("history.retention")}
        </p>
        {/* 一覧のツールバー: 検索 → 状態 → 種類 → 期間 → 実行者（run-history.md「一覧」。#1933 の FilterSelect）。 */}
        <RunHistoryToolbar
          search={
            <SearchField
              id="run-history-search"
              label={t("history.search.label")}
              labelHidden
              value={view.q}
              onSearch={(value) => update({ q: value })}
              clearLabel={t("common.clearSearch")}
              resultCountLabel={
                page ? t("common.searchResultCount", { count: formatNumber(page.total) }) : ""
              }
              maxLength={QUERY_MAX_LENGTH}
              placeholder={t("history.search.placeholder")}
            />
          }
          searchActive={Boolean(view.q)}
          status={{
            value: view.status,
            onChange: (value) => update({ status: value }),
            options: RUN_STATUS_OPTIONS,
          }}
          kind={{
            value: view.surface,
            onChange: (value) => update({ surface: value as RunSurfaceFilter }),
            options: RUN_SURFACE_FILTERS.map((value) => ({
              value,
              label: value === "all" ? t("history.filter.all") : runSurfaceLabel(value),
            })),
          }}
          period={{ value: view.period, onChange: (value) => update({ period: value }) }}
          executor={showExecutor ? { value: view.executor, onChange: (value) => update({ executor: value }) } : undefined}
          onClearFilters={clearFilters}
          count={listCountProps({ matchedTotal: page?.total, filtered, allTotal: allTotal.data })}
          labels={{ status: statusLabels }}
          testId="run-history-toolbar"
        />

        {query.isError ? (
          <ApiErrorState
            error={query.error}
            fallback={t("history.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <TimedLoadingState
            label={t("history.loading")}
            operationKey="run-history-load"
            testId="run-history-loading"
          >
            <TableSkeleton columns={4} />
          </TimedLoadingState>
        ) : (page?.total ?? 0) > 0 ? (
          <div className="grid gap-2">
            <RunHistoryTable<RunSummary>
              rows={items}
              toItem={toItem}
              onOpen={open}
              showExecutor={showExecutor}
              extraColumns={runExtraColumns()}
              selectedRunId={view.selected}
              // 件数が減って保存したページが範囲外になったら OffsetPagination が最後のページへ戻す。
              loading={items.length === 0}
              labels={{ table: t("history.list.aria"), status: statusLabels }}
              testId="run-history"
            />
            <OffsetPagination
              offset={view.offset}
              limit={view.limit}
              total={page?.total ?? 0}
              count={items.length}
              onPageChange={(_page, nextOffset) =>
                setView((current) => ({ ...current, offset: nextOffset }))
              }
              onPageSizeChange={(nextLimit) =>
                setView((current) => ({ ...current, limit: nextLimit, offset: 0 }))
              }
              labels={paginationLabels()}
              ariaLabel={t("history.paginationLabel")}
              testId="run-history-pagination"
            />
          </div>
        ) : (
          <Card>
            {filtered ? (
              <EmptyState
                title={t("history.noMatch.title")}
                hint={t("history.noMatch.hint")}
                action={<ClearActionButton label={t("history.clearFilters")} onClick={clearFilters} />}
              />
            ) : (
              <EmptyState
                title={t("history.empty.title")}
                hint={seesAllUsers ? t("history.empty.hint") : t("history.empty.ownHint")}
              />
            )}
          </Card>
        )}
      </PageBody>
    </div>
  );
}

/** RAG の列（回答の対応・回答の検証）。所要時間の右に置き、狭い画面では出さない（詳細の概要で見る）。 */
function runExtraColumns(): DataTableColumn<RunSummary>[] {
  return [
    {
      key: "outcome",
      header: t("history.col.outcome"),
      className: "whitespace-nowrap",
      render: (run) => <StatusBadge {...runOutcomeView(run)} />,
    },
    {
      key: "verification",
      header: (
        <span className="inline-flex items-center gap-1">
          {t("history.col.verification")}
          <InfoTip label={t("history.verification.helpLabel")} content={t("history.verification.help")} />
        </span>
      ),
      className: "whitespace-nowrap",
      render: (run) => {
        const view = runVerificationView(run.verification);
        return view ? <StatusBadge {...view} /> : <span className="text-fg-muted">{t("history.notRecorded")}</span>;
      },
    },
  ];
}

/** 詳細（`/history/<run_id>`）。 */
export function RunHistoryDetailClient({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const detail = useRunHistoryDetail(runId);
  const manualRefresh = useActionPending();

  return (
    <div>
      <PageHeader
        wide
        title={t("history.detail.title")}
        subtitle={t("history.detail.subtitle")}
        back={{
          label: t("common.backToList"),
          ariaLabel: t("editor.backToListOf", { list: t("nav.history") }),
          onClick: () => navigate(APP_ROUTES.history),
          testId: "run-history-back",
        }}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("history.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => detail.refetch()),
          },
        ]}
      />
      <PageBody wide className="grid grid-cols-1 gap-5">
        {detail.isError ? (
          <ApiErrorState
            error={detail.error}
            fallback={t("history.detail.loadError")}
            onRetry={() => void detail.refetch()}
          />
        ) : detail.isPending ? (
          <TimedLoadingState
            label={t("history.detail.loading")}
            operationKey="run-history-detail-load"
            testId="run-history-detail-loading"
          >
            <RunDetailSkeleton />
          </TimedLoadingState>
        ) : (
          <RunDetailView run={detail.data} />
        )}
      </PageBody>
    </div>
  );
}

function RunDetailView({ run }: { run: RunDetail }) {
  // AI に渡した箇所のまとまり（#2097）。見出しの件数と「まとまりごと」の表示で同じものを使う。
  const evidence = evidenceGroups(run.citations, run.answer_diagnostics);
  const { user, hasPermission } = useAuth();
  const seesAllUsers = user?.is_system_admin === true;
  // 引用箇所のプレビューは文書を読む権限（backend の `_DOCUMENT_VIEW`）がある利用者だけ。
  const canPreview = [
    MENU_PERMISSIONS.upload,
    MENU_PERMISSIONS.fileList,
    MENU_PERMISSIONS.search,
    MENU_PERMISSIONS.chat,
    MENU_PERMISSIONS.knowledgeBases,
  ].some((permission) => hasPermission(permission));
  // 会話を開けるのは会話の持ち主と system_admin だけ（会話の API の見え方。#1889・#1928）。
  // 持ち主の会話で system_admin が実行した行も、持ち主は会話を開ける（#1954）。
  const canOpenConversation = seesAllUsers || run.owned_by_me === true;
  const chatHref =
    canOpenConversation &&
    run.conversation_id &&
    run.search_answer_profile_id &&
    hasPermission(MENU_PERMISSIONS.chat)
      ? `${APP_ROUTES.chat}?${new URLSearchParams({
          search_answer_profile_id: run.search_answer_profile_id,
          conversation_id: run.conversation_id,
        }).toString()}`
      : null;
  const verification = runVerificationView(run.verification);
  const statusLabels = runStatusLabels();
  const succeeded = run.status === "success";

  const extraFacts: RunFact[] = [
    { id: "citations", label: t("history.detail.citations"), value: formatNumber(run.citation_count) },
  ];
  if (run.status === "error") {
    extraFacts.push({
      id: "errorStage",
      label: t("history.detail.errorStage"),
      value: run.error_stage ? answerStageLabel(run.error_stage) : t("history.notRecorded"),
    });
  }

  const technicalFacts: RunFact[] = ([
    run.conversation_id ? { id: "conversation", label: t("history.detail.conversationId"), value: run.conversation_id, mono: true } : null,
    run.message_id ? { id: "message", label: t("history.detail.messageId"), value: run.message_id, mono: true } : null,
    run.confidence ? { id: "confidence", label: t("history.detail.confidence"), value: run.confidence } : null,
    run.needs_human_review != null
      ? {
          id: "review",
          label: t("history.detail.needsReview"),
          value: run.needs_human_review ? t("history.detail.needsReview.yes") : t("history.detail.needsReview.no"),
        }
      : null,
    run.error_type ? { id: "errorType", label: t("history.detail.errorType"), value: run.error_type, mono: true } : null,
  ] as (RunFact | null)[]).filter((fact): fact is RunFact => fact !== null);

  const timelineSteps = runTimelineSteps(run);

  return (
    <RunDetailLayout
      testId="run-history-detail"
      summary={
        <RunSummaryCard
          testId="run-history-summary"
          status={toRunStatus(run.status)}
          labels={{ summary: t("history.detail.summary"), status: statusLabels }}
          badges={
            <>
              <StatusBadge {...runOutcomeView(run)} />
              {verification ? <StatusBadge {...verification} /> : null}
            </>
          }
          question={run.question || t("history.question.withheld")}
          startedAt={runStartedAt(run)}
          finishedAt={run.elapsed_ms != null ? run.created_at : null}
          durationMs={run.elapsed_ms}
          kind={runSurfaceLabel(run.surface)}
          executor={{ name: runExecutorLabel(run), isSelf: run.executed_by_me }}
          owner={runConversationOwner(run)}
          target={{ label: t("history.detail.profile"), value: runProfileLabel(run) }}
          model={run.answer_generated ? run.model_id || t("history.detail.modelDefault") : null}
          runId={run.run_id}
          extraFacts={extraFacts}
          alert={
            run.status === "blocked" ? (
              <Banner severity="warning">{t("history.result.blocked")}</Banner>
            ) : run.status === "error" ? (
              <Banner severity="danger">{t("history.result.error")}</Banner>
            ) : null
          }
          actions={
            chatHref ? (
              <ButtonLink
                to={chatHref}
                linkComponent={Link}
                icon={MessagesSquare}
                aria-label={t("history.detail.openChatLabel")}
                testId="run-history-open-chat"
              >
                {t("history.detail.openChat")}
              </ButtonLink>
            ) : null
          }
        />
      }
      answer={
        run.answer_generated && succeeded ? (
          <RunDetailSection title={t("history.result.answer")} testId="run-history-answer">
            {run.answer_available && run.answer ? (
              <AnswerText text={run.answer} citations={canPreview ? run.citations : []} />
            ) : (
              <p className="text-sm text-fg-muted" data-testid="run-history-answer-missing">
                {t("history.result.answerMissing")}
              </p>
            )}
          </RunDetailSection>
        ) : null
      }
      timeline={
        <RunDetailSection title={t("history.process.title")} testId="run-history-timeline">
          {timelineSteps.length ? (
            <RunTimeline
              steps={timelineSteps}
              totalDurationMs={run.elapsed_ms}
              labels={{ stepStatus: runTimelineStepLabels() }}
            />
          ) : (
            <p className="text-sm text-fg-muted">{t("history.process.empty")}</p>
          )}
        </RunDetailSection>
      }
      evidence={
        succeeded ? (
          <RunDetailSection
            title={
              // 回答を作った実行は AI に渡した件数と内訳、検索だけの実行は検索結果の件数（RAG 検索と同じ。#2040）。
              run.answer_generated
                ? citationCountLabel(run.citations, evidence?.length ?? null)
                : `${t("search.results")}（${run.citations.length}）`
            }
            testId="run-history-evidence"
          >
            {run.citations.length ? (
              <CitationList
                citations={run.citations}
                groups={evidence}
                idPrefix={`run-${run.run_id}`}
                listTestId="run-history-citations"
                cardProps={{ preview: canPreview }}
              />
            ) : (
              <p className="text-sm text-fg-muted">{t("history.result.noCitations")}</p>
            )}
            {/* 根拠（AI に渡した箇所の一覧・まとまりごと。#2097）を先、実行記録のパネルを後に並べる（#1417）。 */}
            {run.answer_diagnostics ? (
              <AnswerDetailsPanel diagnostics={run.answer_diagnostics} title={t("history.process.diagnostics")} />
            ) : null}
          </RunDetailSection>
        ) : null
      }
      feedback={
        run.feedback.length ? (
          <RunFeedbackSection feedback={run.feedback} canOpen={hasPermission(MENU_PERMISSIONS.feedback)} />
        ) : null
      }
      technical={
        technicalFacts.length ? (
          <RunDetailSection title={t("history.detail.technical")} collapsible testId="run-history-technical">
            <RunFactList facts={technicalFacts} />
          </RunDetailSection>
        ) : null
      }
    />
  );
}

function RunFeedbackSection({ feedback, canOpen }: { feedback: RunFeedback[]; canOpen: boolean }) {
  const columns: DataTableColumn<RunFeedback>[] = [
    {
      key: "target",
      header: t("history.feedback.col.target"),
      rowHeader: true,
      render: (item) =>
        item.target_type === "answer"
          ? t("history.feedback.target.answer")
          : t("history.feedback.target.citation"),
    },
    {
      key: "rating",
      header: t("history.feedback.col.rating"),
      render: (item) => (
        <StatusBadge
          variant={item.rating === "helpful" ? "success" : "warning"}
          label={
            item.rating === "helpful" ? t("history.feedback.helpful") : t("history.feedback.notHelpful")
          }
        />
      ),
    },
    {
      key: "createdAt",
      header: t("history.col.createdAt"),
      className: "tnum text-fg-muted",
      render: (item) => formatRunDateTime(item.created_at),
    },
  ];
  if (canOpen) {
    columns.push({
      key: "open",
      header: t("history.feedback.col.actions"),
      align: "right",
      render: (item) => (
        <ButtonLink
          to={`${APP_ROUTES.feedback}?${new URLSearchParams({ feedback: item.feedback_id, period: "all" }).toString()}`}
          linkComponent={Link}
          size="sm"
          variant="ghost"
          trailingIcon={ExternalLink}
          testId={`run-history-feedback-open-${item.feedback_id}`}
        >
          {t("history.feedback.open")}
        </ButtonLink>
      ),
    });
  }
  return (
    <RunDetailSection
      title={t("history.feedback.title")}
      description={t("history.feedback.description")}
      meta={<StatusBadge variant="neutral" label={t("history.feedback.count", { count: formatNumber(feedback.length) })} />}
      testId="run-history-feedback"
    >
      <DataTable
        columns={columns}
        rows={feedback}
        getRowKey={(item) => item.feedback_id}
        ariaLabel={t("history.feedback.title")}
        tableClassName="w-full min-w-[32rem] text-sm"
      />
    </RunDetailSection>
  );
}

