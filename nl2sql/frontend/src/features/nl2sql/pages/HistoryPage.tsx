import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import {
  ArrowDown,
  ArrowDownUp,
  ArrowUp,
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
  DEFAULT_PAGE_SIZE,
  EmptyState,
  PageHeader,
  Pagination,
  StatusBadge,
  usePagination,
} from "@engchina/production-ready-ui";

import { PageNotice } from "@/components/page-notice";
import { apiGet, isAbortError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import {
  DbManagementSearchField,
  DbObjectManagementPanelShell,
  DbObjectManagementStatusBar,
  DbObjectPanelHeader,
} from "../components/DbObjectManagementShared";
import {
  filterAndSortHistory,
  selectedVisibleHistoryId,
  type HistoryFeedbackFilter,
  type HistorySafetyFilter,
  type HistorySortKey,
  type HistorySortState,
} from "../historyManagementState";
import { engineLabel } from "../labels";
import { historyRerunUrl } from "../queryPrefillState";
import { formatElapsed } from "../useOperationTimer";
import type { HistoryData, HistoryItem } from "../types";

type HistoryDetailTab = "overview" | "sql";

function feedbackLabel(item: HistoryItem) {
  if (item.feedback_rating === "good") return t("nl2sql.feedback.good");
  if (item.feedback_rating === "bad") return t("nl2sql.feedback.bad");
  return t("history.feedback.none");
}

function columnsLabel(item: HistoryItem) {
  if (item.result_columns.length === 0) return "—";
  return item.result_columns.join(", ");
}

function latestCreatedAt(items: HistoryItem[]) {
  return items.reduce((latest, item) => {
    const currentTime = Date.parse(item.created_at);
    const latestTime = latest ? Date.parse(latest) : Number.NEGATIVE_INFINITY;
    return !Number.isNaN(currentTime) && currentTime > latestTime ? item.created_at : latest;
  }, "");
}

function focusHistoryTab(id: string) {
  window.requestAnimationFrame(() => document.getElementById(id)?.focus());
}

function HistorySkeletonBlock({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-muted/30 motion-reduce:animate-none ${className}`} aria-hidden="true" />;
}

function HistoryListSkeleton() {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="history-grid-heading">
      <h2 id="history-grid-heading" className="sr-only">{t("history.list.title")}</h2>
      <HistorySkeletonBlock className="h-14" />
      <HistorySkeletonBlock className="h-28" />
      <div className="grid gap-2" data-testid="history-list-skeleton">
        {Array.from({ length: 6 }, (_, index) => (
          <HistorySkeletonBlock key={index} className="h-20" />
        ))}
      </div>
    </section>
  );
}

function HistoryDetailSkeleton() {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-label={t("history.detail.title")} data-testid="history-detail-skeleton">
      <HistorySkeletonBlock className="h-20" />
      <HistorySkeletonBlock className="h-11" />
      <HistorySkeletonBlock className="h-72" />
    </section>
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
    <button
      type="button"
      aria-label={t("history.sort.button", { label, direction })}
      aria-pressed={active}
      className={`inline-flex min-h-11 items-center gap-1.5 rounded-md px-2 text-left text-xs font-semibold transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 sm:min-h-9 ${
        active ? "bg-primary/10 text-primary" : "text-muted hover:bg-background hover:text-foreground"
      }`}
      onClick={() => onToggle(sortKey)}
    >
      <span>{label}</span>
      <SortIcon size={13} aria-hidden="true" />
    </button>
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
  onSelect: (item: HistoryItem, moveFocus: boolean) => void;
  onClearFilters: () => void;
}) {
  const { page, setPage, totalPages, pageItems, range } = usePagination(items, DEFAULT_PAGE_SIZE);
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="history-grid-heading">
      <DbObjectPanelHeader
        headingId="history-grid-heading"
        icon={History}
        title={t("history.list.title")}
        description={t("history.list.hint")}
        action={<StatusBadge variant="info" label={t("history.list.count", { count: items.length })} />}
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
        <div className="overflow-hidden rounded-md border border-border bg-card">
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
              {pageItems.map((item) => {
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
                      onClick={() => onSelect(item, true)}
                    >
                      <span
                        className="line-clamp-2 min-w-0 break-words text-sm font-semibold leading-5 text-primary [overflow-wrap:anywhere]"
                        data-testid="history-question"
                        title={item.question}
                      >
                        {item.question}
                      </span>
                      <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-mono text-xs tabular-nums text-foreground">
                          {formatDateTime(item.created_at)}
                        </span>
                        <span className="min-w-0 break-words text-xs text-muted [overflow-wrap:anywhere]">
                          {engineLabel(item.engine)}
                        </span>
                        <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
                        <StatusBadge
                          variant={item.feedback_rating ? "success" : "neutral"}
                          label={feedbackLabel(item)}
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
          <Pagination
            page={page}
            totalPages={totalPages}
            onPageChange={setPage}
            summary={t("history.pagination.range", { start: range.start, end: range.end, total: range.total })}
            pageIndicator={t("history.pagination.page", { page, total: totalPages })}
            prevLabel={t("history.pagination.prev")}
            nextLabel={t("history.pagination.next")}
            ariaLabel={t("history.pagination.label")}
            testId="history-pagination"
          />
        </div>
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

function HistoryDetailPanel({
  item,
  tab,
  headingRef,
  onTabChange,
  onRerun,
}: {
  item: HistoryItem | null;
  tab: HistoryDetailTab;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onTabChange: (tab: HistoryDetailTab) => void;
  onRerun: (item: HistoryItem) => void;
}) {
  if (!item) {
    return (
      <section className="grid min-w-0 content-start gap-3 rounded-md border border-border bg-background p-4">
        <EmptyState title={t("history.detail.emptyTitle")} hint={t("history.detail.emptyHint")} />
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
      <div className="flex min-w-0 flex-wrap items-start gap-3" data-testid="history-detail-header">
        <div className="min-w-0 flex-[1_1_28rem]">
          <h2
            id="history-detail-heading"
            ref={headingRef}
            tabIndex={-1}
            className="min-w-0 break-words text-base font-semibold leading-6 text-foreground [overflow-wrap:anywhere] focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            {item.question}
          </h2>
          <div className="mt-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
            <span className="font-mono text-xs tabular-nums text-muted">{formatDateTime(item.created_at)}</span>
            <StatusBadge variant="info" label={engineLabel(item.engine)} />
            <StatusBadge variant="neutral" label={formatElapsed(item.elapsed_ms)} />
            <StatusBadge variant={item.feedback_rating ? "success" : "neutral"} label={feedbackLabel(item)} />
            <StatusBadge
              variant={item.safety_is_safe ? "success" : "danger"}
              label={item.safety_is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
            />
          </div>
        </div>
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
            <HistoryFact icon={Database} label={t("history.profile")} value={item.profile_name || item.profile_id || "—"} />
            <HistoryFact icon={Rows3} label={t("history.rows")} value={formatNumber(item.result_row_count)} />
            <HistoryFact icon={Columns3} label={t("history.columns")} value={formatNumber(item.result_columns.length)} />
          </div>
          <HistoryDetailSection title={t("history.rewritten")} value={item.rewritten_question || "—"} />
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

function HistoryDetailSection({ title, value, mono = false }: { title: string; value: string; mono?: boolean }) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <p className="text-xs font-medium text-muted">{title}</p>
      <p className={`mt-1 break-words text-sm leading-6 text-foreground [overflow-wrap:anywhere] ${mono ? "font-mono text-xs" : ""}`}>{value}</p>
    </div>
  );
}

export function HistoryPage() {
  const navigate = useNavigate();
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useState("");
  const [feedbackFilter, setFeedbackFilter] = useState<HistoryFeedbackFilter>("all");
  const [safetyFilter, setSafetyFilter] = useState<HistorySafetyFilter>("all");
  const [sort, setSort] = useState<HistorySortState>({ key: "created_at", direction: "desc" });
  const [selectedId, setSelectedId] = useState("");
  const [detailTab, setDetailTab] = useState<HistoryDetailTab>("overview");
  const loadSequence = useRef(0);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  const load = async () => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    setLoading(true);
    setMessage("");
    try {
      await runScopedRequest(async (signal) => {
        const data = await apiGet<HistoryData>("/api/nl2sql/history", { signal });
        if (signal.aborted || sequence !== loadSequence.current) return;
        setItems(data.items);
        setSelectedId((current) => selectedVisibleHistoryId(data.items, current));
      });
    } catch (err) {
      if (isAbortError(err)) {
        return;
      }
      setMessage(err instanceof Error ? err.message : t("history.error.load"));
    } finally {
      if (sequence === loadSequence.current) setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    return () => {
      loadSequence.current += 1;
      abortAll();
    };
  }, []);

  const filteredItems = useMemo(
    () => filterAndSortHistory(items, { search, feedback: feedbackFilter, safety: safetyFilter, sort }),
    [feedbackFilter, items, safetyFilter, search, sort]
  );

  useEffect(() => {
    setSelectedId((current) => selectedVisibleHistoryId(filteredItems, current));
  }, [filteredItems]);

  useEffect(() => {
    setDetailTab("overview");
  }, [selectedId]);

  const selectedItem = filteredItems.find((item) => item.id === selectedId) ?? filteredItems[0] ?? null;
  const evaluatedCount = filteredItems.filter((item) => item.feedback_rating).length;
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

  const selectItem = (item: HistoryItem, moveFocus: boolean) => {
    setSelectedId(item.id);
    if (!moveFocus || !window.matchMedia("(max-width: 1279px)").matches) return;
    window.requestAnimationFrame(() => {
      detailHeadingRef.current?.focus();
      detailHeadingRef.current?.scrollIntoView({ block: "start" });
    });
  };

  return (
    <>
      <PageHeader title={t("nav.history")} subtitle={t("history.subtitle")} />
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

        <DbObjectManagementStatusBar
          ariaLabel={t("history.status.label")}
          density="compact"
          metrics={[
            { label: t("history.status.visible"), value: formatNumber(filteredItems.length), emphasis: true },
            { label: t("history.status.evaluated"), value: formatNumber(evaluatedCount) },
            { label: t("history.status.latest"), value: formatDateTime(latestCreatedAt(filteredItems)) },
          ]}
          actions={
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
          >
            <HistoryGrid
              items={filteredItems}
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
            />
            <HistoryDetailPanel
              item={selectedItem}
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
