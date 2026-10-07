import { useWorkspaceState } from "@/components/WorkspaceState";
import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { useValuesChanged } from "@/lib/render-sync";
import {
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
  Clock3,
  Code2,
  Columns3,
  Database,
  History,
  LayoutList,
  MessageSquareText,
  RefreshCw,
  RotateCcw,
  Rows3,
} from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  Button,
  LoadMoreFooter,
  EmptyState,
  toast,
  StatusBadge,
  type StatusVariant,
  PageHeader,
  Tabs,
  PageBody,
  ProcessingIndicator,
  Skeleton,
  TimedLoadingState,
  INFORMATION_LIST_SCROLL_CLASS,
  SelectField,
} from "@engchina/production-ready-ui";
import { useAuth } from "@/features/security/AuthProvider";
import { canOpenRoute } from "@/features/security/route-permissions";

import { PageNotice } from "@/components/page-notice";
import { apiGet, isAbortError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { formatElapsedDuration as formatElapsed } from "@/lib/operationTiming";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import { DbManagementSearchField, DbObjectManagementPanelShell, DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import { QuestionText } from "../components/QuestionText";
import {
  sortHistory,
  type HistoryFeedbackFilter,
  type HistorySafetyFilter,
  type HistorySortKey,
  type HistorySortState,
} from "../historyManagementState";
import { engineLabel } from "../labels";
import { profileRecordDisplayLabel } from "../profileDisplay";
import { historyRerunUrl } from "../queryPrefillState";
import type { EngineTiming, HistoryData, HistoryItem, Nl2SqlEngine, StageTiming } from "../types";
import { userFeedbackRatingBadgeLabel } from "../feedbackLabels";

type HistoryDetailTab = "overview" | "sql";

function HistoryExecutor({ item, detailed = false }: { item: HistoryItem; detailed?: boolean }) {
  const { user } = useAuth();
  if (!user?.is_system_admin) return null;
  const uuid = item.actor_user_uuid?.trim();
  const loginId = item.actor_login_user_id?.trim();
  const name = item.actor_display_name?.trim();
  const nameIncludesLoginId = name?.startsWith(`${loginId}（`) && name.endsWith("）");
  const identity = !uuid ? t("history.actor.unrecorded")
    : loginId ? (name && name !== loginId ? (nameIncludesLoginId ? name : t("history.actor.identity", { name, loginId })) : loginId)
    : t("history.actor.missing", { uuid });
  return (
    <span className="grid min-w-0 gap-1 text-sm leading-relaxed text-fg [overflow-wrap:anywhere]" data-testid="history-executor">
      <span>{t("history.actor.label")}: {identity}</span>
      {detailed && uuid && loginId && <span className="text-fg-muted">{t("history.actor.uuid", { uuid })}</span>}
      {detailed && loginId && <span className="text-fg-muted">{t("history.actor.hint")}</span>}
    </span>
  );
}

function HistorySafetyHelp() {
  return (
    <section className="grid min-w-0 gap-2 rounded-md border border-border bg-surface p-3" aria-labelledby="history-safety-help-heading">
      <h2 id="history-safety-help-heading" className="text-base font-semibold text-fg">
        {t("history.safetyHelp.title")}
      </h2>
      <dl className="grid gap-3 md:grid-cols-2">
        {(["safe", "blocked"] as const).map((state) => (
          <div key={state} className="grid min-w-0 content-start gap-1">
            <dt><StatusBadge variant={state === "safe" ? "success" : "danger"} label={t(`nl2sql.safety.${state}`)} /></dt>
            <dd id={`history-safety-help-${state}`} className="text-sm leading-relaxed text-fg">
              {t(`history.safetyHelp.${state}`)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="text-sm leading-relaxed text-fg-muted">{t("history.safetyHelp.note")}</p>
    </section>
  );
}

function columnsLabel(item: HistoryItem) {
  if (item.result_columns.length === 0) return "—";
  return item.result_columns.join(", ");
}

function isKnownEngine(engine: string): engine is Nl2SqlEngine {
  return ["select_ai", "select_ai_agent", "enterprise_ai_direct"].includes(engine);
}

function engineTimingLabel(engine: string) {
  if (engine === "deterministic") return t("history.timing.engine.deterministic");
  if (isKnownEngine(engine)) return engineLabel(engine);
  return engine || t("history.timing.engine.unknown");
}

function stageTimingValue(stages: StageTiming[], stage: string) {
  return stages.find((item) => item.stage === stage)?.elapsed_ms ?? null;
}

function engineTimingStatusLabel(status: EngineTiming["status"]) {
  if (status === "success") return t("history.timing.status.success");
  if (status === "failed") return t("history.timing.status.failed");
  return t("history.timing.status.skipped");
}

function engineTimingStatusVariant(status: EngineTiming["status"]): StatusVariant {
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "warning";
}

function HistoryListSkeleton() {
  return (
    <TimedLoadingState
      label={t("history.list.loading")}
      operationKey="history-list-load"
      placement="panel"
      className="content-start"
      testId="history-list-loading"
      framed={false}
      // 初回の読込は PageHeader の「再読み込み」の loading がスピナーを出す（同じ処理のスピナーは 1 つ。
      // messaging §3.7、#416）。
      activityIcon="none"
    >
      <h2 id="history-grid-heading" className="sr-only">{t("history.list.title")}</h2>
      <Skeleton className="h-14" />
      <Skeleton className="h-28" />
      <div className="grid gap-2" data-testid="history-list-skeleton">
        {Array.from({ length: 6 }, (_, index) => (
          <Skeleton key={index} className="h-20" />
        ))}
      </div>
    </TimedLoadingState>
  );
}

function HistoryDetailSkeleton() {
  return (
    <TimedLoadingState
      label={t("history.detail.loading")}
      operationKey="history-detail-load"
      placement="panel"
      className="content-start"
      testId="history-detail-skeleton"
      framed={false}
      activityIcon="none"
    >
      <Skeleton className="h-20" />
      <Skeleton className="h-11" />
      <Skeleton className="h-72" />
    </TimedLoadingState>
  );
}

function HistorySortButton({
  label,
  sortKey,
  sort,
  onToggle,
}: {
  label: string;
  sortKey: HistorySortKey;
  sort: HistorySortState;
  onToggle: (key: HistorySortKey) => void;
}) {
  const active = sort.key === sortKey;
  const direction = active
    ? sort.direction === "asc"
      ? t("history.sort.asc")
      : t("history.sort.desc")
    : t("history.sort.inactive");
  const SortIcon = active ? (sort.direction === "asc" ? ArrowUp : ArrowDown) : ArrowDownUp;
  return (
    // 履歴は複数行のカード一覧で表ではないため、並べ替えは共有 Button のトグル（aria-pressed）で出す。
    <Button
      type="button"
      variant="ghost"
      size="sm"
      pressed={active}
      trailingIcon={SortIcon}
      aria-label={t("history.sort.button", { label, direction })}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
    </Button>
  );
}

function HistoryGrid({
  items,
  selectedId,
  search,
  feedbackFilter,
  safetyFilter,
  sort,
  onSearchChange,
  onFeedbackFilterChange,
  onSafetyFilterChange,
  onSortChange,
  onSelect,
  onClearFilters,
  loadedCount,
  total,
  hasMore,
  loadingMore,
  refreshing,
  unavailable,
  onLoadMore,
}: {
  items: HistoryItem[];
  selectedId: string;
  search: string;
  feedbackFilter: HistoryFeedbackFilter;
  safetyFilter: HistorySafetyFilter;
  sort: HistorySortState;
  onSearchChange: (value: string) => void;
  onFeedbackFilterChange: (value: HistoryFeedbackFilter) => void;
  onSafetyFilterChange: (value: HistorySafetyFilter) => void;
  onSortChange: (key: HistorySortKey) => void;
  onSelect: (item: HistoryItem) => void;
  onClearFilters: () => void;
  /** サーバから読込済みの件数(フィルタ前)。 */
  loadedCount: number;
  /** サーバ側の総件数(数えられないときは null)。 */
  total: number | null;
  hasMore: boolean;
  loadingMore: boolean;
  refreshing: boolean;
  /** 今の絞り込みの結果を取得できなかった（前の条件の一覧は出さない）。 */
  unavailable: boolean;
  onLoadMore: () => void;
}) {
  const count = total ?? items.length;
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="history-grid-heading">
      <DbObjectPanelHeader
        headingId="history-grid-heading"
        icon={History}
        title={t("history.list.title")}
        description={t("history.list.hint")}
        action={unavailable ? undefined : <StatusBadge icon={false} variant="info" label={t("history.list.count", { count })} />}
      />

      <div className="grid gap-2 rounded-md border border-border bg-surface-sunken p-3">
        <DbManagementSearchField
          label={t("history.search.label")}
          placeholder={t("history.search.placeholder")}
          value={search}
          onChange={onSearchChange}
        />
        <div
          className="grid grid-cols-1 gap-2 sm:[grid-template-columns:repeat(auto-fit,minmax(min(100%,10rem),1fr))]"
          data-testid="history-filter-grid"
        >
          <HistoryFilterSelect
            label={t("history.filter.feedback")}
            value={feedbackFilter}
            onChange={(value) => onFeedbackFilterChange(value as HistoryFeedbackFilter)}
            options={[
              ["all", t("history.filter.all")],
              ["unrated", t("history.feedback.none")],
              ["good", t("nl2sql.feedback.good")],
              ["bad", t("nl2sql.feedback.bad")],
            ]}
          />
          <HistoryFilterSelect
            label={t("history.filter.safety")}
            value={safetyFilter}
            onChange={(value) => onSafetyFilterChange(value as HistorySafetyFilter)}
            options={[
              ["all", t("history.filter.all")],
              ["safe", t("nl2sql.safety.safe")],
              ["blocked", t("nl2sql.safety.blocked")],
            ]}
          />
        </div>
      </div>

      {unavailable ? (
        // 取得の失敗を「条件に一致する履歴がありません」（空）と取り違えさせない。再試行はページ上部の通知（#912）。
        <div className="grid justify-items-center gap-3 rounded-md border border-border bg-surface-sunken p-4" data-testid="history-list-unavailable">
          <EmptyState title={t("history.unavailable.title")} hint={t("history.unavailable.hint")} />
        </div>
      ) : items.length === 0 ? (
        <div className="grid justify-items-center gap-3 rounded-md border border-border bg-surface-sunken p-4">
          <EmptyState title={t("history.noResults.title")} hint={t("history.noResults.hint")} />
          <Button type="button" variant="secondary" size="sm" onClick={onClearFilters}>
            {t("history.action.clearFilters")}
          </Button>
        </div>
      ) : (
        <div className="grid gap-2">
          <div
            className="overflow-hidden rounded-md border border-border bg-surface"
            data-testid="history-list-surface"
          >
            <div
              className="flex flex-wrap items-center gap-1 border-b border-border bg-surface-sunken px-2 py-1.5"
              role="group"
              aria-label={t("history.sort.label")}
            >
              <HistorySortButton
                label={t("history.grid.question")}
                sortKey="question"
                sort={sort}
                onToggle={onSortChange}
              />
              <HistorySortButton
                label={t("history.grid.execution")}
                sortKey="created_at"
                sort={sort}
                onToggle={onSortChange}
              />
            </div>
            {/* md 未満 5 行・md 以上 8 行の高さで中を縦スクロールにする（#403。以前は手書きの 42rem）。 */}
            <div
              className={`overflow-x-hidden ${INFORMATION_LIST_SCROLL_CLASS}`}
              data-testid="history-list"
            >
              <ul className="divide-y divide-border/70" aria-label={t("history.list.title")} data-testid="history-grid">
                {items.map((item) => {
                  const selected = item.id === selectedId;
                  return (
                    <li key={item.id} className="min-w-0" data-testid="history-row">
                      <button
                        type="button"
                        aria-label={t("history.grid.show", { question: item.question })}
                        aria-describedby={`history-safety-help-${item.safety_is_safe ? "safe" : "blocked"}`}
                        aria-current={selected ? "true" : undefined}
                        className={`grid min-h-20 w-full min-w-0 gap-2 border-l-2 px-3 py-2.5 text-left transition-colors ${
                          selected
                            ? "border-l-accent-fg bg-accent-subtle"
                            : "border-l-transparent hover:bg-surface-hover"
                        }`}
                        onClick={() => onSelect(item)}
                      >
                        <QuestionText
                          value={item.question}
                          variant="select"
                          maxLines={1}
                          className="font-medium text-fg"
                          testId="history-question"
                        />
                        <HistoryExecutor item={item} />
                        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-sans text-xs tabular-nums text-fg">
                            {formatDateTime(item.created_at)}
                          </span>
                          <span className="min-w-0 break-words text-xs text-fg-muted [overflow-wrap:anywhere]">
                            {engineLabel(item.engine)}
                          </span>
                          <StatusBadge icon={false} variant="neutral" label={formatElapsed(item.elapsed_ms)} />
                          {item.generation_elapsed_ms !== null && item.generation_elapsed_ms !== undefined && (
                            <StatusBadge
                              icon={false}
                              variant="info"
                              label={t("history.timing.generationBadge", {
                                elapsed: formatElapsed(item.generation_elapsed_ms),
                              })}
                            />
                          )}
                          <StatusBadge
                            variant={item.feedback_rating ? "success" : "neutral"}
                            label={userFeedbackRatingBadgeLabel(item.feedback_rating)}
                          />
                          <StatusBadge
                            variant={item.safety_is_safe ? "success" : "danger"}
                            label={item.safety_is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
                          />
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </div>
      )}
      {/* 件数と「さらに読み込む」は共通の LoadMoreFooter（#1266）。取り直し（表示を更新）の間は続きを読ませない
          （LoadMoreFooter に disabled が無いので fieldset で無効にする。消すと高さが変わる）。 */}
      {!unavailable && (
        <fieldset disabled={refreshing} className="m-0 min-w-0 border-0 p-0">
          <LoadMoreFooter
            summary={
              total === null
                ? t("history.list.loadedUnknownTotal", { loaded: loadedCount })
                : t("history.list.loaded", { loaded: loadedCount, total })
            }
            hasMore={hasMore}
            loadingMore={loadingMore}
            onLoadMore={onLoadMore}
            loadMoreLabel={t("history.action.loadMore")}
            retryLabel={t("common.action.retry")}
            className="rounded-md border border-border bg-surface-sunken p-3"
            testId="history-load-more"
          />
        </fieldset>
      )}
    </section>
  );
}

function HistoryFilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: Array<[string, string]>;
  onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <SelectField
      id={`history-filter-${id}`}
      label={label}
      value={value}
      options={options.map(([optionValue, optionLabel]) => ({ value: optionValue, label: optionLabel }))}
      onValueChange={onChange}
      className="min-w-0"
    />
  );
}

function historyRequestUrl({
  cursor,
  search,
  feedback,
  safety,
}: {
  cursor?: string;
  search: string;
  feedback: HistoryFeedbackFilter;
  safety: HistorySafetyFilter;
}) {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  const trimmedSearch = search.trim();
  if (trimmedSearch) params.set("q", trimmedSearch);
  if (feedback !== "all") params.set("rating", feedback);
  if (safety !== "all") params.set("safety", safety);
  const query = params.toString();
  return query ? `/api/nl2sql/history?${query}` : "/api/nl2sql/history";
}

function HistoryDetailPanel({
  item,
  tab,
  selectionMissing = false,
  headingRef,
  onTabChange,
  onRerun,
}: {
  item: HistoryItem | null;
  tab: HistoryDetailTab;
  selectionMissing?: boolean;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onTabChange: (tab: HistoryDetailTab) => void;
  /** SQL 生成の画面を開けない利用者には渡さない（再実行のボタンを出さない。#912）。 */
  onRerun?: (item: HistoryItem) => void;
}) {
  if (!item) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-surface-sunken p-4">
        <EmptyState title={t("history.detail.emptyTitle")} hint={t(selectionMissing ? "history.detail.selectionMissing" : "history.detail.emptyHint")} />
      </section>
    );
  }

  const tabs = [
    { id: "overview", label: t("history.detail.overview"), icon: LayoutList },
    { id: "sql", label: t("history.detail.sql"), icon: Code2 },
  ] as const;


  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-surface-sunken p-3 [grid-template-columns:minmax(0,1fr)]" aria-labelledby="history-detail-heading" data-testid="history-detail">
      <div className="grid min-w-0 gap-2 [grid-template-columns:minmax(0,1fr)]" data-testid="history-detail-header">
        <div className="flex min-w-0 flex-wrap items-start gap-3">
          <h2
            id="history-detail-heading"
            ref={headingRef}
            tabIndex={-1}
            className="min-w-0 flex-1 break-words text-base font-semibold leading-6 text-fg [overflow-wrap:anywhere]"
          >
            {t("history.detail.title")}
          </h2>
          {onRerun && (
            <Button
              type="button"
              variant="primary"
              size="sm"
              className="ml-auto w-full shrink-0 whitespace-nowrap sm:w-auto"
              onClick={() => onRerun(item)} icon={RotateCcw}>
              <span>{t("history.action.rerun")}</span>
            </Button>
          )}
        </div>
        <div className="min-w-0 rounded-md border border-border bg-surface p-3" data-testid="history-detail-question-block">
          <p className="text-xs font-medium text-fg-muted">{t("history.grid.question")}</p>
          <QuestionText
            value={item.question}
            variant="detail"
            maxLines={3}
            expandable
            className="mt-1 text-sm font-normal"
            testId="history-detail-question"
          />
        </div>
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className="font-sans text-xs tabular-nums text-fg-muted">{formatDateTime(item.created_at)}</span>
          <StatusBadge icon={false} variant="info" label={engineLabel(item.engine)} />
          <StatusBadge icon={false} variant="neutral" label={formatElapsed(item.elapsed_ms)} />
          {item.generation_elapsed_ms !== null && item.generation_elapsed_ms !== undefined && (
            <StatusBadge
              icon={false}
              variant="info"
              label={t("history.timing.generationBadge", {
                elapsed: formatElapsed(item.generation_elapsed_ms),
              })}
            />
          )}
          <StatusBadge variant={item.feedback_rating ? "success" : "neutral"} label={userFeedbackRatingBadgeLabel(item.feedback_rating)} />
          <StatusBadge
            variant={item.safety_is_safe ? "success" : "danger"}
            label={item.safety_is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
          />
        </div>
        <HistoryExecutor item={item} detailed />
      </div>

      <Tabs
        idPrefix="history-detail"
        ariaLabel={t("history.detail.tabsLabel")}
        value={tab}
        onChange={(id) => onTabChange(id as HistoryDetailTab)}
        items={tabs.map((detailTab) => ({ id: detailTab.id, label: detailTab.label, icon: detailTab.icon }))}
      />

      {tab === "overview" ? (
        <div id="history-detail-panel-overview" role="tabpanel" aria-labelledby="history-detail-tab-overview" className="grid gap-3">
          <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr))]">
            <HistoryFact icon={Database} label={t("history.profile")} value={profileRecordDisplayLabel(item)} />
            <HistoryFact icon={Rows3} label={t("history.rows")} value={item.generation_only ? t("history.notExecuted") : formatNumber(item.result_row_count)} />
            <HistoryFact icon={Columns3} label={t("history.columns")} value={formatNumber(item.result_columns.length)} />
          </div>
          <HistoryTimingBreakdown item={item} />
          <HistoryDetailSection title={t("history.rewritten")} value={item.rewritten_question || "—"} testId="history-detail-rewritten-block" />
          <HistoryDetailSection title={t("history.resultColumns")} value={columnsLabel(item)} mono />
          <div className="rounded-md border border-border bg-surface p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-fg">
              <MessageSquareText size={16} className="text-accent-fg" aria-hidden="true" />
              <span>{t("history.feedbackComment")}</span>
            </div>
            <p className="mt-2 break-words text-sm leading-6 text-fg">{item.feedback_comment || "—"}</p>
          </div>
        </div>
      ) : (
        <section id="history-detail-panel-sql" role="tabpanel" aria-labelledby="history-detail-tab-sql" className="grid gap-3">
          <h3 className="text-sm font-semibold text-fg">{t("history.sql")}</h3>
          <pre data-surface="code" className="max-h-[32rem] overflow-auto rounded-md border border-border bg-surface p-4 font-mono text-sm leading-6 text-fg">
            <code>{item.executable_sql || item.generated_sql || "—"}</code>
          </pre>
        </section>
      )}
    </section>
  );
}

function HistoryTimingMetric({ label, value }: { label: string; value?: number | null }) {
  if (value === null || value === undefined) return null;
  return (
    <div className="min-w-0">
      <p className="text-xs font-medium text-fg-muted">{label}</p>
      <p className="mt-1 font-sans text-sm font-semibold tabular-nums text-fg">
        {formatElapsed(value)}
      </p>
    </div>
  );
}

function HistoryTimingBreakdown({ item }: { item: HistoryItem }) {
  const stageTimings = item.stage_timings ?? [];
  const engineTimings = item.engine_timings ?? [];
  const generationElapsed =
    item.generation_elapsed_ms ?? stageTimingValue(stageTimings, "generate_sql");
  const safetyElapsed = stageTimingValue(stageTimings, "safety_check");
  const executeElapsed = stageTimingValue(stageTimings, "execute_sql");
  const hasBreakdown =
    generationElapsed !== null ||
    safetyElapsed !== null ||
    executeElapsed !== null ||
    engineTimings.length > 0;
  if (!hasBreakdown) return null;

  return (
    <div className="min-w-0 rounded-md border border-border bg-surface p-3" data-testid="history-timing-breakdown">
      <div className="flex items-center gap-2 text-sm font-semibold text-fg">
        <Clock3 size={16} className="text-accent-fg" aria-hidden="true" />
        <span>{t("history.timing.title")}</span>
      </div>
      <div className="mt-3 grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,7rem),1fr))]">
        <HistoryTimingMetric label={t("history.timing.total")} value={item.elapsed_ms} />
        <HistoryTimingMetric label={t("history.timing.generation")} value={generationElapsed} />
        <HistoryTimingMetric label={t("history.timing.safety")} value={safetyElapsed} />
        <HistoryTimingMetric label={t("history.timing.execute")} value={executeElapsed} />
      </div>
      {engineTimings.length > 0 && (
        <ul className="mt-3 grid gap-2" aria-label={t("history.timing.engineAttempts")}>
          {engineTimings.map((timing, index) => (
            <li
              key={`${timing.engine}-${index}`}
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-surface-sunken px-2.5 py-2"
            >
              <span className="min-w-0 break-words text-xs font-semibold text-fg [overflow-wrap:anywhere]">
                {engineTimingLabel(timing.engine)}
              </span>
              <StatusBadge
                variant={engineTimingStatusVariant(timing.status)}
                label={engineTimingStatusLabel(timing.status)}
              />
              <span className="font-sans text-xs tabular-nums text-fg-muted">
                {formatElapsed(timing.elapsed_ms)}
              </span>
              {timing.error ? (
                <span className="min-w-0 break-words text-xs text-fg-muted [overflow-wrap:anywhere]">
                  {timing.error}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function HistoryFact({ icon: Icon, label, value }: { icon: typeof Database; label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-start gap-2 rounded-md border border-border bg-surface p-3">
      <Icon size={16} className="mt-0.5 shrink-0 text-fg-muted" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-fg-muted">{label}</p>
        <p className="mt-1 break-words text-sm font-semibold tabular-nums text-fg [overflow-wrap:anywhere]" title={value}>{value}</p>
      </div>
    </div>
  );
}

function HistoryDetailSection({ title, value, mono = false, testId }: { title: string; value: string; mono?: boolean; testId?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-surface p-3" data-testid={testId}>
      <p className="text-xs font-medium text-fg-muted">{title}</p>
      {mono ? (
        <p className="mt-1 break-words font-mono text-xs leading-6 text-fg [overflow-wrap:anywhere]">{value}</p>
      ) : (
        <QuestionText value={value} variant="detail" maxLines={3} expandable className="mt-1" />
      )}
    </div>
  );
}

export function HistoryPage() {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  // 再実行は SQL 生成の画面へ質問を引き継ぐ。開けない利用者に出すと権限なしの画面へ移ってしまう（#912）。
  const canRerun = canOpenRoute(APP_ROUTES.query, hasPermission);
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  // どのボタンが始めた読込か。スピナーは押したボタンだけが出す（#819）。初回の読込は §3.7 のとおり
  // ヘッダーの「表示を更新」が出し、絞り込み・検索の変更による取り直しは null（一覧の処理中の表示だけ）。
  const [loadOrigin, setLoadOrigin] = useState<"header" | "notice" | null>("header");
  const [filtersSeen, setFiltersSeen] = useState(false);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useWorkspaceState("search", "");
  const [feedbackFilter, setFeedbackFilter] = useWorkspaceState<HistoryFeedbackFilter>("feedbackFilter", "all");
  const [safetyFilter, setSafetyFilter] = useWorkspaceState<HistorySafetyFilter>("safetyFilter", "all");
  const [sort, setSort] = useWorkspaceState<HistorySortState>("sort", { key: "created_at", direction: "desc" });
  const [selectedId, setSelectedId] = useWorkspaceState("selectedId", "");
  const [detailTab, setDetailTab] = useWorkspaceState<HistoryDetailTab>("detailTab", "overview");
  const [nextCursor, setNextCursor] = useState("");
  const [loadedFilters, setLoadedFilters] = useState("");
  const [total, setTotal] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const loadSequence = useRef(0);
  const filterSignature = JSON.stringify([feedbackFilter, safetyFilter, search]);
  const previousFilters = useRef(filterSignature);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  // 先頭ページから取り直す。loading / message は呼び出し側で先に設定しておく。
  // state の更新は応答の callback の中だけで行う（effect からも呼ぶため）。
  const fetchHistory = (announce: boolean) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    return runScopedRequest(async (signal) => {
      const data = await apiGet<HistoryData>(
        historyRequestUrl({ search, feedback: feedbackFilter, safety: safetyFilter }),
        { signal }
      );
      if (signal.aborted || sequence !== loadSequence.current) return;
      setItems(data.items);
      setLoadedFilters(filterSignature);
      setNextCursor(data.next_cursor ?? "");
      setTotal(data.total ?? null);
      setSelectedId((current) => current || data.items[0]?.id || "");
    })
      .then(() => {
        if (announce && sequence === loadSequence.current) {
          toast.success(t("common.action.refreshed"));
        }
      })
      .catch((err: unknown) => {
        if (isAbortError(err) || sequence !== loadSequence.current) {
          return;
        }
        setMessage(err instanceof Error ? err.message : t("history.error.load"));
      })
      .finally(() => {
        if (sequence === loadSequence.current) setLoading(false);
      });
  };

  const load = async (announce = false, origin: "header" | "notice" = "header") => {
    setLoading(true);
    setLoadOrigin(origin);
    setMessage("");
    setLoadingMore(false);
    await fetchHistory(announce);
  };

  // 続きページを読込済みの末尾へ追加する(再読込は load() で先頭からやり直す)。
  const loadMore = async () => {
    if (!nextCursor || loadingMore || loading || loadedFilters !== filterSignature) return;
    const sequence = loadSequence.current;
    setLoadingMore(true);
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiGet<HistoryData>(
          historyRequestUrl({
            cursor: nextCursor,
            search,
            feedback: feedbackFilter,
            safety: safetyFilter,
          }),
          { signal }
        );
        if (signal.aborted || sequence !== loadSequence.current) return;
        setItems((current) => {
          const seen = new Set(current.map((item) => item.id));
          return [...current, ...data.items.filter((item) => !seen.has(item.id))];
        });
        setNextCursor(data.next_cursor ?? "");
        setTotal(data.total ?? null);
      });
    } catch (err) {
      if (isAbortError(err) || sequence !== loadSequence.current) return;
      toast.warning(t("history.error.loadMore"));
    } finally {
      if (sequence === loadSequence.current) setLoadingMore(false);
    }
  };

  // 絞り込みが変わったレンダーで読み込み中の表示にし（effect で setState しない）、取得は effect で行う。
  // 取得は絞り込みが変わったときだけ行う。取得の関数は毎レンダー作り直すので、最新のものを ref から呼ぶ。
  const fetchHistoryRef = useRef(fetchHistory);
  useLayoutEffect(() => { fetchHistoryRef.current = fetchHistory; });
  const filtersChanged = useValuesChanged([feedbackFilter, safetyFilter, search]);
  if (filtersChanged) {
    // 最初のレンダーは初回の読込（ヘッダーが回る）。以降の絞り込みの変更はボタンを回さない（#819）。
    if (filtersSeen) setLoadOrigin(null);
    else setFiltersSeen(true);
    setLoading(true);
    setMessage("");
    setLoadingMore(false);
  }
  useEffect(() => {
    if (previousFilters.current !== filterSignature) {
      previousFilters.current = filterSignature;
      setSelectedId("");
      setDetailTab("overview");
    }
    void fetchHistoryRef.current(false);
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
    // filterSignature は 3 つの絞り込みの JSON なので、元の deps（feedbackFilter / safetyFilter / search）と同じ時に変わる。
    // abortAll と setter は固定の関数。
  }, [filterSignature, abortAll, setDetailTab, setSelectedId]);

  const sortedItems = useMemo(() => sortHistory(items, sort), [items, sort]);

  const selectedItem = sortedItems.find((item) => item.id === selectedId) ?? null;
  const hasActiveFilters = Boolean(search.trim()) || feedbackFilter !== "all" || safetyFilter !== "all";
  // スケルトンで覆うのは、まだ 1 度も一覧を取得できていない初回の読み込みだけ（loadedFilters は取得の成功で入る）。
  // 絞り込みの結果が 0 件のあとの取り直しでも、絞り込みの欄を含む一覧を出したまま処理中の表示にする。
  // スケルトンへ置き換えると、入力中の検索欄や開いている選択欄が作り直されてフォーカスを失う（#739）。
  const initialLoading = loading && !loadedFilters;
  // 今の絞り込みの結果をまだ取得できていないまま取得が失敗した（初回の失敗・絞り込みを変えた後の失敗）。
  // 前の条件の一覧・件数を今の条件の結果として出さず、空の案内（履歴はまだありません・条件に一致しない）
  // とも取り違えさせない。同じ条件の「表示を更新」の失敗は前の内容を残す（messaging §3.6 / §3.7。#912）。
  const listUnavailable = !loading && Boolean(message) && loadedFilters !== filterSignature;

  const toggleSort = (key: HistorySortKey) => {
    setSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const clearFilters = () => {
    setSearch("");
    setFeedbackFilter("all");
    setSafetyFilter("all");
  };

  const selectItem = (item: HistoryItem) => {
    if (item.id !== selectedId) setDetailTab("overview");
    setSelectedId(item.id);
  };

  return (
    <>
      <PageHeader wide
        title={t("nav.history")}
        subtitle={t("history.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true, "header"),
            loading: loading && loadOrigin === "header",
            disabled: loading && loadOrigin !== "header",
          },
        ]}
      />
      <PageBody wide className="grid gap-3">
        <HistorySafetyHelp />
        <PageNotice
          notice={message ? { tone: "danger", message: `${message} ${t("history.error.retryHint")}` } : null}
          action={
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={loading && loadOrigin === "notice"}
              disabled={loading && loadOrigin !== "notice"}
              onClick={() => void load(false, "notice")}
              icon={RefreshCw}
            >
              <span>{t("history.action.refresh")}</span>
            </Button>
          }
        />

        {initialLoading ? (
          <DbObjectManagementPanelShell
            id="history-management-panel"
            labelledBy="history-grid-heading"
            idPrefix="history-management"
            ariaLabel={t("history.workspace.label")}
            splitId="history-management-list"
            preferredWidePane="right"
          >
            <HistoryListSkeleton />
            <HistoryDetailSkeleton />
          </DbObjectManagementPanelShell>
        ) : items.length === 0 && !hasActiveFilters && !loading && !listUnavailable ? (
          <section className="rounded-md border border-border bg-surface p-4 shadow-sm" aria-label={t("history.workspace.label")}>
            <EmptyState title={t("history.empty.title")} hint={t("history.empty.hint")} />
          </section>
        ) : (
          <DbObjectManagementPanelShell
            id="history-management-panel"
            labelledBy="history-grid-heading"
            idPrefix="history-management"
            ariaLabel={t("history.workspace.label")}
            splitId="history-management-list"
            preferredWidePane="right"
            processing={
              loading ? (
                <ProcessingIndicator
                  active
                  label={t("common.processing.refreshing")}
                  operationKey="history-refresh"
                  placement="workspace"
                  className="rounded-md border border-border bg-surface-sunken px-3 py-2"
                  testId="history-workspace-processing"
                  // 絞り込み・検索の変更による取り直しはボタンが回らないため、この表示がスピナーを出す（#819）。
                  activityIcon={loadOrigin ? "none" : "spinner"}
                />
              ) : undefined
            }
          >
            <HistoryGrid
              items={listUnavailable ? [] : sortedItems}
              selectedId={selectedItem?.id ?? ""}
              search={search}
              feedbackFilter={feedbackFilter}
              safetyFilter={safetyFilter}
              sort={sort}
              onSearchChange={setSearch}
              onFeedbackFilterChange={setFeedbackFilter}
              onSafetyFilterChange={setSafetyFilter}
              onSortChange={toggleSort}
              onSelect={selectItem}
              onClearFilters={clearFilters}
              loadedCount={items.length}
              total={total}
              hasMore={Boolean(nextCursor) && loadedFilters === filterSignature}
              loadingMore={loadingMore}
              refreshing={loading}
              unavailable={listUnavailable}
              onLoadMore={() => void loadMore()}
            />
            <HistoryDetailPanel
              item={listUnavailable ? null : selectedItem}
              selectionMissing={!listUnavailable && Boolean(selectedId) && !selectedItem}
              tab={detailTab}
              headingRef={detailHeadingRef}
              onTabChange={setDetailTab}
              onRerun={canRerun ? (item) => navigate(historyRerunUrl(item, APP_ROUTES.query)) : undefined}
            />
          </DbObjectManagementPanelShell>
        )}
      </PageBody>
    </>
  );
}
