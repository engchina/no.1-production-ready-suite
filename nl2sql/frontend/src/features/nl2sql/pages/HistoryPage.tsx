import { SortHeader } from "@/components/SortHeader";
import { useWorkspaceState } from "@/components/WorkspaceState";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
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

import { Button } from "@/components/ui/button";
import { EmptyState, toast } from "@engchina/production-ready-ui";

import { StatusBadge, type StatusVariant } from "@/components/ui/status-badge";
import { PageHeader } from "@/components/PageHeader";
import { PageNotice } from "@/components/page-notice";
import { ProcessingIndicator, TimedLoadingState } from "@/components/ProcessingState";
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

function focusHistoryTab(id: string) {
  window.requestAnimationFrame(() => document.getElementById(id)?.focus({ preventScroll: true }));
}

function HistorySkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-muted/30 motion-reduce:animate-none ${className}`} aria-hidden="true" />;
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
    >
      <h2 id="history-grid-heading" className="sr-only">{t("history.list.title")}</h2>
      <HistorySkeletonBlock className="h-14" />
      <HistorySkeletonBlock className="h-28" />
      <div className="grid gap-2" data-testid="history-list-skeleton">
        {Array.from({ length: 6 }, (_, index) => (
          <HistorySkeletonBlock key={index} className="h-20" />
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
    >
      <HistorySkeletonBlock className="h-20" />
      <HistorySkeletonBlock className="h-11" />
      <HistorySkeletonBlock className="h-72" />
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
    <SortHeader
      type="button"
      aria-label={t("history.sort.button", { label, direction })}
      aria-pressed={active}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <SortIcon size={13} aria-hidden="true" />
    </SortHeader>
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
        action={<StatusBadge variant="info" label={t("history.list.count", { count })} />}
      />

      <div className="grid gap-2 rounded-md border border-border bg-background p-3">
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

      {items.length === 0 ? (
        <div className="grid justify-items-center gap-3 rounded-md border border-border bg-background p-4">
          <EmptyState title={t("history.noResults.title")} hint={t("history.noResults.hint")} />
          <Button type="button" variant="secondary" size="sm" onClick={onClearFilters}>
            {t("history.action.clearFilters")}
          </Button>
        </div>
      ) : (
        <div className="grid gap-2">
          <div
            className="overflow-hidden rounded-md border border-border bg-card"
            data-testid="history-list-surface"
          >
            <div
              className="flex flex-wrap items-center gap-1 border-b border-border bg-background px-2 py-1.5"
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
            <div className="max-h-[42rem] overflow-x-hidden overflow-y-auto" data-testid="history-list">
              <ul className="divide-y divide-border/70" aria-label={t("history.list.title")} data-testid="history-grid">
                {items.map((item) => {
                  const selected = item.id === selectedId;
                  return (
                    <li key={item.id} className="min-w-0" data-testid="history-row">
                      <button
                        type="button"
                        aria-label={t("history.grid.show", { question: item.question })}
                        aria-current={selected ? "true" : undefined}
                        className={`grid min-h-20 w-full min-w-0 gap-2 border-l-2 px-3 py-2.5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 ${
                          selected
                            ? "border-l-primary bg-primary/10"
                            : "border-l-transparent hover:bg-background"
                        }`}
                        onClick={() => onSelect(item)}
                      >
                        <QuestionText
                          value={item.question}
                          variant="select"
                          maxLines={1}
                          className="font-medium text-foreground"
                          testId="history-question"
                        />
                        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="font-mono text-xs tabular-nums text-foreground">
                            {formatDateTime(item.created_at)}
                          </span>
                          <span className="min-w-0 break-words text-xs text-muted [overflow-wrap:anywhere]">
                            {engineLabel(item.engine)}
                          </span>
                          <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
                          {item.generation_elapsed_ms !== null && item.generation_elapsed_ms !== undefined && (
                            <StatusBadge
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
      <div
        className="flex flex-col gap-2 rounded-md border border-border bg-background p-3 sm:flex-row sm:items-center sm:justify-between"
        data-testid="history-load-more"
      >
        <p className="text-xs leading-5 text-muted">
          {total === null
            ? t("history.list.loadedUnknownTotal", { loaded: loadedCount })
            : t("history.list.loaded", { loaded: loadedCount, total })}
        </p>
        {hasMore && (
          <Button type="button" variant="secondary" size="sm" loading={loadingMore} onClick={onLoadMore}>
            {t("history.action.loadMore")}
          </Button>
        )}
      </div>
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
  return (
    <label className="grid min-w-0 gap-1 text-sm font-medium text-foreground">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        className="min-h-11 w-full min-w-0 rounded-md border border-border bg-card px-3 py-2 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40"
      >
        {options.map(([optionValue, optionLabel]) => (
          <option key={optionValue} value={optionValue}>{optionLabel}</option>
        ))}
      </select>
    </label>
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
  onRerun: (item: HistoryItem) => void;
}) {
  if (!item) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4">
        <EmptyState title={t("history.detail.emptyTitle")} hint={t(selectionMissing ? "history.detail.selectionMissing" : "history.detail.emptyHint")} />
      </section>
    );
  }

  const tabs = [
    { id: "overview", label: t("history.detail.overview"), icon: LayoutList },
    { id: "sql", label: t("history.detail.sql"), icon: Code2 },
  ] as const;

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keyMap: Record<string, number | undefined> = {
      ArrowRight: (index + 1) % tabs.length,
      ArrowLeft: (index - 1 + tabs.length) % tabs.length,
      Home: 0,
      End: tabs.length - 1,
    };
    const nextIndex = keyMap[event.key];
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextTab = tabs[nextIndex];
    onTabChange(nextTab.id);
    focusHistoryTab(`history-detail-tab-${nextTab.id}`);
  };

  return (
    <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-3 [grid-template-columns:minmax(0,1fr)]" aria-labelledby="history-detail-heading" data-testid="history-detail">
      <div className="grid min-w-0 gap-2 [grid-template-columns:minmax(0,1fr)]" data-testid="history-detail-header">
        <div className="flex min-w-0 flex-wrap items-start gap-3">
          <h2
            id="history-detail-heading"
            ref={headingRef}
            tabIndex={-1}
            className="min-w-0 flex-1 break-words text-base font-semibold leading-6 text-foreground [overflow-wrap:anywhere] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {t("history.detail.title")}
          </h2>
          <Button
            type="button"
            variant="primary"
            size="sm"
            className="ml-auto w-full shrink-0 whitespace-nowrap sm:w-auto"
            onClick={() => onRerun(item)}
          >
            <RotateCcw size={15} aria-hidden="true" />
            <span>{t("history.action.rerun")}</span>
          </Button>
        </div>
        <div className="min-w-0 rounded-md border border-border bg-card p-3" data-testid="history-detail-question-block">
          <p className="text-xs font-medium text-muted">{t("history.grid.question")}</p>
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
          <span className="font-mono text-xs tabular-nums text-muted">{formatDateTime(item.created_at)}</span>
          <StatusBadge variant="info" label={engineLabel(item.engine)} />
          <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
          {item.generation_elapsed_ms !== null && item.generation_elapsed_ms !== undefined && (
            <StatusBadge
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
      </div>

      <div className="overflow-x-auto border-b border-border" role="tablist" aria-label={t("history.detail.tabsLabel")}>
        <div className="flex min-w-max gap-1">
          {tabs.map((detailTab, index) => {
            const Icon = detailTab.icon;
            const selected = tab === detailTab.id;
            return (
              <button
                key={detailTab.id}
                id={`history-detail-tab-${detailTab.id}`}
                type="button"
                role="tab"
                aria-selected={selected}
                aria-controls={`history-detail-panel-${detailTab.id}`}
                className={`group inline-flex min-h-11 shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-4 text-sm font-semibold transition-colors focus:outline-none focus-visible:bg-primary/10 focus-visible:ring-2 focus-visible:ring-ring/40 ${
                  selected
                    ? "border-primary bg-card text-primary"
                    : "border-transparent text-muted hover:border-border hover:bg-card hover:text-foreground"
                }`}
                onClick={() => onTabChange(detailTab.id)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
              >
                <Icon size={15} aria-hidden="true" className={selected ? "text-primary" : "text-muted"} />
                <span>{detailTab.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {tab === "overview" ? (
        <div id="history-detail-panel-overview" role="tabpanel" aria-labelledby="history-detail-tab-overview" className="grid gap-3">
          <div className="grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr))]">
            <HistoryFact icon={Database} label={t("history.profile")} value={profileRecordDisplayLabel(item)} />
            <HistoryFact icon={Rows3} label={t("history.rows")} value={formatNumber(item.result_row_count)} />
            <HistoryFact icon={Columns3} label={t("history.columns")} value={formatNumber(item.result_columns.length)} />
          </div>
          <HistoryTimingBreakdown item={item} />
          <HistoryDetailSection title={t("history.rewritten")} value={item.rewritten_question || "—"} testId="history-detail-rewritten-block" />
          <HistoryDetailSection title={t("history.resultColumns")} value={columnsLabel(item)} mono />
          <div className="rounded-md border border-border bg-card p-3">
            <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
              <MessageSquareText size={16} className="text-primary" aria-hidden="true" />
              <span>{t("history.feedbackComment")}</span>
            </div>
            <p className="mt-2 break-words text-sm leading-6 text-foreground">{item.feedback_comment || "—"}</p>
          </div>
        </div>
      ) : (
        <section id="history-detail-panel-sql" role="tabpanel" aria-labelledby="history-detail-tab-sql" className="grid gap-3">
          <h3 className="text-sm font-semibold text-foreground">{t("history.sql")}</h3>
          <pre className="max-h-[32rem] overflow-auto rounded-md border border-border bg-code p-4 font-mono text-sm leading-6 text-code-fg">
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
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className="mt-1 font-mono text-sm font-semibold tabular-nums text-foreground">
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
    <div className="min-w-0 rounded-md border border-border bg-card p-3" data-testid="history-timing-breakdown">
      <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
        <Clock3 size={16} className="text-primary" aria-hidden="true" />
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
              className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-border bg-background px-2.5 py-2"
            >
              <span className="min-w-0 break-words text-xs font-semibold text-foreground [overflow-wrap:anywhere]">
                {engineTimingLabel(timing.engine)}
              </span>
              <StatusBadge
                variant={engineTimingStatusVariant(timing.status)}
                label={engineTimingStatusLabel(timing.status)}
              />
              <span className="font-mono text-xs tabular-nums text-muted">
                {formatElapsed(timing.elapsed_ms)}
              </span>
              {timing.error ? (
                <span className="min-w-0 break-words text-xs text-muted [overflow-wrap:anywhere]">
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
    <div className="flex min-w-0 items-start gap-2 rounded-md border border-border bg-card p-3">
      <Icon size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
      <div className="min-w-0">
        <p className="text-xs font-medium text-muted">{label}</p>
        <p className="mt-1 break-words text-sm font-semibold tabular-nums text-foreground [overflow-wrap:anywhere]" title={value}>{value}</p>
      </div>
    </div>
  );
}

function HistoryDetailSection({ title, value, mono = false, testId }: { title: string; value: string; mono?: boolean; testId?: string }) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card p-3" data-testid={testId}>
      <p className="text-xs font-medium text-muted">{title}</p>
      {mono ? (
        <p className="mt-1 break-words font-mono text-xs leading-6 text-foreground [overflow-wrap:anywhere]">{value}</p>
      ) : (
        <QuestionText value={value} variant="detail" maxLines={3} expandable className="mt-1" />
      )}
    </div>
  );
}

export function HistoryPage() {
  const navigate = useNavigate();
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useWorkspaceState("search", "");
  const [feedbackFilter, setFeedbackFilter] = useWorkspaceState<HistoryFeedbackFilter>("feedbackFilter", "all");
  const [safetyFilter, setSafetyFilter] = useWorkspaceState<HistorySafetyFilter>("safetyFilter", "all");
  const [sort, setSort] = useWorkspaceState<HistorySortState>("sort", { key: "created_at", direction: "desc" });
  const [selectedId, setSelectedId] = useWorkspaceState("selectedId", "");
  const [detailTab, setDetailTab] = useWorkspaceState<HistoryDetailTab>("detailTab", "overview");
  const [nextCursor, setNextCursor] = useState("");
  const [total, setTotal] = useState<number | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const loadSequence = useRef(0);
  const filterSignature = JSON.stringify([feedbackFilter, safetyFilter, search]);
  const previousFilters = useRef(filterSignature);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const load = async (announce = false) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setMessage("");
    setNextCursor("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiGet<HistoryData>(
          historyRequestUrl({ search, feedback: feedbackFilter, safety: safetyFilter }),
          { signal }
        );
        if (signal.aborted || sequence !== loadSequence.current) return;
        setItems(data.items);
        setNextCursor(data.next_cursor ?? "");
        setTotal(data.total ?? null);
        setSelectedId((current) => current || data.items[0]?.id || "");
      });
      if (announce && sequence === loadSequence.current) {
        toast.success(t("common.action.refreshed"));
      }
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("history.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  // 続きページを読込済みの末尾へ追加する(再読込は load() で先頭からやり直す)。
  const loadMore = async () => {
    if (!nextCursor || loadingMore) return;
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
      if (isAbortError(err)) return;
      toast.warning(t("history.error.loadMore"));
    } finally {
      setLoadingMore(false);
    }
  };

  useEffect(() => {
    if (previousFilters.current !== filterSignature) {
      previousFilters.current = filterSignature;
      setSelectedId("");
      setDetailTab("overview");
    }
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, [feedbackFilter, safetyFilter, search]);

  const sortedItems = useMemo(() => sortHistory(items, sort), [items, sort]);

  const selectedItem = sortedItems.find((item) => item.id === selectedId) ?? null;
  const hasActiveFilters = Boolean(search.trim()) || feedbackFilter !== "all" || safetyFilter !== "all";

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
      <PageHeader
        title={t("nav.history")}
        subtitle={t("history.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: () => load(true),
            loading,
          },
        ]}
      />
      <main className="grid gap-3 p-3 sm:p-4 lg:p-6">
        <PageNotice
          notice={message ? { tone: "danger", message: `${message} ${t("history.error.retryHint")}` } : null}
          action={
            <Button type="button" variant="secondary" size="sm" loading={loading} onClick={() => void load()}>
              <RefreshCw size={15} aria-hidden="true" />
              <span>{t("history.action.refresh")}</span>
            </Button>
          }
        />

        {loading && items.length === 0 ? (
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
        ) : items.length === 0 && !hasActiveFilters ? (
          <section className="rounded-md border border-border bg-card p-4 shadow-sm" aria-label={t("history.workspace.label")}>
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
                  className="rounded-md border border-border bg-background px-3 py-2"
                  testId="history-workspace-processing"
                  activityIcon="none"
                />
              ) : undefined
            }
          >
            <HistoryGrid
              items={sortedItems}
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
              hasMore={Boolean(nextCursor)}
              loadingMore={loadingMore}
              onLoadMore={() => void loadMore()}
            />
            <HistoryDetailPanel
              item={selectedItem}
              selectionMissing={Boolean(selectedId) && !selectedItem}
              tab={detailTab}
              headingRef={detailHeadingRef}
              onTabChange={setDetailTab}
              onRerun={(item) => navigate(historyRerunUrl(item, APP_ROUTES.query))}
            />
          </DbObjectManagementPanelShell>
        )}
      </main>
    </>
  );
}
