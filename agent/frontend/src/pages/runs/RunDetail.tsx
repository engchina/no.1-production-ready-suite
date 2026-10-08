import { useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, FileText, GitBranch, PlayCircle, RefreshCw, Server, ShieldAlert } from "lucide-react";
import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Disclosure,
  EmptyState,
  FormStatus,
  ListSkeleton,
  ObjectActionBar,
  ProcessingIndicator,
  StatusBadge,
  InfoTip,
  ToggleChip,
  Tabs,
  TabPanel,
  type EntityAction,
  type StatusVariant,
} from "@engchina/production-ready-ui";
import {
  agentApi,
  type Artifact,
  type RunAuditData,
  type RunEvent,
  type RunState,
  type ToolAuditRecord,
} from "@/lib/api";
import { QueryState } from "@/components/ListViews";
import { AnswerFeedback } from "@/components/chat/AnswerFeedback";
import { AnswerReviewSections, LimitNote } from "@/components/chat/AnswerReview";
import { AnswerBody, ResultTable } from "@/components/chat/ResultTables";
import { AddToEvaluationCase, useCanEditEvaluationSets } from "@/components/evaluation/AddToEvaluationCase";
import { ANSWER_VALIDATION_KIND, SUPPORT_TASK_KIND, answerReview } from "@/lib/answer-review";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import { type AgentCapabilities } from "@/lib/permissions";
import { ragEvidenceItems } from "@/lib/rag-evidence";
import { RagFigureButton } from "@/components/evidence/RagFigure";
import { artifactTable, runToolResultTables, stepResultTable, type ToolResultTable } from "@/lib/run-tables";
import {
  approvalStatusView,
  artifactKindView,
  eventTypeView,
  permissionView,
  policyDecisionView,
  runStatusView,
  stepStatusView,
} from "@/lib/status-labels";
import {
  type RunEventSourceState,
  type RunStreamMode,
  type RunWebSocketState,
  type WebSocketStreamStatus,
  websocketStatusVariant,
} from "@/pages/runs/run-event-stream";
import { JsonPanel, JsonPreview, MetricPill, formatDate } from "@/pages/shared/page-helpers";

/** Run の取消・再開の可否。一覧の行メニューと詳細の ObjectActionBar・ストリーム操作で同じ判定を使う。 */
export function runCapabilities(run: RunState): {
  canCancel: boolean;
  canResume: boolean;
} {
  // 組み込み Runtime（#754）は承認がすべて決まると自動で再開する。手動の再開は、承認が決まり SDK の状態を
  // 保存したまま止まっている Run（待ち）を起動し直すときだけ使う（backend の `builtin_resume_pending`。
  // 旧エンジンの Run の再開は backend が必ず断る。#1119）。
  const resumePending =
    run.runtime_id === "builtin" && run.status === "queued" && typeof run.metadata?._builtin_sdk_state === "string";
  return {
    canCancel: ["queued", "running", "waiting_approval"].includes(run.status),
    canResume: resumePending,
  };
}

