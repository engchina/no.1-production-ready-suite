import { userDisplayName } from "@production-ready/system-settings";
import { useWorkspaceState } from "@/components/WorkspaceState";
import { useEffect, useId, useLayoutEffect, useRef, useState, type RefObject } from "react";
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
  EmptyState,
  ListToolbar,
  OffsetPagination,
  toast,
  StatusBadge,
  type StatusVariant,
  PageHeader,
  Tabs,
  PageBody,
  ProcessingIndicator,
  Skeleton,
  TimedLoadingState,
  SelectField,
} from "@production-ready/ui";
import { useAuth } from "@/features/security/AuthProvider";
import { canOpenRoute } from "@/features/security/route-permissions";

import { PageNotice } from "@/components/page-notice";
import { ApiError, apiGet, isAbortError } from "@/lib/api";
import { formatDateTime, formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { formatElapsedDuration as formatElapsed } from "@/lib/operationTiming";
import { listCountLabels, paginationLabels } from "@/lib/pagination-labels";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import { useWorkspacePaging } from "@/lib/use-workspace-paging";
import { DbManagementSearchField, DbObjectManagementPanelShell, DbObjectPanelHeader } from "../components/DbObjectManagementShared";
import { HistoryListRow, HistoryRowList, HistoryRowListSkeleton } from "../components/HistoryRowList";
import { QuestionText } from "../components/QuestionText";
import {
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
  // 実行者は表示名（空ならログイン ID）で出し、UUID は出さない。見つからない利用者は削除済みの文言（#1696）。
  const identity = !uuid
    ? t("history.actor.unrecorded")
    : userDisplayName(
        { display_name: item.actor_display_name, login_user_id: item.actor_login_user_id },
        t("history.actor.missing")
      );
  const resolved = Boolean(uuid && (item.actor_display_name?.trim() || item.actor_login_user_id?.trim()));
  return (
    <span className="grid min-w-0 gap-1 text-sm leading-relaxed text-fg [overflow-wrap:anywhere]" data-testid="history-executor">
      <span>{t("history.actor.label")}: {identity}</span>
      {detailed && resolved && <span className="text-fg-muted">{t("history.actor.hint")}</span>}
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
    <HistoryRowListSkeleton
      label={t("history.list.loading")}
      operationKey="history-list-load"
      testId="history-list-loading"
      skeletonTestId="history-list-skeleton"
      // 初回の読込は PageHeader の「再読み込み」の loading がスピナーを出す（同じ処理のスピナーは 1 つ。
      // messaging §3.7、#416）。
      activityIcon="none"
    >
      <h2 id="history-grid-heading" className="sr-only">{t("history.list.title")}</h2>
      <Skeleton className="h-14" />
      <Skeleton className="h-28" />
    </HistoryRowListSkeleton>
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
  total,
  unfilteredTotal,
  hasActiveFilters,
  offset,
  pageSize,
  refreshing,
  unavailable,
  onPageChange,
  onPageSizeChange,
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
  /** 絞り込みを適用した全件（サーバーが数える）。 */
  total: number;
  /** 絞り込む前の全件（「N 件（全 M 件）」の M）。 */
  unfilteredTotal: number;
  hasActiveFilters: boolean;
  /** 表示しているページの先頭の位置と 1 ページの件数（offset 型のページング。#1891）。 */
  offset: number;
  pageSize: number;
  refreshing: boolean;
  /** 今の絞り込みの結果を取得できなかった（前の条件の一覧は出さない）。 */
  unavailable: boolean;
  onPageChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3" aria-labelledby="history-grid-heading">
      <DbObjectPanelHeader
        headingId="history-grid-heading"
        icon={History}
        title={t("history.list.title")}
        description={t("history.list.hint")}
      />

      {/* 検索・絞り込み・件数は共通の ListToolbar（検索欄は lg で固定、件数は右の 1 か所。#1891）。 */}
      <div className="rounded-md border border-border bg-surface-sunken p-3">
        <ListToolbar
          testId="history-filter-grid"
          search={
            <DbManagementSearchField
              label={t("history.search.label")}
              placeholder={t("history.search.placeholder")}
              value={search}
              onChange={onSearchChange}
              // 実行履歴は #1955 で作り直すまで上のラベルの形のまま（#1933 の段 3 の対象外）。
              labelHidden={false}
            />
          }
          count={
            unavailable
              ? undefined
              : {
                  total: unfilteredTotal,
                  matched: hasActiveFilters ? total : undefined,
                  labels: listCountLabels(),
                  testId: "history-list-count",
                }
          }
          filters={
            <>
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
            </>
          }
        />
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
          <HistoryRowList
            label={t("history.list.title")}
            surfaceTestId="history-list-surface"
            scrollTestId="history-list"
            listTestId="history-grid"
            header={
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
            }
          >
            {items.map((item) => (
              <HistoryListRow
                key={item.id}
                question={item.question}
                createdAt={item.created_at}
                engine={engineLabel(item.engine)}
                selected={item.id === selectedId}
                onSelect={() => onSelect(item)}
                ariaLabel={t("history.grid.show", { question: item.question })}
                ariaDescribedBy={`history-safety-help-${item.safety_is_safe ? "safe" : "blocked"}`}
                testId="history-row"
                questionTestId="history-question"
                meta={<HistoryExecutor item={item} />}
                badges={
                  <>
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
                  </>
                }
              />
            ))}
          </HistoryRowList>
          {/* ページ送りは一覧の直下の共通の OffsetPagination（ページ番号の入力・最初へ / 最後へ・表示件数。
              1 ページなら出さない。#1764 / #1891）。取り直し（表示を更新・絞り込み・ページ送り）の間は押させない。 */}
          <fieldset disabled={refreshing} className="m-0 min-w-0 border-0 p-0">
            <OffsetPagination
              offset={offset}
              limit={pageSize}
              total={total}
              count={items.length}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
              disabled={refreshing}
              labels={paginationLabels()}
              ariaLabel={t("history.pagination.label")}
              testId="history-pagination"
            />
          </fieldset>
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
  const id = useId();
  return (
    <SelectField
      id={`history-filter-${id}`}
      label={label}
      value={value}
      options={options.map(([optionValue, optionLabel]) => ({ value: optionValue, label: optionLabel }))}
      onValueChange={onChange}
      // 評価・安全性の短い選択（#1891 の幅の段）。
      width="sm"
    />
  );
}

function historyRequestUrl({
  offset,
  limit,
  search,
  feedback,
  safety,
  sort,
}: {
  offset: number;
  limit: number;
  search: string;
  feedback: HistoryFeedbackFilter;
  safety: HistorySafetyFilter;
  sort: HistorySortState;
}) {
  // 1 ページは表示件数（既定 25 件）。並べ替えはサーバーが全件で行う（ページの中だけで並べ替えない。#1764）。
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  const trimmedSearch = search.trim();
  if (trimmedSearch) params.set("q", trimmedSearch);
  if (feedback !== "all") params.set("rating", feedback);
  if (safety !== "all") params.set("safety", safety);
  if (sort.key !== "created_at" || sort.direction !== "desc") {
    params.set("sort", sort.key);
    params.set("direction", sort.direction);
  }
  return `/api/nl2sql/history?${params.toString()}`;
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
  // ヘッダーの「最新情報を取得」が出し、絞り込み・検索の変更による取り直しは null（一覧の処理中の表示だけ）。
  const [loadOrigin, setLoadOrigin] = useState<"header" | "notice" | null>("header");
  const [filtersSeen, setFiltersSeen] = useState(false);
  const [message, setMessage] = useState("");
  const [search, setSearch] = useWorkspaceState("search", "");
  const [feedbackFilter, setFeedbackFilter] = useWorkspaceState<HistoryFeedbackFilter>("feedbackFilter", "all");
  const [safetyFilter, setSafetyFilter] = useWorkspaceState<HistorySafetyFilter>("safetyFilter", "all");
  const [sort, setSort] = useWorkspaceState<HistorySortState>("sort", { key: "created_at", direction: "desc" });
  const [selectedId, setSelectedId] = useWorkspaceState("selectedId", "");
  const [detailTab, setDetailTab] = useWorkspaceState<HistoryDetailTab>("detailTab", "overview");
  // ページ番号と表示件数は作業状態に残し、再読込・画面の移動のあとも同じページに戻る（#1764 / #1891）。
  const pages = useWorkspacePaging("listPaging");
  const { offset: pageOffset, pageSize } = pages;
  // 表示している一覧の取得条件（絞り込み・並べ替え・ページ）。取得に成功したときだけ入る。
  const [loadedRequest, setLoadedRequest] = useState("");
  const [total, setTotal] = useState(0);
  const [unfilteredTotal, setUnfilteredTotal] = useState(0);
  const loadSequence = useRef(0);
  const filterSignature = JSON.stringify([feedbackFilter, safetyFilter, search]);
  const requestSignature = JSON.stringify([
    feedbackFilter,
    safetyFilter,
    search,
    sort.key,
    sort.direction,
    pageOffset,
    pageSize,
  ]);
  const previousFilters = useRef(filterSignature);
  const { abortAll, run: runScopedRequest } = useRequestScope();

  // 今の条件のページを取り直す。loading / message は呼び出し側で先に設定しておく。
  // state の更新は応答の callback の中だけで行う（effect からも呼ぶため）。
  const fetchHistory = (announce: boolean) => {
    const sequence = loadSequence.current + 1;
    loadSequence.current = sequence;
    return runScopedRequest(async (signal) => {
      const data = await apiGet<HistoryData>(
        historyRequestUrl({
          offset: pageOffset,
          limit: pageSize,
          search,
          feedback: feedbackFilter,
          safety: safetyFilter,
          sort,
        }),
        { signal }
      );
      if (signal.aborted || sequence !== loadSequence.current) return;
      if (pageOffset > 0 && data.items.length === 0) {
        // 復元したページが無くなった（履歴が減った）。空のページを出さず、最後のページを取り直す。
        pages.setPage(Math.max(1, Math.ceil(data.total / pageSize)));
        return;
      }
      setItems(data.items);
      setLoadedRequest(requestSignature);
      setTotal(data.total);
      setUnfilteredTotal(data.unfiltered_total ?? data.total);
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
        if (pageOffset > 0 && err instanceof ApiError && err.status === 422) {
          // 復元したページの位置を読めない（範囲の外の件数など）。先頭のページから取り直す。
          pages.reset();
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
    await fetchHistory(announce);
  };

  // 絞り込み・並べ替え・ページが変わったレンダーで読み込み中の表示にし（effect で setState しない）、取得は
  // effect で行う。取得の関数は毎レンダー作り直すので、最新のものを ref から呼ぶ。
  const fetchHistoryRef = useRef(fetchHistory);
  useLayoutEffect(() => { fetchHistoryRef.current = fetchHistory; });
  const requestChanged = useValuesChanged([requestSignature]);
  if (requestChanged) {
    // 最初のレンダーは初回の読込（ヘッダーが回る）。以降の条件・ページの変更はボタンを回さない（#819）。
    if (filtersSeen) setLoadOrigin(null);
    else setFiltersSeen(true);
    setLoading(true);
    setMessage("");
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
    // requestSignature は絞り込み・並べ替え・ページの JSON で、そのどれかが変わったときに取り直す。
    // filterSignature は絞り込みが変わったときの選択の解除に使う。abortAll と setter は固定の関数。
  }, [requestSignature, filterSignature, abortAll, setDetailTab, setSelectedId]);

  const selectedItem = items.find((item) => item.id === selectedId) ?? null;
  const hasActiveFilters = Boolean(search.trim()) || feedbackFilter !== "all" || safetyFilter !== "all";
  // スケルトンで覆うのは、まだ 1 度も一覧を取得できていない初回の読み込みだけ（loadedRequest は取得の成功で入る）。
  // 絞り込みの結果が 0 件のあとの取り直しでも、絞り込みの欄を含む一覧を出したまま処理中の表示にする。
  // スケルトンへ置き換えると、入力中の検索欄や開いている選択欄が作り直されてフォーカスを失う（#739）。
  const initialLoading = loading && !loadedRequest;
  // 今の絞り込みの結果をまだ取得できていないまま取得が失敗した（初回の失敗・絞り込みを変えた後の失敗）。
  // 前の条件の一覧・件数を今の条件の結果として出さず、空の案内（履歴はまだありません・条件に一致しない）
  // とも取り違えさせない。同じ条件の「最新情報を取得」の失敗は前の内容を残す（messaging §3.6 / §3.7。#912）。
  const listUnavailable = !loading && Boolean(message) && loadedRequest !== requestSignature;

  // 絞り込み・並べ替えを変えたら先頭のページへ戻す（ページの位置は条件ごとに意味が変わる）。
  const toggleSort = (key: HistorySortKey) => {
    pages.reset();
    // 並べ替えると先頭のページの内容が変わるので、その先頭の履歴を選ぶ。
    setSelectedId("");
    setDetailTab("overview");
    setSort((current) => ({
      key,
      direction: current.key === key && current.direction === "asc" ? "desc" : "asc",
    }));
  };

  const changeSearch = (value: string) => {
    if (value !== search) pages.reset();
    setSearch(value);
  };
  const changeFeedbackFilter = (value: HistoryFeedbackFilter) => {
    if (value !== feedbackFilter) pages.reset();
    setFeedbackFilter(value);
  };
  const changeSafetyFilter = (value: HistorySafetyFilter) => {
    if (value !== safetyFilter) pages.reset();
    setSafetyFilter(value);
  };

  const clearFilters = () => {
    pages.reset();
    setSearch("");
    setFeedbackFilter("all");
    setSafetyFilter("all");
  };

  // ページ・表示件数を変えたら、移った先の先頭の履歴を選ぶ（前のページの選択を出したままにしない）。
  const goToPage = (page: number) => {
    setSelectedId("");
    setDetailTab("overview");
    pages.setPage(page);
  };
  const changePageSize = (size: number) => {
    setSelectedId("");
    setDetailTab("overview");
    pages.setPageSize(size);
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
              <span>{t("common.retry")}</span>
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
              items={listUnavailable ? [] : items}
              selectedId={selectedItem?.id ?? ""}
              search={search}
              feedbackFilter={feedbackFilter}
              safetyFilter={safetyFilter}
              sort={sort}
              onSearchChange={changeSearch}
              onFeedbackFilterChange={changeFeedbackFilter}
              onSafetyFilterChange={changeSafetyFilter}
              onSortChange={toggleSort}
              onSelect={selectItem}
              onClearFilters={clearFilters}
              total={total}
              unfilteredTotal={unfilteredTotal}
              hasActiveFilters={hasActiveFilters}
              offset={pageOffset}
              pageSize={pageSize}
              refreshing={loading}
              unavailable={listUnavailable}
              onPageChange={goToPage}
              onPageSizeChange={changePageSize}
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
