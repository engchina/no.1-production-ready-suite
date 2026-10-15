"use client";

/**
 * 実行履歴（`/history`・`/history/<run_id>`。#1890）。チャットと RAG 検索の実行を一覧し、全幅の詳細で
 * 回答・根拠・回答の検証・処理の経過を確かめる。
 *
 * - 型は Agent の実行履歴と同じ「一覧 → 全幅の詳細」（page-archetypes.md の B 型の例外。長い回答・根拠・
 *   経過を読むため）。詳細は URL の run_id を唯一の情報源にし、戻ると一覧の検索・絞り込み・ページを保つ。
 * - 一覧は NL2SQL の基準（`DataTable` の `stickyHeader` + `visibleRows`、サーバー側の `OffsetPagination`、
 *   `ListToolbar` の左に `SearchField`）。
 * - 見え方は backend が決める（system_admin は全員。それ以外は自分が実行した実行と、自分の会話で
 *   他の人〔system_admin〕が実行した実行。#1889・#1954）。「実行者」の列は全員に出し、実行した人が
 *   自分でない行には質問の下に「〈名前〉が実行」を添える。ツールバーで実行者を絞れる。
 */

import {
  Banner,
  ButtonLink,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ClearActionButton,
  DataTable,
  type DataTableColumn,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  InfoTip,
  ListToolbar,
  OffsetPagination,
  PageBody,
  PageHeader,
  RowTitleButton,
  SearchField,
  SelectField,
  Skeleton,
  StatusBadge,
  TableSkeleton,
  TabPanel,
  Tabs,
  TimedLoadingState,
  ToggleChip,
  useActionPending,
} from "@production-ready/ui";
import { ExternalLink, MessagesSquare, RefreshCw, UserRound } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { useAuth } from "@/components/security/AuthProvider";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { AnswerDetailsPanel } from "@/components/search/AnswerDetailsPanel";
import { AnswerText } from "@/components/search/AnswerText";
import { CitationCard } from "@/components/search/CitationCard";
import { answerStageLabel } from "@/lib/answer-progress";
import {
  api,
  type RunDetail,
  type RunExecutorFilter,
  type RunFeedback,
  type RunStage,
  type RunStep,
  type RunSummary,
} from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { isPageSize, listCountProps } from "@/lib/list-paging";
import { paginationLabels } from "@/lib/pagination-labels";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { useListTotal, useRunHistory, useRunHistoryDetail } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { useWorkspaceState } from "@/lib/workspace-state";

import {
  formatRunElapsed,
  RUN_EXECUTOR_FILTERS,
  RUN_STATUS_FILTERS,
  RUN_SURFACE_FILTERS,
  runConversationOwnerLabel,
  runExecutedByNote,
  runExecutorFilterLabel,
  runExecutorLabel,
  runOutcomeView,
  runProfileLabel,
  runStatusView,
  runSurfaceLabel,
  runVerificationView,
  type RunStatusFilter,
  type RunSurfaceFilter,
} from "./run-history-view";

const QUERY_MAX_LENGTH = 200;

interface RunHistoryView {
  q: string;
  surface: RunSurfaceFilter;
  status: RunStatusFilter;
  /** 実行者の絞り込み（#1954。既定はすべて）。 */
  executor: RunExecutorFilter;
  offset: number;
  /** 1 ページの件数（表示件数。#1891）。 */
  limit: number;
}

const INITIAL_VIEW: RunHistoryView = {
  q: "",
  surface: "all",
  status: "all",
  executor: "all",
  offset: 0,
  limit: DEFAULT_PAGE_SIZE,
};

function isRunHistoryView(value: unknown): value is RunHistoryView {
  const view = value as RunHistoryView;
  return (
    typeof view === "object" &&
    view !== null &&
    typeof view.q === "string" &&
    (RUN_SURFACE_FILTERS as readonly string[]).includes(view.surface) &&
    (RUN_STATUS_FILTERS as readonly string[]).includes(view.status) &&
    (RUN_EXECUTOR_FILTERS as readonly string[]).includes(view.executor) &&
    Number.isInteger(view.offset) &&
    view.offset >= 0 &&
    isPageSize(view.limit)
  );
}

export function runDetailPath(runId: string): string {
  return `${APP_ROUTES.history}/${encodeURIComponent(runId)}`;
}

