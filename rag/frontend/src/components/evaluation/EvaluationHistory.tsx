import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  DEFAULT_PAGE_SIZE,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListToolbar,
  OffsetPagination,
  PAGE_SIZE_OPTIONS,
  RowTitleButton,
  StatusBadge,
  TableSkeleton,
  TimedLoadingState,
} from "@production-ready/ui";
import { History } from "lucide-react";

import { ApiErrorState, EmptyState } from "@/components/StateViews";
import type { EvaluationJobSummary } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import { listCountLabels, paginationLabels } from "@/lib/pagination-labels";
import { useEvaluationJobs } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { useWorkspaceState } from "@/lib/workspace-state";

import {
  EVALUATION_JOB_STATUS_VARIANT,
  evaluationJobStatusLabel,
  evaluationHistoryProfileLabel,
} from "./evaluation-job";

interface HistoryPaging {
  offset: number;
  size: number;
}

function isHistoryPaging(value: unknown): value is HistoryPaging {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.offset === "number" &&
    Number.isInteger(candidate.offset) &&
    candidate.offset >= 0 &&
    typeof candidate.size === "number" &&
    (PAGE_SIZE_OPTIONS as readonly number[]).includes(candidate.size)
  );
}

/** 評価を直接開く URL（#4811。共有・新しいタブで開ける）。 */
export function evaluationJobHref(jobId: string): string {
  return `${APP_ROUTES.evaluation}?${new URLSearchParams({ job: jobId })}`;
}

/**
 * 過去の評価の一覧（#4811）。見られる評価（使った検索・回答プロファイルを今使える評価と、自分が実行した
 * 評価）を新しい順に出し、行を選ぶと保存済みの結果を下に出す（モデルは呼び直さない）。
 */
