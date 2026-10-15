import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Copy, RefreshCw, RotateCcw } from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  ApiErrorBanner,
  Banner,
  Button,
  ContentActionBar,
  EmptyState,
  ObjectActionBar,
  PageBody,
  PageHeader,
  RunDetailLayout,
  RunDetailSection,
  RunDetailSkeleton,
  RunFactList,
  RunSummary,
  RunTimeline,
  StatusBadge,
  TimedLoadingState,
  toast,
  type EntityAction,
  type RunFact,
  type RunTimelineStep,
} from "@production-ready/ui";
import { useAuth } from "@/features/security/AuthProvider";
import { canOpenRoute } from "@/features/security/route-permissions";
import { ApiError, apiGet, isAbortError } from "@/lib/api";
import { copyTextToClipboard } from "@/lib/clipboard";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useRequestScope } from "@/lib/useRequestScope";
import { feedbackRatingLabel, userFeedbackRatingBadgeLabel } from "../feedbackLabels";
import {
  historyExecutor,
  historyKindDisplay,
  historyOwner,
  historyRunStatus,
  historyTimelineSteps,
} from "../historyManagementState";
import { engineLabel } from "../labels";
import { profileRecordDisplayLabel } from "../profileDisplay";
import { historyRerunUrl } from "../queryPrefillState";
import type { HistoryItem } from "../types";

/*
 * 実行履歴の全幅の詳細（`?id=<run_id>`。#1955。UX 契約 run-history.md「詳細」）。
 * 概要 → 処理の経過 → SQL と結果 → フィードバック → 技術的な詳細（畳む）を縦に 1 枚で読む（タブで隠さない）。
 * NL2SQL は回答の文を記録しないので「回答」の節は出さない。
 */

function SqlBlock({ sql, label, testId }: { sql: string; label: string; testId: string }) {
  return (
    // スクロール領域はキーボードでも操作できるよう focus 可能にする（WCAG 2.1.1）。
    <pre
      data-surface="code"
      tabIndex={0}
      role="region"
      aria-label={label}
      className="max-h-96 overflow-auto rounded-md border border-border bg-surface p-4 font-mono text-sm leading-6 text-fg"
      data-testid={testId}
    >
      <code>{sql}</code>
    </pre>
  );
}

function timelineSteps(item: HistoryItem): RunTimelineStep[] {
  // 段の記録は所要時間だけなので、処理の始まりからの位置を時刻にして線を描く（文字の所要時間は記録の値）。
  const origin = Date.parse(item.created_at) || 0;
  const at = (ms: number) => new Date(origin + ms).toISOString();
  return historyTimelineSteps(item).map((step) => ({
    id: step.id,
    label: step.label,
    status: step.status,
    startedAt: at(step.offsetMs),
    finishedAt: at(step.offsetMs + Math.max(0, step.durationMs)),
    durationMs: step.durationMs,
    level: step.level,
    children: step.error ? <p className="break-words text-sm text-fg [overflow-wrap:anywhere]">{step.error}</p> : undefined,
  }));
}