/** 一覧（`/history`）。 */
export function RunHistoryClient() {
  const navigate = useNavigate();
  const seesAllUsers = useAuth().user?.is_system_admin === true;
  // 検索語・絞り込み・ページは、詳細へ移って戻っても再読込しても残す（workspace-state.md）。
  const [view, setView] = useWorkspaceState("history.view", INITIAL_VIEW, isRunHistoryView);
  const query = useRunHistory({
    q: view.q || undefined,
    surface: view.surface === "all" ? undefined : view.surface,
    status: view.status === "all" ? undefined : view.status,
    executor: view.executor,
    limit: view.limit,
    offset: view.offset,
  });
  const manualRefresh = useActionPending();
  const page = query.data;
  const items = page?.items ?? [];
  const filtered =
    Boolean(view.q) || view.surface !== "all" || view.status !== "all" || view.executor !== "all";
  // 件数の「N 件（全 M 件）」の M（条件を外した、見える範囲の全件）。絞り込み中だけ 1 件ぶんの取得で数える（#1891）。
  const allTotal = useListTotal(
    "run-history",
    {},
    async () => (await api.listRuns({ limit: 1, offset: 0 })).total,
    filtered
  );
  // 条件を変えたら 1 ページ目へ戻す（変わらない Enter・blur ではページを保つ）。
  const update = (patch: Partial<Omit<RunHistoryView, "offset" | "limit">>) =>
    setView((current) => {
      const next = { ...current, ...patch };
      const changed =
        next.q !== current.q ||
        next.surface !== current.surface ||
        next.status !== current.status ||
        next.executor !== current.executor;
      return changed ? { ...next, offset: 0 } : current;
    });

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
        {/* 一覧のツールバー: 左に検索、その右に種類と状態の絞り込み（page-archetypes.md「一覧のツールバー」）。 */}
        <ListToolbar
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
          filters={
            <>
              {/* eslint-disable-next-line no-restricted-syntax -- 実行履歴のツールバーは #1955 で共通の部品（RunHistoryToolbar）に作り直す。それまで今の形のまま（#1933）。 */}
              <SelectField
                id="run-history-surface"
                label={t("history.filter.surface")}
                value={view.surface}
                options={RUN_SURFACE_FILTERS.map((value) => ({
                  value,
                  label:
                    value === "all" ? t("history.filter.all") : runSurfaceLabel(value),
                }))}
                onValueChange={(value) => update({ surface: value as RunSurfaceFilter })}
                // 短い選択（種類）は sm（#1891 の幅の基準）。
                width="sm"
                className="[&_label]:text-xs"
                buttonClassName="bg-surface"
              />
              {/* 実行者（#1954）: 持ち主の会話で他の人（system_admin）が実行した行を見分ける。 */}
              <SelectField
                id="run-history-executor"
                label={t("history.filter.executor")}
                value={view.executor}
                options={RUN_EXECUTOR_FILTERS.map((value) => ({
                  value,
                  label: runExecutorFilterLabel(value),
                }))}
                onValueChange={(value) => update({ executor: value as RunExecutorFilter })}
                width="sm"
                className="[&_label]:text-xs"
                buttonClassName="bg-surface"
              />
              <div
                className="flex flex-wrap items-center gap-1"
                role="group"
                aria-label={t("history.filter.status.aria")}
              >
                {RUN_STATUS_FILTERS.map((value) => (
                  // eslint-disable-next-line no-restricted-syntax -- 実行履歴のツールバーは #1955 で共通の部品（RunHistoryToolbar）に作り直す。それまで今の形のまま（#1933）。
                  <ToggleChip
                    key={value}
                    selected={view.status === value}
                    onClick={() => update({ status: value })}
                  >
                    {value === "all" ? t("history.filter.all") : runStatusView(value).label}
                  </ToggleChip>
                ))}
              </div>
            </>
          }
          count={listCountProps({ matchedTotal: page?.total, filtered, allTotal: allTotal.data })}
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
            <TableSkeleton columns={7} />
          </TimedLoadingState>
        ) : (page?.total ?? 0) > 0 ? (
          <div className="grid gap-2">
            <DataTable<RunSummary>
              columns={runColumns({ onOpen: (run) => navigate(runDetailPath(run.run_id)) })}
              rows={items}
              // 件数が減って保存したページが範囲外になったら OffsetPagination が最後のページへ戻す。
              loading={items.length === 0}
              getRowKey={(run) => run.run_id}
              onRowClick={(run) => navigate(runDetailPath(run.run_id))}
              rowProps={(run) => ({
                className: INFORMATION_TABLE_ROW_CLASS,
                "data-testid": `run-history-row-${run.run_id}`,
              })}
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              scrollAriaLabel={t("history.list.scroll")}
              scrollTestId="run-history-scroll-region"
              tableClassName="w-full min-w-[68rem] text-sm"
              ariaLabel={t("history.list.aria")}
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
                action={
                  <ClearActionButton
                    label={t("history.clearFilters")}
                    onClick={() => setView(INITIAL_VIEW)}
                  />
                }
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

function runColumns({ onOpen }: { onOpen: (run: RunSummary) => void }): DataTableColumn<RunSummary>[] {
  return [
    {
      key: "question",
      header: t("history.col.question"),
      rowHeader: true,
      className: "max-w-[28rem]",
      render: (run) => {
        const executedBy = runExecutedByNote(run);
        return (
          <div className="grid min-w-0 gap-1">
            <RowTitleButton
              title={run.question || t("history.question.withheld")}
              href={runDetailPath(run.run_id)}
              maxLines={2}
              subtitle={`${runSurfaceLabel(run.surface)} / ${runProfileLabel(run)}`}
              // 修飾キーの無いクリックは画面内で開く（Ctrl / ⌘ + クリックは新しいタブ。#583）。
              onClick={() => onOpen(run)}
              data-testid={`run-history-open-${run.run_id}`}
            />
            {/* 実行した人が自分でない行（持ち主の会話で system_admin が実行した行など。#1954）。
                色だけに頼らず、アイコンと文で示す。 */}
            {executedBy ? (
              <p
                className="flex items-center gap-1 break-words text-xs text-fg-muted [overflow-wrap:anywhere]"
                data-testid={`run-history-executed-by-${run.run_id}`}
              >
                <UserRound size={14} aria-hidden="true" className="shrink-0" />
                {executedBy}
              </p>
            ) : null}
          </div>
        );
      },
    },
    {
      key: "outcome",
      header: t("history.col.outcome"),
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
      render: (run) => <VerificationBadge run={run} />,
    },
    {
      key: "status",
      header: t("history.col.status"),
      render: (run) => <StatusBadge {...runStatusView(run.status)} />,
    },
    {
      key: "elapsed",
      header: t("history.col.elapsed"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (run) => formatRunElapsed(run.elapsed_ms),
    },
    {
      key: "createdAt",
      header: t("history.col.createdAt"),
      className: "tnum text-fg-muted",
      render: (run) => formatDateTime(run.created_at),
    },
    {
      // 実行者（#1954）。全員に出す（自分の会話で他の人が実行した行を見分ける）。
      key: "executor",
      header: t("history.col.executor"),
      className: "max-w-[12rem] break-words text-fg-muted",
      render: (run) => runExecutorLabel(run),
    },
  ];
}

function VerificationBadge({ run }: { run: Pick<RunSummary, "verification"> }) {
  const view = runVerificationView(run.verification);
  if (!view) return <span className="text-fg-muted">{t("history.notRecorded")}</span>;
  return <StatusBadge {...view} />;
}

/** 詳細（`/history/<run_id>`）。 */
export function RunHistoryDetailClient({ runId }: { runId: string }) {
  const navigate = useNavigate();
  const detail = useRunHistoryDetail(runId);

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
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-11 w-full" />
            <Skeleton className="h-72 w-full" />
          </TimedLoadingState>
        ) : (
          <RunDetailView run={detail.data} />
        )}
      </PageBody>
    </div>
  );
}

