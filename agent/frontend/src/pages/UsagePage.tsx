import { useMemo, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  PageBody,
  PageHeader,
  SelectField,
  Skeleton,
  TabPanel,
  TableSkeleton,
  Tabs,
  type DataTableColumn,
  useActionPending,
} from "@engchina/production-ready-ui";

import { PagedDataTable, QueryState } from "@/components/ListViews";
import { ReportSourceNote } from "@/components/ReportSourceNote";
import { agentApi, REPORT_PERIOD_DAYS, type UsagePeriodDays, type UsageReport, type UsageTotals } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import { useWorkspaceState, type WorkspaceValidator } from "@/lib/workspace-state";

// 利用状況（#772）。業務 Agent の Run が使ったモデルの量を、期間・業務 Agent・利用者・モデル・日ごとに見る。
// 金額には換算しない（モデルの単価は契約で変わるため）。期間は 365 日まで。Oracle の構成は保存した
// Run の履歴（AGENT_RUN_FACTS）を SQL で集計する（#794）。

const PERIODS: readonly UsagePeriodDays[] = REPORT_PERIOD_DAYS;
const VIEWS = ["agent", "user", "model", "day"] as const;
type UsageView = (typeof VIEWS)[number];

const isPeriod: WorkspaceValidator<UsagePeriodDays> = (value): value is UsagePeriodDays =>
  PERIODS.includes(value as UsagePeriodDays);
const isView: WorkspaceValidator<UsageView> = (value): value is UsageView => VIEWS.includes(value as UsageView);

/** 日を区切るタイムゾーン（このブラウザ）。取れない環境は backend の既定（Asia/Tokyo）。 */
function viewerTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Tokyo";
  } catch {
    return "Asia/Tokyo";
  }
}

export function UsagePage() {
  const [days, setDays] = useWorkspaceState("usage", "days", 30 as UsagePeriodDays, isPeriod);
  const [view, setView] = useWorkspaceState("usage", "view", "agent" as UsageView, isView);
  const timezone = useMemo(() => viewerTimezone(), []);
  const report = useQuery({
    queryKey: ["usage", days, timezone],
    queryFn: () => agentApi.getUsageReport(days, timezone),
    // 期間を変えている間は前の集計を出したまま取り直す（Skeleton に戻さない）。
    placeholderData: keepPreviousData,
  });
  // 「表示を更新」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();

  return (
    <>
      <PageHeader
        wide
        title={t("nav.usage")}
        subtitle={t("page.usage.subtitle")}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => report.refetch()),
          },
        ]}
      />
      <PageBody wide>
        <SelectField<string>
          id="usage-period"
          label={t("usage.period")}
          width="sm"
          value={String(days)}
          options={PERIODS.map((period) => ({ value: String(period), label: t(`usage.period.${period}` as I18nKey) }))}
          onValueChange={(value) => setDays(Number(value) as UsagePeriodDays)}
        />
        <QueryState query={report} loadingLabel={t("loading.usage")} testId="usage-loading" skeleton={<UsageSkeleton />}>
          {report.data ? <UsageContent report={report.data} view={view} onViewChange={setView} /> : null}
        </QueryState>
      </PageBody>
    </>
  );
}

function UsageSkeleton() {
  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="grid grid-cols-2 gap-4 pt-4 xl:grid-cols-3">
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className="h-16" />
          ))}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="pt-4">
          <TableSkeleton columns={6} />
        </CardContent>
      </Card>
    </div>
  );
}