function HistoryRunView({ item }: { item: HistoryItem }) {
  const navigate = useNavigate();
  const { hasPermission } = useAuth();
  const canRerun = canOpenRoute(APP_ROUTES.query, hasPermission);
  const status = historyRunStatus(item);
  const sql = item.executable_sql || item.generated_sql;
  const steps = timelineSteps(item);
  const executed = !item.generation_only && item.safety_is_safe;

  const copySql = async () => {
    try {
      await copyTextToClipboard(sql);
      toast.success(t("history.copied"));
    } catch {
      toast.error(t("history.copyFailed"));
    }
  };

  const actions: EntityAction[] = [
    {
      id: "rerun",
      label: t("history.action.rerun"),
      icon: RotateCcw,
      onSelect: () => navigate(historyRerunUrl(item, APP_ROUTES.query)),
      visible: canRerun,
      testId: "history-detail-rerun",
    },
  ];

  const extraFacts: RunFact[] = [
    { id: "engine", label: t("history.detail.engine"), value: engineLabel(item.engine) },
    {
      id: "rows",
      label: t("history.detail.rows"),
      value: executed ? formatNumber(item.result_row_count) : t("history.notExecuted"),
    },
  ];

  const resultFacts: RunFact[] = [];
  if (item.rewritten_question.trim()) {
    resultFacts.push({ id: "rewritten", label: t("history.rewritten"), value: item.rewritten_question, wide: true });
  }
  resultFacts.push({
    id: "rows",
    label: t("history.detail.rows"),
    value: executed ? formatNumber(item.result_row_count) : t("history.notExecuted"),
  });
  if (item.result_columns.length > 0) {
    resultFacts.push({ id: "columns", label: t("history.detail.columns"), value: item.result_columns.join(", "), mono: true, wide: true });
  }

  const hasFeedback = Boolean(item.feedback_rating || item.feedback_comment.trim());
  const showGeneratedSql = Boolean(item.generated_sql.trim()) && item.generated_sql.trim() !== sql.trim();
  const engineErrors = (item.engine_timings ?? []).filter((timing) => timing.error?.trim());
  // MCP の呼び出し元（API キーの名前・Agent の Run の ID。#1986）は技術的な詳細に置く。
  const callerFacts: RunFact[] = [];
  if (item.api_key_name?.trim()) {
    callerFacts.push({ id: "api-key", label: t("history.detail.apiKey"), value: item.api_key_name });
  }
  if (item.agent_run_id?.trim()) {
    callerFacts.push({ id: "agent-run", label: t("history.detail.agentRunId"), value: item.agent_run_id, mono: true });
  }

  return (
    <>
      <RunDetailLayout
        testId="history-detail"
        summary={
          <RunSummary
            testId="history-detail-summary"
            status={status}
            badges={
              item.feedback_rating ? (
                <StatusBadge
                  variant={item.feedback_rating === "good" ? "success" : "warning"}
                  label={userFeedbackRatingBadgeLabel(item.feedback_rating)}
                />
              ) : null
            }
            question={item.question}
            startedAt={item.created_at}
            durationMs={item.elapsed_ms ?? null}
            kind={historyKindDisplay(item)}
            executor={historyExecutor(item)}
            owner={historyOwner(item)}
            target={{ label: t("history.detail.target"), value: profileRecordDisplayLabel(item) || "—" }}
            runId={item.id}
            extraFacts={extraFacts}
            actions={
              canRerun ? (
                <ObjectActionBar actions={actions} ariaLabel={t("history.detail.actions")} testId="history-detail-actions" />
              ) : null
            }
            alert={
              status === "blocked" ? (
                <div data-testid="history-detail-blocked">
                  <Banner severity="warning" title={t("history.detail.blocked.title")}>
                    {t("history.detail.blocked.body")}
                  </Banner>
                </div>
              ) : null
            }
            labels={{ self: t("history.self") }}
          />
        }
        timeline={
          steps.length > 0 ? (
            <RunDetailSection title={t("history.detail.timeline")} testId="history-detail-timeline">
              <RunTimeline steps={steps} totalDurationMs={item.elapsed_ms ?? null} />
            </RunDetailSection>
          ) : null
        }
        evidence={
          <RunDetailSection
            title={t("history.detail.sqlSection")}
            testId="history-detail-sql"
          >
            {sql.trim() ? (
              <>
                <ContentActionBar ariaLabel={t("history.detail.sqlActions")} testId="history-detail-sql-actions">
                  <Button type="button" variant="secondary" size="sm" icon={Copy} onClick={() => void copySql()}>
                    {t("history.action.copySql")}
                  </Button>
                </ContentActionBar>
                <SqlBlock sql={sql} label={t("history.detail.sqlRegion")} testId="history-detail-sql-code" />
              </>
            ) : (
              <p className="text-sm text-fg-muted">{t("history.detail.noSql")}</p>
            )}
            <RunFactList facts={resultFacts} testId="history-detail-result" />
          </RunDetailSection>
        }
        feedback={
          hasFeedback ? (
            <RunDetailSection title={t("history.detail.feedback")} testId="history-detail-feedback">
              <RunFactList
                facts={[
                  { id: "rating", label: t("history.column.rating"), value: feedbackRatingLabel(item.feedback_rating) },
                  ...(item.feedback_comment.trim()
                    ? [{ id: "comment", label: t("history.detail.feedbackComment"), value: item.feedback_comment, wide: true }]
                    : []),
                ]}
              />
            </RunDetailSection>
          ) : null
        }
        technical={
          showGeneratedSql || engineErrors.length > 0 || callerFacts.length > 0 ? (
            <RunDetailSection title={t("history.detail.technical")} collapsible testId="history-detail-technical">
              {callerFacts.length > 0 ? <RunFactList facts={callerFacts} testId="history-detail-caller" /> : null}
              {showGeneratedSql ? (
                <div className="grid min-w-0 gap-1">
                  <p className="text-xs text-fg-muted">{t("history.detail.generatedSql")}</p>
                  <SqlBlock sql={item.generated_sql} label={t("history.detail.generatedSql")} testId="history-detail-generated-sql" />
                </div>
              ) : null}
              {engineErrors.length > 0 ? (
                <div className="grid min-w-0 gap-1">
                  <p className="text-xs text-fg-muted">{t("history.detail.engineAttempts")}</p>
                  <pre data-surface="code" className="overflow-auto rounded-md border border-border bg-surface p-3 font-mono text-xs leading-5 text-fg">
                    {JSON.stringify(item.engine_timings, null, 2)}
                  </pre>
                </div>
              ) : null}
            </RunDetailSection>
          ) : null
        }
      />
    </>
  );
}