export function RunDetail({
  run,
  agentName,
  actions,
  streamMode,
  onStreamModeChange,
  websocketState,
  sseState,
  capabilities,
}: {
  run: RunState;
  /** 業務 Agent の名前（一覧に無いときは ID）。 */
  agentName: string;
  actions: EntityAction[];
  streamMode: RunStreamMode;
  onStreamModeChange: (mode: RunStreamMode) => void;
  websocketState: RunWebSocketState;
  sseState: RunEventSourceState;
  capabilities: AgentCapabilities;
}) {
  const [selectedTab, setTab] = useState("result");
  const tab = selectedTab === "audit" && !capabilities.viewAudit ? "result" : selectedTab;
  const queryClient = useQueryClient();
  const structured = structuredFallback(run);
  const pendingApproval = run.approvals.find((approval) => approval.status === "pending");
  const canAddCase = useCanEditEvaluationSets();

  return (
    <section className="space-y-5" aria-label={t("run.detail")}>
      <Card>
        <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle>{t("run.detail")}</CardTitle>
              <StatusBadge {...runStatusView(run.status)} />
            </div>
            <CardDescription className="break-words [overflow-wrap:anywhere]">{run.id}</CardDescription>
          </div>
          <ObjectActionBar
            actions={actions}
            ariaLabel={t("run.actions")}
            moreLabel={t("common.moreActions")}
            testId="run-object-actions"
          />
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm leading-6 text-fg">{run.goal}</p>
          <RunProgressIndicator run={run} />
          <div className="grid gap-2 text-xs text-fg-muted sm:grid-cols-2">
            <span>{`${t("run.form.agent")}: ${agentName}`}</span>
            <span>{`${t("run.runtime")}: ${run.runtime_id === "builtin" ? t("runtime.builtin.title") : run.runtime_id}`}</span>
            <span>{`${t("common.createdAt")}: ${formatDate(run.created_at)}`}</span>
            <span>{`${t("common.updatedAt")}: ${formatDate(run.updated_at)}`}</span>
            {/* モデルの利用量（承認待ちからの再開を含めた累計。#772）。 */}
            <span className="sm:col-span-2" data-testid="run-usage">
              {`${t("run.usage")}: ${
                run.usage
                  ? t("run.usage.summary", {
                      model: run.usage.model || t("usage.modelNone"),
                      requests: formatNumber(run.usage.requests),
                      input: formatNumber(run.usage.input_tokens),
                      output: formatNumber(run.usage.output_tokens),
                      total: formatNumber(run.usage.total_tokens),
                    })
                  : t("run.usage.none")
              }`}
            </span>
          </div>
        </CardContent>
      </Card>

      {run.status === "waiting_approval" ? (
        <Banner severity="warning" title={t("run.waitingApproval")}>
          {pendingApproval?.tool_call.name}
        </Banner>
      ) : null}
      <Tabs
        value={tab}
        onChange={setTab}
        ariaLabel={t("run.detailTabs")}
        items={[
          { id: "result", label: t("run.tab.result") },
          { id: "process", label: t("run.tab.process") },
          ...(capabilities.viewAudit ? [{ id: "audit", label: t("run.audit") }] : []),
        ]}
      />
      <TabPanel id="result" value={tab} className="space-y-5">
        <ArtifactsPanel run={run} showRawReview={capabilities.admin} onShowProcess={() => setTab("process")} />
        {structured ? <StructuredResultCard key={run.id} run={run} table={structured} /> : null}
        {capabilities.admin && run.status === "completed" && run.artifacts.some((item) => item.kind === "answer") ? (
          <Card>
            <CardHeader>
              <CardTitle>{t("run.reviewTitle")}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* 管理者の評価（#774）。Agent 管理の権限で、回答が出た Run に付ける（本人の評価とは別）。 */}
              <AnswerFeedback
                runId={run.id}
                current={run.admin_review ?? null}
                mode="admin"
                onSaved={() => {
                  void queryClient.invalidateQueries({ queryKey: ["runs"] });
                  void queryClient.invalidateQueries({
                    queryKey: ["evaluation-case-draft", run.id],
                  });
                }}
              />
              {/* 管理者の評価の下で、この Run の質問を評価ケースにする（品質評価の権限。評価の Run は除く。#810）。 */}
              {canAddCase && !run.metadata?.evaluation_job_id ? (
                <AddToEvaluationCase key={run.id} runId={run.id} testId="run-add-case" />
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </TabPanel>
      <TabPanel id="process" value={tab} className="space-y-5">
        <div className="min-w-0 space-y-5">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("run.steps")}</CardTitle>
            </CardHeader>
            <CardContent>
              {run.steps.length ? (
                <div className="space-y-3">
                  {run.steps.map((step) => (
                    <div key={step.id} className="min-w-0 rounded-md border border-border p-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium text-fg">{step.tool_call?.name ?? step.kind}</span>
                        <StatusBadge {...stepStatusView(step.status)} />
                      </div>
                      {step.tool_result?.error ? (
                        <p className="mt-2 text-xs text-danger-fg">{step.tool_result.error}</p>
                      ) : null}
                      {step.tool_result?.output ? (
                        <ToolOutput
                          output={step.tool_result.output}
                          table={stepResultTable(step)}
                          name={t("resultTable.toolName", { tool: step.tool_call?.name ?? step.kind })}
                          testId={`run-step-table-${step.id}`}
                        />
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState title={t("common.empty.title")} />
              )}
            </CardContent>
          </Card>

          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("run.timeline")}</CardTitle>
              <CardDescription>{t("run.timelineDescription")}</CardDescription>
            </CardHeader>
            <CardContent>
              <RunTimeline events={run.events} />
            </CardContent>
          </Card>
        </div>

        <Disclosure summary={t("run.stream")} description={t("run.streamDescription")}>
          <RunStreamControls
            mode={streamMode}
            onModeChange={onStreamModeChange}
            websocketState={websocketState}
            sseState={sseState}
          />
        </Disclosure>
      </TabPanel>
      {capabilities.viewAudit ? (
        <TabPanel id="audit" value={tab}>
          {tab === "audit" ? <AuditPanel runId={run.id} /> : null}
        </TabPanel>
      ) : null}
    </section>
  );
}

/**
 * 実行中の Run の経過時間（durable job。messaging.md §3.7 の placement="job"）。
 * 開始時刻はサーバーの created_at を使い、Run を開き直しても経過時間が 0 に戻らない。
 * 実行中の工程（tool / step）が分かるときは工程名も出す。承認待ちは人の操作を待つため遅延の案内を出さない。
 */
function RunProgressIndicator({ run }: { run: RunState }) {
  if (!["queued", "running", "waiting_approval"].includes(run.status)) return null;
  const currentStep = [...run.steps].reverse().find((step) => step.status === "running");
  const stepName = currentStep ? (currentStep.tool_call?.name ?? currentStep.kind) : null;
  const label =
    run.status === "queued"
      ? t("run.progress.queued")
      : run.status === "waiting_approval"
        ? t("run.progress.waitingApproval")
        : stepName
          ? t("run.progress.runningStep", { step: stepName })
          : t("run.progress.running");
  return (
    <ProcessingIndicator
      active
      label={label}
      operationKey={run.id}
      startedAt={run.created_at}
      placement="job"
      showSlowMessage={run.status !== "waiting_approval"}
      className="rounded-md border border-border bg-surface-sunken px-3 py-2"
      testId="run-progress"
    />
  );
}

interface TimelineEventView {
  title: string;
  subtitle: string;
  icon: ReactNode;
  badgeLabel: string;
  badgeVariant: StatusVariant;
  details: Array<{ label: string; value: string }>;
  warnings: string[];
  payloadPreview?: Record<string, unknown>;
}

function RunTimeline({ events }: { events: RunEvent[] }) {
  if (!events.length) {
    return <EmptyState title={t("run.timeline.empty")} />;
  }
  return (
    <ol className="relative space-y-3" aria-label={t("run.timeline")}>
      {events.map((event) => (
        <RunTimelineItem key={event.id} event={event} />
      ))}
    </ol>
  );
}

function RunTimelineItem({ event }: { event: RunEvent }) {
  const view = timelineEventView(event);
  return (
    <li className="grid min-w-0 grid-cols-[2rem_minmax(0,1fr)] gap-3">
      <div className="flex justify-center">
        <div className="mt-1 flex size-8 items-center justify-center rounded-full border border-border bg-surface-sunken text-fg-muted">
          {view.icon}
        </div>
      </div>
      <div className="min-w-0 rounded-md border border-border p-3">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-fg [overflow-wrap:anywhere]">{view.title}</p>
            <p className="mt-1 break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">{view.subtitle}</p>
          </div>
          <StatusBadge variant={view.badgeVariant} label={view.badgeLabel} />
        </div>

        {/* key / value は <dl> で並べ、枠の中にさらに地の付いた箱を重ねない（入れ子の枠は 1 段まで。#818）。 */}
        <dl className="mt-3 grid min-w-0 gap-x-4 gap-y-2 text-xs sm:grid-cols-2">
          <TimelineFact label={t("run.timeline.eventType")} value={event.type} />
          <TimelineFact label={t("run.timeline.time")} value={formatDate(event.created_at)} />
          {view.details.map((detail) => (
            <TimelineFact key={`${detail.label}:${detail.value}`} label={detail.label} value={detail.value} />
          ))}
        </dl>

        {view.warnings.length ? (
          // 警告は手書きのチップにせず、アイコン付きの FormStatus で出す（色だけに頼らない）。
          <div className="mt-3 min-w-0 space-y-1 break-words [overflow-wrap:anywhere]">
            {view.warnings.map((warning) => (
              <FormStatus key={warning} tone="warning" message={warning} />
            ))}
          </div>
        ) : null}

        {view.payloadPreview ? <JsonPreview value={view.payloadPreview} /> : null}
      </div>
    </li>
  );
}

function TimelineFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="break-words font-medium text-fg [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

function timelineEventView(event: RunEvent): TimelineEventView {
  if (event.type.startsWith("runtime.")) {
    return {
      title: t("run.runtime"),
      subtitle: event.message,
      icon: <Server size={16} aria-hidden />,
      ...timelineBadge(event.type),
      details: compactTimelineDetails([
        [t("run.timeline.runtime"), payloadString(event.payload, "runtime_id")],
        [t("run.timeline.externalRun"), payloadString(event.payload, "external_run_id")],
      ]),
      warnings: [],
      payloadPreview: event.type === "runtime.failed" ? event.payload : undefined,
    };
  }
  if (event.type === "tool.guardrail_warning") {
    return {
      title: t("run.guardrailWarning"),
      subtitle: event.message,
      icon: <ShieldAlert size={16} aria-hidden />,
      ...timelineBadge(event.type),
      details: [],
      warnings: payloadStringArray(event.payload, "warnings"),
      payloadPreview: event.payload,
    };
  }
  if (event.type.startsWith("tool.")) {
    const toolName = payloadString(event.payload, "tool_name") ?? t("common.tool");
    return {
      title: toolName,
      subtitle: event.message,
      icon: <PlayCircle size={16} aria-hidden />,
      ...timelineBadge(event.type),
      details: compactTimelineDetails([
        [t("run.timeline.step"), payloadString(event.payload, "step_id")],
        [t("run.auditDuration"), payloadNumberText(event.payload, "duration_ms", "ms")],
      ]),
      warnings: payloadStringArray(event.payload, "guardrail_warnings"),
    };
  }
  if (event.type.startsWith("approval.")) {
    return {
      title: t("run.auditApproval"),
      subtitle: event.message,
      icon: <Check size={16} aria-hidden />,
      ...timelineBadge(event.type),
      details: compactTimelineDetails([
        [t("run.timeline.approval"), payloadString(event.payload, "approval_id")],
        [t("run.timeline.step"), payloadString(event.payload, "step_id")],
      ]),
      warnings: [],
    };
  }
  if (event.type === "artifact.created") {
    return {
      title: payloadString(event.payload, "name") ?? t("run.artifacts"),
      subtitle: event.message,
      icon: <FileText size={16} aria-hidden />,
      ...timelineBadge(event.type),
      details: compactTimelineDetails([[t("run.auditArtifacts"), payloadString(event.payload, "artifact_id")]]),
      warnings: [],
    };
  }
  return {
    title: event.message,
    subtitle: `${event.type} / ${formatDate(event.created_at)}`,
    icon: <GitBranch size={16} aria-hidden />,
    ...timelineBadge(event.type),
    details: [],
    warnings: [],
  };
}

/** タイムラインのバッジ（イベントの種類を日本語にする。lib/status-labels.ts）。 */
function timelineBadge(type: string): Pick<TimelineEventView, "badgeLabel" | "badgeVariant"> {
  const view = eventTypeView(type);
  return { badgeLabel: view.label, badgeVariant: view.variant };
}

function compactTimelineDetails(items: Array<[string, string | null]>): Array<{ label: string; value: string }> {
  return items.filter((item): item is [string, string] => Boolean(item[1])).map(([label, value]) => ({ label, value }));
}

function payloadString(payload: Record<string, unknown> | null, key: string): string | null {
  if (!payload) {
    return null;
  }
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function payloadStringArray(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function payloadNumberText(payload: Record<string, unknown>, key: string, suffix = ""): string | null {
  const value = payload[key];
  if (typeof value !== "number" || Number.isNaN(value)) {
    return null;
  }
  return suffix ? `${value}${suffix}` : String(value);
}

function RunStreamControls({
  mode,
  onModeChange,
  websocketState,
  sseState,
}: {
  mode: RunStreamMode;
  onModeChange: (mode: RunStreamMode) => void;
  websocketState: RunWebSocketState;
  sseState: RunEventSourceState;
}) {
  return (
    <Card className="min-w-0">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle>{t("run.stream")}</CardTitle>
          <CardDescription>{t("run.streamDescription")}</CardDescription>
        </div>
        <StatusBadge
          variant={mode === "websocket" ? websocketStatusVariant[websocketState.status] : "info"}
          label={mode === "websocket" ? websocketStatusLabel(websocketState.status) : t("run.stream.sse")}
          // WebSocket は接続状態（アイコンあり）、SSE は方式名（状態ではない）
          icon={mode === "websocket"}
        />
      </CardHeader>
      <CardContent className="space-y-4">
        {/* 購読の方式（モード）の切り替え。共有の ToggleChip を使う（buttons.md §6。手書きのセグメントは作らない）。 */}
        {/* 選んだ方式の説明は常設せず、チップの横の info アイコンから出す（#901）。 */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex flex-wrap gap-2" role="group" aria-label={t("run.streamMode")}>
            <ToggleChip selected={mode === "sse"} onClick={() => onModeChange("sse")}>
              {t("run.stream.sse")}
            </ToggleChip>
            <ToggleChip selected={mode === "websocket"} onClick={() => onModeChange("websocket")}>
              {t("run.stream.websocket")}
            </ToggleChip>
          </div>
          <InfoTip
            label={t("run.streamMode.infoLabel")}
            content={mode === "websocket" ? t("run.stream.websocketDescription") : t("run.stream.sseDescription")}
            contentTestId="run-stream-description"
            data-testid="run-stream-info"
          />
        </div>

        {mode === "websocket" && websocketState.stopReason ? (
          <Banner
            severity="warning"
            title={t("run.stream.stoppedTitle")}
            action={
              <Button variant="secondary" size="sm" icon={RefreshCw} onClick={websocketState.reconnect}>
                {t("run.stream.reconnect")}
              </Button>
            }
          >
            <span data-testid="run-stream-stopped">{websocketState.stopReason}</span>
          </Banner>
        ) : null}
        {mode === "sse" && sseState.status === "failed" ? (
          <Banner
            severity="warning"
            title={t("run.stream.stoppedTitle")}
            action={
              <Button variant="secondary" size="sm" icon={RefreshCw} onClick={sseState.reconnect}>
                {t("run.stream.reconnect")}
              </Button>
            }
          >
            <span data-testid="run-stream-stopped">{t("run.stream.sseFailed")}</span>
          </Banner>
        ) : null}

        {/* 通信の指標は業務の判断に使わないので「接続の詳細」に畳む（messaging.md §10.3）。 */}
        {mode === "websocket" ? (
          <Disclosure summary={t("run.stream.details")} variant="plain" size="sm" data-testid="run-stream-details">
            <dl className="grid min-w-0 gap-x-4 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-3">
              <StreamMetric label={t("run.stream.heartbeat")} value={websocketState.lastHeartbeat ?? "-"} />
              <StreamMetric label={t("run.stream.ack")} value={websocketState.lastAck ?? "-"} />
              <StreamMetric label={t("run.stream.error")} value={websocketState.lastError ?? "-"} />
              <StreamMetric label={t("run.stream.lastEvent")} value={websocketState.lastEventId ?? "-"} />
              <StreamMetric label={t("run.stream.reconnects")} value={String(websocketState.reconnectAttempts)} />
            </dl>
          </Disclosure>
        ) : null}
      </CardContent>
    </Card>
  );
}

function StreamMetric({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-medium text-fg">{label}</dt>
      <dd className="break-words text-fg-muted [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

function websocketStatusLabel(status: WebSocketStreamStatus): string {
  const labels: Record<WebSocketStreamStatus, string> = {
    idle: t("run.stream.wsIdle"),
    connecting: t("run.stream.wsConnecting"),
    open: t("run.stream.wsOpen"),
    reconnecting: t("run.stream.wsReconnecting"),
    closed: t("run.stream.wsClosed"),
    error: t("run.stream.wsError"),
    stopped: t("run.stream.wsStopped"),
  };
  return labels[status];
}

function AuditPanel({ runId }: { runId: string }) {
  const audit = useQuery({
    queryKey: ["runs", runId, "audit"],
    queryFn: () => agentApi.getRunAudit(runId),
  });

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.audit")}</CardTitle>
        <CardDescription>{t("run.auditDescription")}</CardDescription>
      </CardHeader>
      <CardContent>
        <QueryState
          query={audit}
          loadingLabel={t("loading.runAudit")}
          skeleton={<ListSkeleton rows={3} rowClassName="h-24" />}
        >
          {audit.data?.records.length ? (
            <div className="grid min-w-0 gap-3">
              {audit.data.records.map((record) => (
                <AuditRecordItem key={record.step_id} audit={audit.data} record={record} />
              ))}
            </div>
          ) : (
            <EmptyState title={t("run.noAudit")} />
          )}
        </QueryState>
      </CardContent>
    </Card>
  );
}

function AuditRecordItem({ audit, record }: { audit: RunAuditData; record: ToolAuditRecord }) {
  return (
    <div className="min-w-0 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-all text-sm font-medium text-fg">{record.tool_name}</p>
          <p className="mt-0.5 break-all text-xs text-fg-muted">{`${runStatusView(audit.status).label} / ${record.step_id}`}</p>
        </div>
        <StatusBadge {...stepStatusView(record.status)} />
      </div>

      <dl className="mt-3 grid gap-x-4 gap-y-2 text-xs sm:grid-cols-2 xl:grid-cols-4">
        <AuditFact
          label={t("run.auditPolicy")}
          value={record.policy_decision ? policyDecisionView(record.policy_decision).label : "-"}
        />
        <AuditFact
          label={t("common.permission")}
          value={record.permission_level ? permissionView(record.permission_level).label : "-"}
        />
        <AuditFact
          label={t("run.auditApproval")}
          value={record.approval_status ? approvalStatusView(record.approval_status).label : "-"}
        />
        <AuditFact
          label={t("run.auditDuration")}
          value={record.duration_ms === null || record.duration_ms === undefined ? "-" : `${record.duration_ms}ms`}
        />
      </dl>

      {record.trace_id ? (
        <p className="mt-3 break-all text-xs text-fg-muted">{`${t("run.auditTrace")}: ${record.trace_id}`}</p>
      ) : null}
      {record.artifact_ids.length ? (
        <p className="mt-3 break-all text-xs text-fg-muted">
          {`${t("run.auditArtifacts")}: ${record.artifact_ids.join(", ")}`}
        </p>
      ) : null}
      {record.guardrail_warnings.length ? (
        <Banner severity="warning" title={t("run.auditWarnings")}>
          <div className="space-y-1">
            {record.guardrail_warnings.map((warning) => (
              <p key={warning} className="break-words [overflow-wrap:anywhere]">
                {warning}
              </p>
            ))}
          </div>
        </Banner>
      ) : null}
      {record.error ? (
        // title は利用者向けの要約、エラーコードは「詳細」に畳む。失敗なので開いて出す（messaging.md §10.3。#725）。
        <Banner severity="danger" title={t("run.auditErrorTitle")}>
          <div className="min-w-0 space-y-2">
            <p className="break-words [overflow-wrap:anywhere]">{record.error}</p>
            {record.error_code ? (
              <Disclosure variant="plain" size="sm" summary={t("run.auditErrorDetails")} defaultOpen>
                <p className="break-all text-xs text-fg-muted">{`${t("audit.errorCode")}: ${record.error_code}`}</p>
              </Disclosure>
            ) : null}
          </div>
        </Banner>
      ) : null}
      <div className="mt-3">
        <JsonPanel title={t("run.auditMetadata")} value={record.audit_metadata} />
      </div>
    </div>
  );
}

function AuditFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="break-words font-medium text-fg [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

function ArtifactsPanel({
  run,
  showRawReview,
  onShowProcess,
}: {
  run: RunState;
  /** 支援タスクの状態・回答の検証の元の JSON を出すか（管理者だけ。#1286）。 */
  showRawReview: boolean;
  onShowProcess: () => void;
}) {
  // 最終回答を先に読む。ツールが返した中間成果物は、その後に元の順序で並べる。
  const artifacts = [...run.artifacts].sort((a, b) => Number(b.kind === "answer") - Number(a.kind === "answer"));
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.artifacts")}</CardTitle>
        <CardDescription>{t("run.artifactsDescription")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {run.artifacts.length ? (
          artifacts.map((artifact) => (
            <div key={artifact.id} className="min-w-0 rounded-md border border-border p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="break-all text-sm font-medium text-fg">{artifact.name}</p>
                  <p className="mt-0.5 text-xs text-fg-muted">{formatDate(artifact.created_at)}</p>
                </div>
                <StatusBadge {...artifactKindView(artifact.kind)} icon={false} />
              </div>
              {artifact.kind === "answer" && typeof (artifact.content.text ?? artifact.content.answer) === "string" ? (
                // 回答の Markdown の表は共通の結果の表で出す（チャットと同じ。#1158）。
                <AnswerBody
                  text={String(artifact.content.text ?? artifact.content.answer)}
                  renderText={(text) => (
                    <p className="whitespace-pre-wrap break-words text-sm leading-6 text-fg [overflow-wrap:anywhere]">
                      {text}
                    </p>
                  )}
                  testId={`run-answer-${artifact.id}`}
                />
              ) : artifact.kind === "rag_evidence" ? (
                <RagEvidenceArtifact artifact={artifact} runId={run.id} />
              ) : artifact.kind === SUPPORT_TASK_KIND || artifact.kind === ANSWER_VALIDATION_KIND ? (
                <ReviewArtifact artifact={artifact} showRaw={showRawReview} />
              ) : artifact.kind === "structured_table" ? (
                <StructuredArtifactSummary artifact={artifact} />
              ) : (
                <ToolOutput
                  output={artifact.content}
                  table={artifactTable(artifact)}
                  name={artifact.name}
                  testId={`run-artifact-table-${artifact.id}`}
                />
              )}
            </div>
          ))
        ) : (
          <EmptyState
            title={t("run.noArtifacts")}
            hint={t("run.noArtifactsHint")}
            action={
              <Button variant="secondary" icon={GitBranch} onClick={onShowProcess}>
                {t("run.tab.process")}
              </Button>
            }
          />
        )}
      </CardContent>
    </Card>
  );
}

/**
 * 支援タスクの状態・回答の検証（#1286）。業務の利用者にはチャットと同じ業務の言葉で出し、内部の語を含む
 * 元の JSON は管理者だけに畳んで出す（handoff §13）。
 */
function ReviewArtifact({ artifact, showRaw }: { artifact: Artifact; showRaw: boolean }) {
  const review = answerReview([artifact]);
  return (
    <div className="mt-3 min-w-0 space-y-2" data-testid={`run-review-${artifact.id}`}>
      {review?.limitReached ? <LimitNote testId={`run-review-limit-${artifact.id}`} /> : null}
      {review ? (
        <AnswerReviewSections review={review} />
      ) : (
        <p className="text-sm text-fg-muted">{t("run.reviewEmpty")}</p>
      )}
      {showRaw ? (
        <Disclosure variant="plain" size="sm" summary={t("run.rawJson")}>
          <JsonPreview value={artifact.content} />
        </Disclosure>
      ) : null}
    </div>
  );
}

function RagEvidenceArtifact({ artifact, runId }: { artifact: Artifact; runId: string }) {
  const answer = typeof artifact.content.answer === "string" ? artifact.content.answer : null;
  const evidence = ragEvidenceItems(artifact.content);
  const contexts = arrayOfRecords(artifact.content.contexts);

  return (
    <div className="mt-3 space-y-4">
      {answer ? (
        <section className="space-y-1">
          <h3 className="text-sm font-medium text-fg">{t("run.ragAnswer")}</h3>
          <p className="break-words text-sm leading-6 text-fg [overflow-wrap:anywhere]">{answer}</p>
        </section>
      ) : null}
      {evidence.length ? (
        <section className="space-y-2">
          <h3 className="flex items-center gap-2 text-sm font-medium text-fg">
            <FileText size={16} aria-hidden />
            {t("run.citations")}
          </h3>
          <div className="grid gap-2">
            {evidence.map((item) => (
              <EvidenceItem
                key={item.key}
                title={item.title}
                subtitle={
                  item.usedInAnswer
                    ? [t("run.evidenceUsed"), item.location].filter(Boolean).join(" · ")
                    : item.location
                }
                detail={item.text}
                action={
                  item.figure ? (
                    // 図の根拠は元の図を開いて確かめられる（#1311）。
                    <RagFigureButton
                      runId={runId}
                      figure={item.figure}
                      title={item.title}
                      location={item.location}
                      testId={`run-evidence-figure-${item.key}`}
                    />
                  ) : null
                }
              />
            ))}
          </div>
        </section>
      ) : null}
      {contexts.length ? (
        <section className="space-y-2">
          <h3 className="text-sm font-medium text-fg">{t("run.contexts")}</h3>
          <div className="grid gap-2">
            {contexts.slice(0, 6).map((context, index) => (
              <EvidenceItem
                key={String(context.id ?? index)}
                title={textValue(context.title) ?? textValue(context.source) ?? `context ${index + 1}`}
                subtitle={formatContextSubtitle(context)}
                detail={textValue(context.text) ?? textValue(context.snippet) ?? textValue(context.content)}
              />
            ))}
          </div>
        </section>
      ) : null}
      <JsonPreview value={artifact.content} />
    </div>
  );
}

function StructuredArtifactSummary({ artifact }: { artifact: Artifact }) {
  const table = artifactTable(artifact);
  const rowCount = numericValue(artifact.content.row_count);
  const truncated = typeof artifact.content.truncated === "boolean" ? artifact.content.truncated : null;
  const warnings = arrayOfText(artifact.content.warnings);
  const sql = structuredSql(artifact.content);

  return (
    <div className="mt-3 space-y-3">
      {table ? null : (
        // 表の形でない構造化データ（ジョブの実行中など）は、今までどおり件数の要約と JSON で出す。
        <div className="grid gap-2 text-sm sm:grid-cols-3">
          <MetricPill label={t("run.rowCount")} value={rowCount === null ? "-" : String(rowCount)} />
          <MetricPill
            label={t("run.truncated")}
            value={truncated === null ? "-" : truncated ? t("common.yes") : t("common.no")}
          />
          <MetricPill label={t("run.columns")} value={String(arrayOfRecords(artifact.content.columns).length)} />
        </div>
      )}
      {sql ? <JsonPanel title={t("run.sql")} value={sql} /> : null}
      {warnings.length ? (
        <Banner severity="warning">
          <div className="space-y-1">
            {warnings.map((warning) => (
              <p key={warning}>{warning}</p>
            ))}
          </div>
        </Banner>
      ) : null}
      <ToolOutput
        output={artifact.content}
        table={table}
        name={artifact.name}
        testId={`run-artifact-table-${artifact.id}`}
      />
    </div>
  );
}

/**
 * ツールの結果・成果物の内容。表の形なら共通の結果の表で出し、元の JSON は「元の JSON」に畳む。
 * 表でなければ今までどおり JSON で出す（#1158）。
 */
function ToolOutput({
  output,
  table,
  name,
  testId,
}: {
  output: unknown;
  table: ReturnType<typeof stepResultTable>;
  name: string;
  testId: string;
}) {
  if (!table) return <JsonPreview value={output} />;
  return (
    <div className="mt-3 min-w-0 space-y-2">
      <ResultTable data={table} name={name} testId={testId} />
      <Disclosure variant="plain" size="sm" summary={t("run.rawJson")}>
        <JsonPreview value={output} />
      </Disclosure>
    </div>
  );
}

function EvidenceItem({
  title,
  subtitle,
  detail,
  action,
}: {
  title: string;
  subtitle?: string | null;
  detail?: string | null;
  /** 根拠への操作（図を開くなど。#1311）。 */
  action?: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-md bg-surface-sunken p-3">
      <p className="break-words text-sm font-medium text-fg [overflow-wrap:anywhere]">{title}</p>
      {subtitle ? <p className="mt-1 break-all text-xs text-fg-muted">{subtitle}</p> : null}
      {detail ? (
        <p className="mt-2 line-clamp-4 break-words text-xs leading-5 text-fg [overflow-wrap:anywhere]">{detail}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

/**
 * 「構造化データ」のカード。表の形の成果物（NL2SQL のツールの結果など）が無い Run で、表の形のツールの結果の
 * 最後の 1 つを結果のタブに出す（成果物の表と同じものを二度出さない。#1158）。
 */
function StructuredResultCard({ run, table }: { run: RunState; table: ToolResultTable }) {
  const output = run.steps.find((step) => step.id === table.stepId)?.tool_result?.output ?? {};
  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle>{t("run.structuredResult")}</CardTitle>
        <CardDescription>{structuredSql(output) ?? t("run.sqlHidden")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ResultTable
          data={table.data}
          name={t("resultTable.toolName", { tool: table.toolName })}
          testId="run-structured-table"
        />
      </CardContent>
    </Card>
  );
}

function structuredFallback(run: RunState): ToolResultTable | null {
  if (run.artifacts.some((artifact) => artifactTable(artifact))) return null;
  return runToolResultTables(run).at(-1) ?? null;
}

/** 構造化データの SQL（NL2SQL の MCP の出力は `executable_sql` / `generated_sql`）。 */
function structuredSql(content: Record<string, unknown>): string | null {
  return textValue(content.sql) ?? textValue(content.executable_sql) ?? textValue(content.generated_sql);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function arrayOfRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

function arrayOfText(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function textValue(value: unknown): string | null {
  if (typeof value === "string" && value.trim()) {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

function numericValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatContextSubtitle(context: Record<string, unknown>): string | null {
  const parts = [
    textValue(context.source),
    numericValue(context.score) === null ? null : `${t("run.score")}: ${numericValue(context.score)}`,
  ].filter((part): part is string => Boolean(part));
  return parts.length ? parts.join(" / ") : null;
}