function UsageContent({
  report,
  view,
  onViewChange,
}: {
  report: UsageReport;
  view: UsageView;
  onViewChange: (view: UsageView) => void;
}) {
  const { totals, previous } = report;
  const unrecorded = totals.runs - totals.runs_with_usage;
  // 更新前の backend が返す欠測の行もモデル件数に含めない。記録済みの0 token は残す。
  const models = report.by_model.filter((row) => row.runs_with_usage > 0);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{t("usage.summary.title")}</CardTitle>
          <CardDescription>
            {t("usage.summary.description", { since: formatDay(report.since), timezone: report.timezone })}
          </CardDescription>
          <ReportSourceNote source={report.source} />
        </CardHeader>
        <CardContent className="space-y-4">
          {/* 統計のタイルはカードの幅を等分する（6 個なので 2 / 3 列。design-system README §4）。 */}
          <div className="grid grid-cols-2 gap-x-5 gap-y-4 xl:grid-cols-3" data-testid="usage-summary">
            <Metric label={t("usage.metric.totalTokens")} value={totals.total_tokens} previous={previous.total_tokens} />
            <Metric label={t("usage.metric.inputTokens")} value={totals.input_tokens} previous={previous.input_tokens} />
            <Metric label={t("usage.metric.outputTokens")} value={totals.output_tokens} previous={previous.output_tokens} />
            <Metric label={t("usage.metric.averageTokens")} value={averageTokens(totals)} previous={averageTokens(previous)} />
            <Metric label={t("usage.metric.requests")} value={totals.requests} previous={previous.requests} />
            <Metric label={t("usage.metric.runs")} value={totals.runs} previous={previous.runs} />
          </div>
          {unrecorded > 0 ? (
            <p className="text-xs text-fg-muted" data-testid="usage-unrecorded">
              {t("usage.unrecorded", { count: formatNumber(unrecorded) })}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card className="min-w-0">
        <CardHeader>
          <CardTitle>{t("usage.breakdown.title")}</CardTitle>
          <CardDescription>{t("usage.breakdown.description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Tabs
            idPrefix="usage"
            ariaLabel={t("usage.breakdown.title")}
            value={view}
            onChange={(id) => onViewChange(id as UsageView)}
            items={[
              { id: "agent", label: t("usage.view.agent"), count: report.by_agent.length },
              { id: "user", label: t("usage.view.user"), count: report.by_user.length },
              { id: "model", label: t("usage.view.model"), count: models.length },
              { id: "day", label: t("usage.view.day") },
            ]}
          />
          {totals.runs === 0 ? (
            <EmptyState title={t("usage.empty.title")} hint={t("usage.empty.description")} />
          ) : (
            <>
              <TabPanel idPrefix="usage" id="agent" value={view}>
                <BreakdownTable
                  rows={report.by_agent}
                  total={totals.total_tokens}
                  rowKey={(row) => row.agent_id}
                  label={t("usage.table.agent")}
                  nameHeader={t("usage.column.agent")}
                  resetKey={`agent:${report.days}`}
                  name={(row) => (
                    <>
                      <span className="block break-words font-medium text-fg [overflow-wrap:anywhere]">
                        {row.agent_name || t("usage.agentDeleted")}
                      </span>
                      <span className="block break-all font-mono text-xs text-fg-muted">{row.agent_id}</span>
                    </>
                  )}
                />
              </TabPanel>
              <TabPanel idPrefix="usage" id="user" value={view}>
                <BreakdownTable
                  rows={report.by_user}
                  total={totals.total_tokens}
                  rowKey={(row) => row.user_uuid ?? "none"}
                  label={t("usage.table.user")}
                  nameHeader={t("usage.column.user")}
                  resetKey={`user:${report.days}`}
                  name={(row) => (
                    <span className="break-words font-medium text-fg [overflow-wrap:anywhere]">
                      {row.user_uuid ? row.display_name || t("usage.userUnknown") : t("usage.userNone")}
                    </span>
                  )}
                />
              </TabPanel>
              <TabPanel idPrefix="usage" id="model" value={view} className="space-y-3">
                <p className="text-xs text-fg-muted">{t("usage.model.description")}</p>
                {models.length === 0 ? (
                  <EmptyState title={t("usage.model.empty.title")} hint={t("usage.model.empty.description")} />
                ) : (
                  <BreakdownTable
                    rows={models}
                    total={totals.total_tokens}
                    rowKey={(row) => row.model || "none"}
                    label={t("usage.table.model")}
                    nameHeader={t("usage.column.model")}
                    resetKey={`model:${report.days}`}
                    name={(row) =>
                      row.model ? (
                        <span className="break-all font-mono text-xs text-fg">{row.model}</span>
                      ) : (
                        <span className="text-fg-muted">{t("usage.modelNone")}</span>
                      )
                    }
                  />
                )}
              </TabPanel>
              <TabPanel idPrefix="usage" id="day" value={view}>
                <BreakdownTable
                  // 新しい日を上に出す（API は古い順）。
                  rows={[...report.by_day].reverse()}
                  total={totals.total_tokens}
                  rowKey={(row) => row.day}
                  label={t("usage.table.day")}
                  nameHeader={t("usage.column.day")}
                  resetKey={`day:${report.days}`}
                  name={(row) => <span className="tabular-nums text-fg">{formatDay(row.day)}</span>}
                />
              </TabPanel>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Metric({ label, value, previous }: { label: string; value: number; previous: number }) {
  return (
    <div className="min-w-0 border-l-2 border-accent-emphasis pl-3">
      <p className="truncate text-xs text-fg-muted" title={label}>
        {label}
      </p>
      <p className="mt-0.5 text-xl font-semibold tabular-nums text-fg">{formatNumber(value)}</p>
      <p className="mt-0.5 text-xs tabular-nums text-fg-muted">{deltaLabel(value, previous)}</p>
    </div>
  );
}

/** 利用量を記録した Run 1 件あたりの合計 token（四捨五入）。 */
function averageTokens(totals: UsageTotals): number {
  return totals.runs_with_usage > 0 ? Math.round(totals.total_tokens / totals.runs_with_usage) : 0;
}

function deltaLabel(value: number, previous: number): string {
  const delta = value - previous;
  if (delta === 0) return t("usage.delta.same");
  return t("usage.delta", { value: `${delta > 0 ? "+" : "−"}${formatNumber(Math.abs(delta))}` });
}

function BreakdownTable<T extends UsageTotals>({
  rows,
  total,
  rowKey,
  label,
  nameHeader,
  name,
  resetKey,
}: {
  rows: T[];
  total: number;
  rowKey: (row: T) => string;
  label: string;
  nameHeader: string;
  name: (row: T) => ReactNode;
  resetKey: string;
}) {
  const columns: DataTableColumn<T>[] = [
    { key: "name", header: nameHeader, rowHeader: true, className: "min-w-40", render: name },
    { key: "runs", header: t("usage.metric.runs"), align: "right", render: (row) => numberCell(row.runs) },
    { key: "requests", header: t("usage.metric.requests"), align: "right", render: (row) => numberCell(row.requests) },
    {
      key: "input_tokens",
      header: t("usage.metric.inputTokens"),
      align: "right",
      render: (row) => numberCell(row.input_tokens),
    },
    {
      key: "output_tokens",
      header: t("usage.metric.outputTokens"),
      align: "right",
      render: (row) => numberCell(row.output_tokens),
    },
    {
      key: "total_tokens",
      header: t("usage.metric.totalTokens"),
      align: "right",
      render: (row) => <span className="font-medium tabular-nums text-fg">{formatNumber(row.total_tokens)}</span>,
    },
    {
      key: "share",
      header: t("usage.column.share"),
      className: "w-40",
      render: (row) => <ShareBar value={row.total_tokens} total={total} />,
    },
  ];
  return (
    <PagedDataTable<T>
      rows={rows}
      columns={columns}
      getRowKey={rowKey}
      ariaLabel={label}
      tableClassName="w-full min-w-[54rem]"
      resetKey={resetKey}
    />
  );
}

function numberCell(value: number) {
  return <span className="tabular-nums text-fg">{formatNumber(value)}</span>;
}

/** 期間の合計 token に対する割合（数字を併記し、棒だけに頼らない）。 */
function ShareBar({ value, total }: { value: number; total: number }) {
  const ratio = total > 0 ? value / total : 0;
  const percent = Math.round(ratio * 1000) / 10;
  return (
    <div className="grid grid-cols-[minmax(3rem,1fr)_auto] items-center gap-2 text-xs">
      <span className="h-1.5 overflow-hidden rounded-full bg-surface-hover" aria-hidden>
        <span className="block h-full rounded-full bg-accent-emphasis" style={{ width: `${Math.min(100, percent)}%` }} />
      </span>
      <span className="w-12 text-right tabular-nums text-fg-muted">{`${percent}%`}</span>
    </div>
  );
}

const dayFormat = new Intl.DateTimeFormat("ja-JP", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });

/** `YYYY-MM-DD`（その日）か ISO の日時を「YYYY/MM/DD(曜)」へ。 */
function formatDay(value: string): string {
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
  return Number.isNaN(date.getTime()) ? value : dayFormat.format(date);
}