type DetailTab = "result" | "process" | "feedback";

function RunDetailView({ run }: { run: RunDetail }) {
  const { user, hasPermission } = useAuth();
  const seesAllUsers = user?.is_system_admin === true;
  const [tab, setTab] = useState<DetailTab>("result");
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
  const conversationOwner = runConversationOwnerLabel(run);

  return (
    <section className="min-w-0 space-y-5" aria-label={t("history.detail.title")}>
      <Card className="min-w-0" data-testid="run-history-summary">
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{t("history.detail.summary")}</CardTitle>
              <StatusBadge {...runStatusView(run.status)} />
              <StatusBadge {...runOutcomeView(run)} />
              {verification ? <StatusBadge {...verification} /> : null}
            </div>
            <CardDescription className="whitespace-pre-wrap break-words text-sm text-fg [overflow-wrap:anywhere]">
              {run.question || t("history.question.withheld")}
            </CardDescription>
          </div>
          {chatHref ? (
            <ButtonLink
              to={chatHref}
              linkComponent={Link}
              icon={MessagesSquare}
              aria-label={t("history.detail.openChatLabel")}
              testId="run-history-open-chat"
            >
              {t("history.detail.openChat")}
            </ButtonLink>
          ) : null}
        </CardHeader>
        <CardContent>
          <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 xl:grid-cols-3">
            <Field label={t("history.detail.surface")}>{runSurfaceLabel(run.surface)}</Field>
            <Field label={t("history.detail.profile")}>{runProfileLabel(run)}</Field>
            <Field label={t("history.detail.createdAt")}>{formatDateTime(run.created_at)}</Field>
            <Field label={t("history.detail.elapsed")}>{formatRunElapsed(run.elapsed_ms)}</Field>
            <Field label={t("history.detail.citations")}>{formatNumber(run.citation_count)}</Field>
            {run.answer_generated ? (
              <Field label={t("history.detail.model")}>
                {run.model_id || t("history.detail.modelDefault")}
              </Field>
            ) : null}
            <Field label={t("history.detail.executor")}>
              <span data-testid="run-history-detail-executor">{runExecutorLabel(run)}</span>
            </Field>
            {conversationOwner != null ? (
              <Field label={t("history.detail.conversationOwner")}>
                <span data-testid="run-history-detail-conversation-owner">{conversationOwner}</span>
              </Field>
            ) : null}
            {run.status === "error" ? (
              <>
                <Field label={t("history.detail.errorStage")}>
                  {run.error_stage ? answerStageLabel(run.error_stage) : t("history.notRecorded")}
                </Field>
                <Field label={t("history.detail.errorType")}>
                  <span className="font-mono text-xs">{run.error_type || t("history.notRecorded")}</span>
                </Field>
              </>
            ) : null}
            <Field label={t("history.detail.runId")}>
              <span className="break-all font-mono text-xs">{run.run_id}</span>
            </Field>
          </dl>
        </CardContent>
      </Card>

      <Tabs
        value={tab}
        onChange={(next) => setTab(next as DetailTab)}
        ariaLabel={t("history.detail.tabs")}
        items={[
          { id: "result", label: t("history.tab.result") },
          { id: "process", label: t("history.tab.process") },
          { id: "feedback", label: t("history.tab.feedback", { count: run.feedback.length }) },
        ]}
      />
      <TabPanel id="result" value={tab} className="space-y-5">
        <RunResult run={run} canPreview={canPreview} />
      </TabPanel>
      <TabPanel id="process" value={tab} className="space-y-5">
        <RunProcess stages={run.stages} steps={run.steps} />
      </TabPanel>
      <TabPanel id="feedback" value={tab}>
        <RunFeedbackList feedback={run.feedback} canOpen={hasPermission(MENU_PERMISSIONS.feedback)} />
      </TabPanel>
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="mt-0.5 break-words text-fg [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

function RunResult({ run, canPreview }: { run: RunDetail; canPreview: boolean }) {
  return (
    <>
      {run.status === "blocked" ? (
        <Banner severity="warning">{t("history.result.blocked")}</Banner>
      ) : run.status === "error" ? (
        <Banner severity="danger">{t("history.result.error")}</Banner>
      ) : null}
      {run.answer_generated && run.status === "success" ? (
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("history.result.answer")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {run.answer_available && run.answer ? (
              <AnswerText text={run.answer} citations={canPreview ? run.citations : []} />
            ) : (
              <p className="text-sm text-fg-muted" data-testid="run-history-answer-missing">
                {t("history.result.answerMissing")}
              </p>
            )}
          </CardContent>
        </Card>
      ) : !run.answer_generated && run.status === "success" ? (
        <p className="text-sm text-fg-muted">{t("history.result.noAnswer")}</p>
      ) : null}
      {run.status === "success" ? (
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("history.result.citations", { count: run.citations.length })}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {run.citations.length ? (
              <ul className="space-y-2" data-testid="run-history-citations">
                {run.citations.map((chunk, index) => (
                  <CitationCard
                    key={`${chunk.chunk_id}-${index}`}
                    chunk={chunk}
                    index={index}
                    preview={canPreview}
                  />
                ))}
              </ul>
            ) : (
              <p className="text-sm text-fg-muted">{t("history.result.noCitations")}</p>
            )}
            {/* 根拠（引用の一覧）を先、根拠の構成・実行記録のパネルを後に並べる（#1417）。 */}
            {run.answer_diagnostics ? (
              <AnswerDetailsPanel
                diagnostics={run.answer_diagnostics}
                title={t("history.process.diagnostics")}
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}

function RunProcess({ stages, steps }: { stages: RunStage[]; steps: RunStep[] }) {
  const stageColumns: DataTableColumn<RunStage & { index: number }>[] = [
    {
      key: "stage",
      header: t("history.process.col.stage"),
      rowHeader: true,
      render: (stage) => answerStageLabel(stage.stage),
    },
    {
      key: "outcome",
      header: t("history.process.col.result"),
      render: (stage) => <StatusBadge {...stageOutcomeView(stage.outcome)} />,
    },
    {
      key: "elapsed",
      header: t("history.process.col.elapsed"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (stage) => formatRunElapsed(stage.elapsed_ms),
    },
  ];
  const stepColumns: DataTableColumn<RunStep & { index: number }>[] = [
    {
      key: "name",
      header: t("history.process.col.stage"),
      rowHeader: true,
      className: "break-words",
      render: (step) => step.name || t("history.notRecorded"),
    },
    {
      key: "status",
      header: t("history.process.col.result"),
      className: "text-fg-muted",
      render: (step) => step.status || t("history.notRecorded"),
    },
    {
      key: "elapsed",
      header: t("history.process.col.elapsed"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (step) =>
        formatRunElapsed(step.elapsed_seconds == null ? null : step.elapsed_seconds * 1000),
    },
    {
      key: "llmCalls",
      header: t("history.process.col.llmCalls"),
      align: "right",
      className: "tnum text-fg-muted",
      render: (step) => formatNumber(step.llm_calls ?? 0),
    },
  ];
  return (
    <>
      <Card className="min-w-0">
        <CardHeader>
          <CardTitle>{t("history.process.stages")}</CardTitle>
          <CardDescription>{t("history.process.stagesDescription")}</CardDescription>
        </CardHeader>
        <CardContent>
          {stages.length ? (
            <DataTable
              columns={stageColumns}
              rows={stages.map((stage, index) => ({ ...stage, index }))}
              getRowKey={(stage) => `${stage.stage}-${stage.index}`}
              ariaLabel={t("history.process.stages")}
              tableClassName="w-full text-sm"
              rowProps={() => ({ "data-testid": "run-history-stage" })}
            />
          ) : (
            <p className="text-sm text-fg-muted">{t("history.process.empty")}</p>
          )}
        </CardContent>
      </Card>
      {steps.length ? (
        <Card className="min-w-0">
          <CardHeader>
            <CardTitle>{t("history.process.steps")}</CardTitle>
            <CardDescription>{t("history.process.stepsDescription")}</CardDescription>
          </CardHeader>
          <CardContent>
            <DataTable
              columns={stepColumns}
              rows={steps.map((step, index) => ({ ...step, index }))}
              getRowKey={(step) => `${step.name}-${step.index}`}
              ariaLabel={t("history.process.steps")}
              tableClassName="w-full min-w-[32rem] text-sm"
              rowProps={() => ({ "data-testid": "run-history-step" })}
            />
          </CardContent>
        </Card>
      ) : null}
    </>
  );
}

function stageOutcomeView(outcome: string) {
  if (outcome === "success") return { variant: "success" as const, label: t("history.process.outcome.success") };
  if (outcome === "cancelled") return { variant: "warning" as const, label: t("history.process.outcome.cancelled") };
  return { variant: "danger" as const, label: t("history.process.outcome.error") };
}

function RunFeedbackList({ feedback, canOpen }: { feedback: RunFeedback[]; canOpen: boolean }) {
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
      render: (item) => formatDateTime(item.created_at),
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
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("history.feedback.title")}</CardTitle>
        <CardDescription>{t("history.feedback.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        {feedback.length ? (
          <DataTable
            columns={columns}
            rows={feedback}
            getRowKey={(item) => item.feedback_id}
            ariaLabel={t("history.feedback.title")}
            tableClassName="w-full min-w-[32rem] text-sm"
          />
        ) : (
          <p className="text-sm text-fg-muted">{t("history.feedback.empty")}</p>
        )}
      </CardContent>
    </Card>
  );
}