export function EvaluationHistory({
  selectedJobId,
  onSelect,
}: {
  selectedJobId: string | null;
  onSelect: (jobId: string) => void;
}) {
  // ページと表示件数は作業状態に残す（ui.md「ページング」）。
  const [paging, setPaging] = useWorkspaceState<HistoryPaging>(
    "evaluation.historyPaging",
    { offset: 0, size: Number(DEFAULT_PAGE_SIZE) },
    isHistoryPaging
  );
  const query = useEvaluationJobs(paging.offset, paging.size);
  const page = query.data;
  return (
    <Card className="min-w-0" data-testid="evaluation-history">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <History size={16} className="text-accent-fg" aria-hidden />
          {t("evaluation.history.title")}
        </CardTitle>
        <CardDescription>{t("evaluation.history.description")}</CardDescription>
      </CardHeader>
      <CardContent className="grid min-w-0 gap-3">
        {query.isPending ? (
          <TimedLoadingState
            label={t("evaluation.history.loading")}
            operationKey="evaluation-history-load"
            testId="evaluation-history-loading"
          >
            <TableSkeleton columns={4} />
          </TimedLoadingState>
        ) : query.isError ? (
          <ApiErrorState
            error={query.error}
            fallback={t("evaluation.history.loadError")}
            onRetry={() => void query.refetch()}
          />
        ) : page && page.total === 0 ? (
          <EmptyState
            title={t("evaluation.history.empty")}
            hint={t("evaluation.history.emptyHint")}
          />
        ) : page ? (
          <>
            <ListToolbar
              count={{ total: page.total, labels: listCountLabels() }}
              testId="evaluation-history-toolbar"
            />
            <DataTable<EvaluationJobSummary>
              ariaLabel={t("evaluation.history.title")}
              rows={page.items}
              getRowKey={(job) => job.job_id}
              onRowClick={(job) => onSelect(job.job_id)}
              selectedRowKey={selectedJobId}
              rowProps={(job) => ({
                className: INFORMATION_TABLE_ROW_CLASS,
                "data-testid": `evaluation-history-row-${job.job_id}`,
              })}
              stickyHeader
              visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
              scrollAriaLabel={t("evaluation.history.scrollLabel")}
              scrollTestId="evaluation-history-scroll-region"
              tableClassName="w-full"
              columns={[
                {
                  key: "created_at",
                  header: t("evaluation.history.column.createdAt"),
                  rowHeader: true,
                  className: "align-top",
                  render: (job) => (
                    <RowTitleButton
                      title={formatDateTime(job.created_at)}
                      // 375px では列を畳み、プロファイル・状態・判定を日時の下にまとめる。
                      subtitle={
                        <span className="lg:hidden">
                          {evaluationHistoryProfileLabel(job)} · {evaluationJobStatusLabel(job.status)}
                          {job.passed === null ? "" : ` · ${passedLabel(job.passed)}`}
                          {job.compare_passed == null ? "" : ` · ${t("evaluation.history.comparePassed", { label: passedLabel(job.compare_passed) })}`}
                        </span>
                      }
                      href={evaluationJobHref(job.job_id)}
                      current={job.job_id === selectedJobId}
                      aria-label={t("evaluation.history.open", {
                        time: formatDateTime(job.created_at),
                      })}
                      onClick={() => onSelect(job.job_id)}
                    />
                  ),
                },
                {
                  key: "profile",
                  header: t("evaluation.history.column.profile"),
                  headerClassName: "hidden lg:table-cell",
                  className: "hidden break-words align-top lg:table-cell",
                  render: (job) => evaluationHistoryProfileLabel(job),
                },
                {
                  key: "status",
                  header: t("evaluation.history.column.status"),
                  headerClassName: "hidden md:table-cell",
                  className: "hidden whitespace-nowrap align-top md:table-cell",
                  render: (job) => (
                    <StatusBadge
                      variant={EVALUATION_JOB_STATUS_VARIANT[job.status]}
                      label={evaluationJobStatusLabel(job.status)}
                    />
                  ),
                },
                {
                  key: "cases",
                  header: t("evaluation.history.column.cases"),
                  headerClassName: "hidden md:table-cell",
                  className: "tnum hidden whitespace-nowrap align-top md:table-cell",
                  render: (job) => t("evaluation.history.cases", { count: job.case_count }),
                },
                {
                  key: "passed",
                  header: t("evaluation.history.column.passed"),
                  headerClassName: "hidden md:table-cell",
                  className: "hidden whitespace-nowrap align-top md:table-cell",
                  // 比べた評価は、比べた条件の判定（技術エラー・閾値の不合格）も並べる（#5074）。
                  render: (job) =>
                    job.passed === null ? (
                      "—"
                    ) : (
                      <span className="inline-flex flex-wrap gap-1.5">
                        <StatusBadge
                          variant={job.passed ? "success" : "danger"}
                          label={passedLabel(job.passed)}
                        />
                        {job.compare_passed == null ? null : (
                          <StatusBadge
                            variant={job.compare_passed ? "success" : "danger"}
                            label={t("evaluation.history.comparePassed", { label: passedLabel(job.compare_passed) })}
                          />
                        )}
                      </span>
                    ),
                },
                {
                  key: "actor",
                  header: t("evaluation.history.column.actor"),
                  headerClassName: "hidden lg:table-cell",
                  className: "hidden break-words align-top lg:table-cell",
                  render: (job) => job.actor_display_name ?? "—",
                },
              ]}
            />
            <OffsetPagination
              offset={paging.offset}
              limit={paging.size}
              total={page.total}
              count={page.items.length}
              onPageChange={(next) =>
                setPaging((current) => ({ ...current, offset: (next - 1) * current.size }))
              }
              pageSizeOptions={[...PAGE_SIZE_OPTIONS]}
              onPageSizeChange={(size) => setPaging({ size, offset: 0 })}
              disabled={query.isPlaceholderData}
              labels={paginationLabels()}
              ariaLabel={t("evaluation.history.paginationLabel")}
              testId="evaluation-history-pagination"
            />
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}

function passedLabel(passed: boolean): string {
  return passed ? t("evaluation.status.passed") : t("evaluation.status.failed");
}