export function HistoryRunDetail({ historyId, onBack }: { historyId: string; onBack: () => void }) {
  const [item, setItem] = useState<HistoryItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [announce, setAnnounce] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const sequence = useRef(0);
  const { abortAll, run } = useRequestScope();

  const fetchItem = (notify: boolean) => {
    const current = sequence.current + 1;
    sequence.current = current;
    return run(async (signal) => {
      const data = await apiGet<HistoryItem>(`/api/nl2sql/history/${encodeURIComponent(historyId)}`, { signal });
      if (signal.aborted || current !== sequence.current) return;
      setItem(data);
      setError(null);
      if (notify) toast.success(t("common.action.refreshed"));
    })
      .catch((err: unknown) => {
        if (isAbortError(err) || current !== sequence.current) return;
        setError(err);
      })
      .finally(() => {
        if (current === sequence.current) setLoading(false);
      });
  };
  const fetchRef = useRef(fetchItem);
  useLayoutEffect(() => {
    fetchRef.current = fetchItem;
  });
  useEffect(() => {
    void fetchRef.current(false);
    return () => {
      sequence.current += 1;
      abortAll();
    };
  }, [historyId, abortAll]);

  const refresh = () => {
    setLoading(true);
    setAnnounce(true);
    void fetchItem(true);
  };

  const notFound = error instanceof ApiError && (error.status === 404 || error.status === 403);

  return (
    <>
      <PageHeader
        wide
        title={t("history.detail.title")}
        back={{
          label: t("history.detail.back"),
          ariaLabel: t("history.detail.backAria"),
          onClick: onBack,
          testId: "history-detail-back",
        }}
        actions={[
          {
            id: "refresh",
            kind: "utility",
            label: t("common.action.refresh"),
            icon: RefreshCw,
            onClick: refresh,
            loading: loading && announce,
            disabled: loading && !announce,
          },
        ]}
      />
      <PageBody wide>
        {loading && !item ? (
          <TimedLoadingState
            label={t("history.detail.loading")}
            operationKey={`history-detail-${historyId}`}
            placement="panel"
            testId="history-detail-loading"
            framed={false}
            activityIcon="spinner"
          >
            <RunDetailSkeleton />
          </TimedLoadingState>
        ) : notFound ? (
          <div data-testid="history-detail-not-found">
            <EmptyState
              title={t("history.detail.notFound.title")}
              hint={t("history.detail.notFound.hint")}
              action={
                <Button type="button" variant="secondary" size="sm" onClick={onBack}>
                  {t("history.detail.back")}
                </Button>
              }
            />
          </div>
        ) : (
          <div className="grid min-w-0 gap-4">
            {error !== null ? (
              <ApiErrorBanner
                error={error}
                fallback={t("history.error.detail")}
                testId="history-detail-error"
                action={
                  <Button type="button" variant="secondary" size="sm" icon={RefreshCw} loading={loading} onClick={refresh}>
                    {t("common.retry")}
                  </Button>
                }
              />
            ) : null}
            {item ? <HistoryRunView item={item} /> : null}
          </div>
        )}
      </PageBody>
    </>
  );
}
