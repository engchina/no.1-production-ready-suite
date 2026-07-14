import {
  CheckCircle2,
  ChevronDown,
  FileCode2,
  GitCompareArrows,
  Lightbulb,
  MessageSquareText,
  Network,
  Play,
  RefreshCw,
  Send,
  ShieldAlert,
} from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
  cn,
  type StatusVariant,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { IntentEditor } from "./IntentEditor";
import { OntologyWorkspace } from "./OntologyWorkspace";
import {
  currentIntentForSession,
  currentIntentVersionForSession,
  currentSqlArtifactForSession,
  currentValidationForSession,
  executionBindingForSession,
  hasGraphPatchVersionConflict,
  intentGraphToOntologyGraph,
  profileScopedOntologyGraph,
  querySessionState,
  sqlSemanticGraphToOntologyGraph,
  type GraphPatch,
  type IntentAmbiguity,
  type IntentConcept,
  type OntologyGraph,
  type OntologyImprovementProposalRequest,
  type OntologyJsonValue,
  type OntologyValidationFinding,
  type OntologyValidationReport,
  type QuerySession,
  type QuerySessionExecuteRequest,
  type QuerySessionGenerateSqlRequest,
  type QuerySessionSqlConfirmationRequest,
  type QuerySessionState,
  type SqlSemanticGraph,
  type SqlSemanticItem,
  type SqlSemanticJoin,
  type ValidationSeverity,
} from "./types";

type QueryOntologyView = "business" | "intent" | "sql" | "validation";
type FlowAction = "patch" | "intent" | "sql" | "execute" | "proposal";

export interface QueryVersionConflict {
  message?: string;
  baseVersion: number;
  currentVersion: number;
}

export interface QueryOntologyFlowLabels {
  workflowAria: string;
  sessionState: string;
  businessTab: string;
  intentTab: string;
  sqlTab: string;
  validationTab: string;
  originalQuestion: string;
  suggestedQuestion: string;
  entities: string;
  metrics: string;
  dimensions: string;
  filters: string;
  timeRange: string;
  granularity: string;
  sort: string;
  limit: string;
  candidatePath: string;
  unresolvedAmbiguity: string;
  noValue: string;
  intentGraphTitle: string;
  sqlGraphTitle: string;
  intentConfirmation: string;
  intentConfirmationHelp: string;
  confirmIntent: string;
  sqlMeaningTitle: string;
  sqlMeaningDescription: string;
  rawSql: string;
  tables: string;
  joins: string;
  groups: string;
  aggregates: string;
  validationTitle: string;
  validationDescription: string;
  coverage: (value: number) => string;
  applyPatch: string;
  patchTitle: string;
  patchDescription: string;
  conflictTitle: string;
  conflictMessage: (baseVersion: number, currentVersion: number) => string;
  reload: string;
  confirmSql: string;
  confirmAndExecute: string;
  execute: string;
  proposal: string;
  proposalHelp: string;
  actionFailed: string;
}

export const DEFAULT_QUERY_ONTOLOGY_FLOW_LABELS: QueryOntologyFlowLabels = {
  workflowAria: "NL2SQL Ontology 確認フロー",
  sessionState: "現在の状態",
  businessTab: "業務モデル",
  intentTab: "質問の解釈",
  sqlTab: "SQL の意味",
  validationTab: "差分・確認",
  originalQuestion: "元の質問",
  suggestedQuestion: "提案された質問",
  entities: "対象",
  metrics: "指標",
  dimensions: "切り口",
  filters: "絞り込み",
  timeRange: "期間",
  granularity: "粒度",
  sort: "並び順",
  limit: "上限",
  candidatePath: "関係経路",
  unresolvedAmbiguity: "質問の解釈に未解決の曖昧さがあります。候補を修正してから確認してください。",
  noValue: "指定なし",
  intentGraphTitle: "質問の解釈グラフ",
  sqlGraphTitle: "SQL セマンティックグラフ",
  intentConfirmation: "質問の理解を確認",
  intentConfirmationHelp: "対象・指標・条件・粒度が意図どおりかを確認すると、SQL を生成できます。",
  confirmIntent: "この解釈で SQL を生成",
  sqlMeaningTitle: "SQL の業務的な意味",
  sqlMeaningDescription: "表名や構文より先に、参照・関連・絞り込み・集計の意味を確認します。",
  rawSql: "SQL 詳細",
  tables: "参照対象",
  joins: "関連付け",
  groups: "集計粒度",
  aggregates: "集計方法",
  validationTitle: "三方差分と実行確認",
  validationDescription: "質問の解釈、SQL の意味、Profile Ontology View を照合した結果です。",
  coverage: (value) => `意図カバレッジ ${Math.round(value * 100)}%`,
  applyPatch: "変更を適用",
  patchTitle: "Ontology patch のプレビュー",
  patchDescription: "適用すると現在のクエリセッションに新しい intent version を作成します。正式 Ontology は変更しません。",
  conflictTitle: "別の変更が先に保存されました",
  conflictMessage: (baseVersion, currentVersion) =>
    `編集元は version ${baseVersion} ですが、現在は version ${currentVersion} です。最新状態を読み直してください。`,
  reload: "最新状態を読み込む",
  confirmSql: "SQL の意味を確認",
  confirmAndExecute: "SQL の意味を確認して実行",
  execute: "確認済み SQL を実行",
  proposal: "改善提案として送信",
  proposalHelp: "セッション内の変更を、正式 Ontology の改善候補として保存します。公開は行いません。",
  actionFailed: "操作を完了できませんでした。内容を確認して再試行してください。",
};

export interface QueryOntologyFlowProps {
  session: QuerySession;
  pendingPatch?: GraphPatch | null;
  versionConflict?: QueryVersionConflict | null;
  onApplyIntentPatch?: (patch: GraphPatch) => void | Promise<void>;
  onConfirmIntent?: (request: QuerySessionGenerateSqlRequest) => void | Promise<void>;
  onConfirmSql?: (request: QuerySessionSqlConfirmationRequest) => void | Promise<void>;
  onExecute?: (request: QuerySessionExecuteRequest) => void | Promise<void>;
  onCreateProposal?: (request: OntologyImprovementProposalRequest) => void | Promise<void>;
  onReload?: () => void | Promise<void>;
  labels?: Partial<QueryOntologyFlowLabels>;
  className?: string;
}

const EMPTY_GRAPH: OntologyGraph = { nodes: [], edges: [] };

const STATUS_LABELS: Record<QuerySessionState, string> = {
  interpreting: "質問を解釈中",
  awaiting_intent_confirmation: "質問の確認待ち",
  generating_sql: "SQL を生成中",
  awaiting_sql_confirmation: "SQL の確認待ち",
  executing: "実行中",
  done: "完了",
  error: "エラー",
};

function labelsWithDefaults(labels?: Partial<QueryOntologyFlowLabels>): QueryOntologyFlowLabels {
  return { ...DEFAULT_QUERY_ONTOLOGY_FLOW_LABELS, ...labels };
}

function statusVariant(state: QuerySessionState): StatusVariant {
  if (state === "done") return "success";
  if (state === "error") return "danger";
  if (state === "awaiting_intent_confirmation" || state === "awaiting_sql_confirmation") return "warning";
  return "info";
}

function validationReportStatus(report: OntologyValidationReport | null): "passed" | "warning" | "blocked" {
  if (!report) return "blocked";
  if (report.status) return report.status;
  if ((report.blocker_count ?? 0) > 0 || report.is_valid === false) return "blocked";
  if ((report.warning_count ?? 0) > 0) return "warning";
  return "passed";
}

function validationStatusVariant(report: OntologyValidationReport | null): StatusVariant {
  const status = validationReportStatus(report);
  if (status === "passed") return "success";
  if (status === "warning") return "warning";
  return "danger";
}

function validationStatusLabel(report: OntologyValidationReport | null): string {
  const status = validationReportStatus(report);
  if (status === "passed") return "通過";
  if (status === "warning") return "警告あり";
  return "ブロック";
}

function conceptName(value: IntentConcept | string): string {
  if (typeof value === "string") return value;
  return value.name_ja || value.name || value.ontology_node_id || value.node_id || "名称未設定";
}

function jsonValueLabel(value: OntologyJsonValue): string {
  if (typeof value === "string") return value;
  if (value === null) return "null";
  return JSON.stringify(value);
}

function sqlExpression(value: SqlSemanticItem | SqlSemanticJoin | string): string {
  if (typeof value === "string") return value;
  const joinExpression =
    "condition_sql" in value ? value.condition_sql || value.condition : undefined;
  return (
    value.expression ||
    value.expression_sql ||
    joinExpression ||
    value.qualified_name ||
    value.output_name ||
    value.name ||
    "SQL 要素"
  );
}

function unresolvedAmbiguities(ambiguities: IntentAmbiguity[] | undefined): IntentAmbiguity[] {
  return (ambiguities ?? []).filter((ambiguity) => ambiguity.resolved !== true && ambiguity.blocking !== false);
}

function findingMessage(finding: OntologyValidationFinding): string {
  return finding.message_ja || finding.message || finding.code;
}

function findingVariant(severity: ValidationSeverity): StatusVariant {
  if (severity === "pass" || severity === "passed") return "success";
  if (severity === "warning") return "warning";
  return "danger";
}

function findingLabel(severity: ValidationSeverity): string {
  if (severity === "pass" || severity === "passed") return "通過";
  if (severity === "warning") return "警告";
  return "ブロック";
}

function ViewTab({
  id,
  idPrefix,
  active,
  label,
  icon,
  onSelect,
}: {
  id: QueryOntologyView;
  idPrefix: string;
  active: boolean;
  label: string;
  icon: ReactNode;
  onSelect: (id: QueryOntologyView) => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-controls={`${idPrefix}-panel-${id}`}
      id={`${idPrefix}-tab-${id}`}
      onClick={() => onSelect(id)}
      className={cn(
        "inline-flex min-h-11 flex-1 cursor-pointer items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium outline-none transition-colors motion-reduce:transition-none sm:flex-none",
        active ? "bg-slate-900 text-white" : "text-foreground hover:bg-muted/30",
        "focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-2"
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function FieldSummary({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <dt className="text-xs font-semibold text-muted">{label}</dt>
      <dd className="mt-2 min-w-0 break-words text-sm leading-6 text-foreground">{children}</dd>
    </div>
  );
}

function ChipList({ values, empty }: { values: string[]; empty: string }) {
  if (values.length === 0) return <span className="text-muted">{empty}</span>;
  return (
    <span className="flex flex-wrap gap-2">
      {values.map((value, index) => (
        <StatusBadge key={`${value}:${index}`} variant="neutral" label={value} />
      ))}
    </span>
  );
}

function IntentSummary({
  session,
  labels,
}: {
  session: QuerySession;
  labels: QueryOntologyFlowLabels;
}) {
  const intent = currentIntentForSession(session);
  if (!intent) return <Banner severity="info">質問の解釈を作成中です。</Banner>;
  const filterLabels = intent.filters.map((filter) => {
    const field = filter.label_ja || filter.field || filter.property_node_id || "条件";
    return `${field} ${filter.operator} ${jsonValueLabel(filter.value)}`;
  });
  const sortLabels = (intent.sorts ?? intent.sort ?? []).map(
    (sort) => `${sort.target_id || sort.field || "対象"} ${sort.direction.toUpperCase()}`
  );
  const path = (intent.candidate_paths ?? []).find((item) => item.id === intent.selected_path_id)
    ?? intent.candidate_paths?.[0];
  const timeRange = intent.time_range
    ? [
        intent.time_range.start,
        intent.time_range.end,
        intent.time_range.relative_expression || intent.time_range.relative,
      ]
        .filter((value): value is string => Boolean(value))
        .join(" ～ ")
    : "";
  return (
    <dl className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <FieldSummary label={labels.entities}>
        <ChipList values={intent.entities.map(conceptName)} empty={labels.noValue} />
      </FieldSummary>
      <FieldSummary label={labels.metrics}>
        <ChipList values={intent.metrics.map(conceptName)} empty={labels.noValue} />
      </FieldSummary>
      <FieldSummary label={labels.dimensions}>
        <ChipList values={intent.dimensions.map(conceptName)} empty={labels.noValue} />
      </FieldSummary>
      <FieldSummary label={labels.filters}>
        <ChipList values={filterLabels} empty={labels.noValue} />
      </FieldSummary>
      <FieldSummary label={labels.timeRange}>{timeRange || labels.noValue}</FieldSummary>
      <FieldSummary label={labels.granularity}>
        {intent.granularity || intent.grain || intent.time_range?.granularity || labels.noValue}
      </FieldSummary>
      <FieldSummary label={labels.sort}>
        <ChipList values={sortLabels} empty={labels.noValue} />
      </FieldSummary>
      <FieldSummary label={labels.limit}>
        {intent.limit == null ? labels.noValue : `${intent.limit.toLocaleString("ja-JP")} 件`}
      </FieldSummary>
      <FieldSummary label={labels.candidatePath}>
        {path?.name_ja || path?.label || (path?.node_ids.length ? path.node_ids.join(" → ") : labels.noValue)}
      </FieldSummary>
    </dl>
  );
}

function IntentPatchPreview({
  patch,
  currentVersion,
  conflict,
  busy,
  labels,
  onApply,
  onReload,
}: {
  patch: GraphPatch;
  currentVersion: number;
  conflict?: QueryVersionConflict | null;
  busy: boolean;
  labels: QueryOntologyFlowLabels;
  onApply?: (patch: GraphPatch) => void | Promise<void>;
  onReload?: () => void | Promise<void>;
}) {
  const localConflict = hasGraphPatchVersionConflict(patch.base_version, currentVersion);
  const activeConflict = conflict ?? (localConflict
    ? { baseVersion: patch.base_version, currentVersion }
    : null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{labels.patchTitle}</CardTitle>
        <CardDescription className="leading-6">{labels.patchDescription}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {patch.suggested_question ? (
          <div className="rounded-lg border border-primary/30 bg-primary/10 p-3">
            <p className="text-xs font-semibold text-primary">{labels.suggestedQuestion}</p>
            <p className="mt-1 text-sm leading-6 text-foreground">{patch.suggested_question}</p>
          </div>
        ) : null}
        <ol className="grid gap-2">
          {patch.operations.map((operation, index) => (
            <li key={`${operation.op}:${operation.path}:${index}`} className="rounded-md border border-border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge variant={operation.op === "remove" ? "warning" : "info"} label={operation.op.toUpperCase()} />
                <code className="break-all text-xs text-foreground">{operation.path}</code>
              </div>
              {operation.label || operation.reason_ja ? (
                <p className="mt-2 leading-6 text-foreground">{operation.label || operation.reason_ja}</p>
              ) : null}
              {operation.value !== undefined ? (
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-md bg-background p-2 text-sm leading-6 text-foreground">
                  {JSON.stringify(operation.value, null, 2)}
                </pre>
              ) : null}
            </li>
          ))}
        </ol>
        {activeConflict ? (
          <Banner
            severity="danger"
            title={labels.conflictTitle}
            action={
              onReload ? (
                <Button type="button" variant="secondary" size="sm" className="min-h-11" onClick={() => void onReload()}>
                  <RefreshCw size={15} aria-hidden="true" />
                  {labels.reload}
                </Button>
              ) : undefined
            }
          >
            {activeConflict.message || labels.conflictMessage(activeConflict.baseVersion, activeConflict.currentVersion)}
          </Banner>
        ) : null}
        <div className="flex justify-end">
          <Button
            type="button"
            size="md"
            className="min-h-11"
            loading={busy}
            disabled={!onApply || Boolean(activeConflict) || patch.operations.length === 0}
            onClick={() => void onApply?.(patch)}
          >
            {labels.applyPatch}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function SqlMeaningSummary({
  graph,
  labels,
}: {
  graph: SqlSemanticGraph;
  labels: QueryOntologyFlowLabels;
}) {
  const groups = graph.groups ?? graph.group_by ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{labels.sqlMeaningTitle}</CardTitle>
        <CardDescription className="leading-6">{labels.sqlMeaningDescription}</CardDescription>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3 sm:grid-cols-2">
          <FieldSummary label={labels.tables}>
            <ChipList values={graph.tables.map(sqlExpression)} empty={labels.noValue} />
          </FieldSummary>
          <FieldSummary label={labels.joins}>
            <ChipList values={graph.joins.map(sqlExpression)} empty={labels.noValue} />
          </FieldSummary>
          <FieldSummary label={labels.filters}>
            <ChipList values={graph.filters.map(sqlExpression)} empty={labels.noValue} />
          </FieldSummary>
          <FieldSummary label={labels.aggregates}>
            <ChipList values={graph.aggregates.map(sqlExpression)} empty={labels.noValue} />
          </FieldSummary>
          <FieldSummary label={labels.groups}>
            <ChipList values={groups.map(sqlExpression)} empty={labels.noValue} />
          </FieldSummary>
          <FieldSummary label={labels.limit}>
            {graph.limit == null ? labels.noValue : `${graph.limit.toLocaleString("ja-JP")} 件`}
          </FieldSummary>
        </dl>
      </CardContent>
    </Card>
  );
}

function RawSqlDetails({ sql, label }: { sql: string; label: string }) {
  return (
    <details className="group rounded-lg border border-border bg-card">
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 rounded-lg px-4 py-3 text-sm font-semibold text-foreground outline-none hover:bg-background focus-visible:ring-2 focus-visible:ring-ring/40">
        {label}
        <ChevronDown size={17} className="transition-transform group-open:rotate-180 motion-reduce:transition-none" aria-hidden="true" />
      </summary>
      <div className="border-t border-border p-3">
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-code p-4 text-sm leading-6 text-code-fg">
          <code>{sql}</code>
        </pre>
      </div>
    </details>
  );
}

function ValidationFindings({ findings }: { findings: OntologyValidationFinding[] }) {
  if (findings.length === 0) {
    return <Banner severity="success">差分は検出されませんでした。</Banner>;
  }
  return (
    <ol className="grid gap-3">
      {findings.map((finding, index) => (
        <li key={finding.id || `${finding.code}:${index}`} className="rounded-lg border border-border bg-card p-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge variant={findingVariant(finding.severity)} label={findingLabel(finding.severity)} />
            <code className="text-xs text-muted">{finding.code}</code>
          </div>
          <p className="mt-2 text-sm leading-6 text-foreground">{findingMessage(finding)}</p>
          {finding.suggested_action_ja || finding.remediation ? (
            <p className="mt-2 text-sm leading-6 text-muted">
              {finding.suggested_action_ja || finding.remediation}
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

export function QueryOntologyFlow({
  session,
  pendingPatch,
  versionConflict,
  onApplyIntentPatch,
  onConfirmIntent,
  onConfirmSql,
  onExecute,
  onCreateProposal,
  onReload,
  labels: labelOverrides,
  className,
}: QueryOntologyFlowProps) {
  const labels = labelsWithDefaults(labelOverrides);
  const tabIdPrefix = useId().replace(/:/g, "-");
  const state = querySessionState(session);
  const defaultView: QueryOntologyView =
    state === "awaiting_intent_confirmation" || state === "interpreting"
      ? "intent"
      : state === "generating_sql" || state === "awaiting_sql_confirmation"
        ? "sql"
        : "validation";
  const [activeView, setActiveView] = useState<QueryOntologyView>(defaultView);
  const [busyAction, setBusyAction] = useState<FlowAction | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const intent = currentIntentForSession(session);
  const artifact = currentSqlArtifactForSession(session);
  const sqlGraph = artifact?.semantic_graph ?? session.sql_semantic_graph ?? null;
  const validation = currentValidationForSession(session);
  const binding = executionBindingForSession(session);
  const businessGraph = profileScopedOntologyGraph(
    session.profile_ontology_view,
    session.ontology_graph ?? EMPTY_GRAPH
  );
  const intentGraph = intent ? intentGraphToOntologyGraph(intent) : EMPTY_GRAPH;
  const sqlOntologyGraph = sqlGraph ? sqlSemanticGraphToOntologyGraph(sqlGraph) : EMPTY_GRAPH;
  const ambiguities = unresolvedAmbiguities(intent?.ambiguities);
  const blockers = validation?.findings.filter((finding) => finding.severity === "blocker") ?? [];
  const sql = artifact?.sql || artifact?.generated_sql || artifact?.raw_sql || sqlGraph?.raw_sql || "";
  const currentIntentVersion = currentIntentVersionForSession(session);
  const patchHasConflict = pendingPatch
    ? hasGraphPatchVersionConflict(pendingPatch.base_version, currentIntentVersion)
    : false;

  const runAction = async (action: FlowAction, callback: () => void | Promise<void>) => {
    setBusyAction(action);
    setActionError(null);
    try {
      await callback();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : labels.actionFailed);
    } finally {
      setBusyAction(null);
    }
  };

  const confirmIntent = () => {
    if (!onConfirmIntent || !intent) return;
    const request: QuerySessionGenerateSqlRequest = {
      base_version: currentIntentVersion,
      intent_version: currentIntentVersion,
      ontology_revision_id: session.ontology_revision_id,
      confirm_intent: true,
    };
    void runAction("intent", () => onConfirmIntent(request));
  };

  const confirmSql = () => {
    if (!onConfirmSql || !binding) return;
    void runAction("sql", () => onConfirmSql({ ...binding, confirm_sql: true }));
  };

  const execute = () => {
    if (!onExecute || !binding) return;
    void runAction("execute", () => onExecute({ ...binding, confirm_sql: true }));
  };

  const createProposal = () => {
    if (!onCreateProposal) return;
    const request: OntologyImprovementProposalRequest = {
      base_revision_id: session.ontology_revision_id,
      intent_version: currentIntentVersion,
      patch: pendingPatch ?? undefined,
      summary: pendingPatch?.summary_ja || pendingPatch?.reason,
    };
    void runAction("proposal", () => onCreateProposal(request));
  };

  const tabs = useMemo(
    () => [
      { id: "business" as const, label: labels.businessTab, icon: <Network size={16} aria-hidden="true" /> },
      { id: "intent" as const, label: labels.intentTab, icon: <MessageSquareText size={16} aria-hidden="true" /> },
      { id: "sql" as const, label: labels.sqlTab, icon: <FileCode2 size={16} aria-hidden="true" /> },
      { id: "validation" as const, label: labels.validationTab, icon: <GitCompareArrows size={16} aria-hidden="true" /> },
    ],
    [labels.businessTab, labels.intentTab, labels.sqlTab, labels.validationTab]
  );

  return (
    <section className={cn("space-y-4", className)} aria-label={labels.workflowAria}>
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-muted">{labels.sessionState}</span>
          <StatusBadge variant={statusVariant(state)} label={STATUS_LABELS[state]} />
          <span className="font-mono text-xs text-muted">intent v{currentIntentVersion}</span>
        </div>
        <span className="max-w-full break-all font-mono text-xs text-muted">{session.id}</span>
      </div>

      {session.error_message_ja || session.error_message ? (
        <Banner severity="danger" title="クエリセッションでエラーが発生しました">
          {session.error_message_ja || session.error_message}
        </Banner>
      ) : null}
      {actionError ? <Banner severity="danger">{actionError}</Banner> : null}

      <div
        role="tablist"
        aria-label={labels.workflowAria}
        className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-card p-2 sm:flex sm:flex-wrap"
      >
        {tabs.map((tab) => (
          <ViewTab
            key={tab.id}
            id={tab.id}
            idPrefix={tabIdPrefix}
            active={activeView === tab.id}
            label={tab.label}
            icon={tab.icon}
            onSelect={setActiveView}
          />
        ))}
      </div>

      <div
        id={`${tabIdPrefix}-panel-${activeView}`}
        role="tabpanel"
        aria-labelledby={`${tabIdPrefix}-tab-${activeView}`}
        tabIndex={0}
        className="space-y-4 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        {activeView === "business" ? (
          <OntologyWorkspace
            graph={businessGraph}
            title={labels.businessTab}
            description="この Profile で参照できる業務概念、物理オブジェクト、承認済み関係です。"
          />
        ) : null}

        {activeView === "intent" ? (
          <>
            <Card>
              <CardHeader>
                <CardTitle>{labels.intentTab}</CardTitle>
                <CardDescription className="leading-6">
                  自然言語の質問を、対象・指標・条件・時間・粒度として分解した結果です。
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 lg:grid-cols-2">
                  <div className="rounded-lg border border-border bg-card p-3">
                    <p className="text-xs font-semibold text-muted">{labels.originalQuestion}</p>
                    <p className="mt-2 text-sm leading-6 text-foreground">
                      {session.original_question || intent?.question_original || session.question || labels.noValue}
                    </p>
                  </div>
                  {session.suggested_question || intent?.question_effective || intent?.rewritten_question ? (
                    <div className="rounded-lg border border-primary/30 bg-primary/10 p-3">
                      <p className="text-xs font-semibold text-primary">{labels.suggestedQuestion}</p>
                      <p className="mt-2 text-sm leading-6 text-foreground">
                        {session.suggested_question || intent?.question_effective || intent?.rewritten_question}
                      </p>
                    </div>
                  ) : null}
                </div>
                <IntentSummary session={session} labels={labels} />
                {ambiguities.length > 0 ? (
                  <Banner severity="danger" title={labels.unresolvedAmbiguity}>
                    <ul className="mt-1 list-disc space-y-1 pl-5">
                      {ambiguities.map((ambiguity, index) => (
                        <li key={ambiguity.id || index}>{ambiguity.message_ja || ambiguity.message || ambiguity.code}</li>
                      ))}
                    </ul>
                  </Banner>
                ) : null}
              </CardContent>
            </Card>
            {intent ? (
              <IntentEditor
                intent={intent}
                businessGraph={businessGraph}
                busy={busyAction === "patch"}
                disabled={
                  state !== "awaiting_intent_confirmation"
                  || Boolean(versionConflict)
                  || !onApplyIntentPatch
                }
                onApply={
                  onApplyIntentPatch
                    ? (patch) => runAction("patch", () => onApplyIntentPatch(patch))
                    : undefined
                }
              />
            ) : null}
            <OntologyWorkspace
              graph={intentGraph}
              title={labels.intentGraphTitle}
              description="質問の各要素と、確認が必要な曖昧さを同じ構造で表示します。"
            />
            {pendingPatch ? (
              <IntentPatchPreview
                patch={pendingPatch}
                currentVersion={currentIntentVersion}
                conflict={versionConflict}
                busy={busyAction === "patch"}
                labels={labels}
                onApply={
                  onApplyIntentPatch
                    ? (patch) => runAction("patch", () => onApplyIntentPatch(patch))
                    : undefined
                }
                onReload={onReload}
              />
            ) : null}
            <Card>
              <CardHeader>
                <CardTitle>{labels.intentConfirmation}</CardTitle>
                <CardDescription className="leading-6">{labels.intentConfirmationHelp}</CardDescription>
              </CardHeader>
              <CardContent className="flex justify-end">
                <Button
                  type="button"
                  size="md"
                  className="min-h-11"
                  loading={busyAction === "intent"}
                  disabled={
                    !onConfirmIntent
                    || !intent
                    || ambiguities.length > 0
                    || patchHasConflict
                    || Boolean(versionConflict)
                    || state !== "awaiting_intent_confirmation"
                  }
                  onClick={confirmIntent}
                >
                  <CheckCircle2 size={16} aria-hidden="true" />
                  {labels.confirmIntent}
                </Button>
              </CardContent>
            </Card>
          </>
        ) : null}

        {activeView === "sql" ? (
          sqlGraph ? (
            <>
              <SqlMeaningSummary graph={sqlGraph} labels={labels} />
              <OntologyWorkspace
                graph={sqlOntologyGraph}
                title={labels.sqlGraphTitle}
                description="CTE、参照、Join、条件、集計、並び順、件数制限を AST から決定論的に表示します。"
              />
              {sql ? <RawSqlDetails sql={sql} label={labels.rawSql} /> : null}
              {session.performance_check ? (
                <Card>
                  <CardHeader>
                    <CardTitle>{t("nl2sql.session.performance")}</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {session.performance_check.available ? (
                      <dl className="grid gap-3 text-sm sm:grid-cols-3">
                        <FieldSummary label={t("nl2sql.session.performanceCost")}>
                          {session.performance_check.total_cost?.toLocaleString("ja-JP") ?? labels.noValue}
                        </FieldSummary>
                        <FieldSummary label={t("nl2sql.session.performanceRows")}>
                          {session.performance_check.estimated_cardinality?.toLocaleString("ja-JP") ?? labels.noValue}
                        </FieldSummary>
                        <FieldSummary label={t("nl2sql.session.fullScans")}>
                          {(session.performance_check.full_table_scans ?? []).join(", ") || labels.noValue}
                        </FieldSummary>
                      </dl>
                    ) : (
                      <Banner severity="info">
                        {session.performance_check.warning || t("nl2sql.session.performanceUnavailable")}
                      </Banner>
                    )}
                  </CardContent>
                </Card>
              ) : null}
            </>
          ) : (
            <Banner severity="info">SQL の生成後に、業務的な意味と AST グラフを表示します。</Banner>
          )
        ) : null}

        {activeView === "validation" ? (
          <>
            <Card>
              <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
                <div>
                  <CardTitle>{labels.validationTitle}</CardTitle>
                  <CardDescription className="mt-1 leading-6">{labels.validationDescription}</CardDescription>
                </div>
                <div className="flex flex-wrap gap-2">
                  <StatusBadge variant={validationStatusVariant(validation)} label={validationStatusLabel(validation)} />
                  {validation ? (
                    <StatusBadge variant="info" label={labels.coverage(validation.intent_coverage)} />
                  ) : null}
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                {validation?.business_summary ? (
                  <Banner severity={validationReportStatus(validation) === "blocked" ? "danger" : "info"}>
                    {validation.business_summary}
                  </Banner>
                ) : null}
                {validation ? (
                  <ValidationFindings findings={validation.findings} />
                ) : (
                  <Banner severity="info">SQL の生成後に三方差分を表示します。</Banner>
                )}
                {blockers.length > 0 ? (
                  <Banner severity="danger" title="実行をブロックしています">
                    未解決の blocker を修正して SQL を再生成してください。
                  </Banner>
                ) : null}
                <div className="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
                  {onConfirmSql && !session.sql_confirmation ? (
                    <Button
                      type="button"
                      variant="secondary"
                      size="md"
                      className="min-h-11"
                      loading={busyAction === "sql"}
                      disabled={!binding || blockers.length > 0 || state !== "awaiting_sql_confirmation"}
                      onClick={confirmSql}
                    >
                      <ShieldAlert size={16} aria-hidden="true" />
                      {labels.confirmSql}
                    </Button>
                  ) : null}
                  {onExecute ? (
                    <Button
                      type="button"
                      size="md"
                      className="min-h-11"
                      loading={busyAction === "execute"}
                      disabled={
                        !binding
                        || blockers.length > 0
                        || (onConfirmSql
                          ? !session.sql_confirmation
                          : state !== "awaiting_sql_confirmation")
                      }
                      onClick={execute}
                    >
                      <Play size={16} aria-hidden="true" />
                      {onConfirmSql ? labels.execute : labels.confirmAndExecute}
                    </Button>
                  ) : null}
                </div>
              </CardContent>
            </Card>

            {onCreateProposal ? (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Lightbulb size={17} className="text-warning" aria-hidden="true" />
                    {labels.proposal}
                  </CardTitle>
                  <CardDescription className="leading-6">{labels.proposalHelp}</CardDescription>
                </CardHeader>
                <CardContent className="flex justify-end">
                  <Button
                    type="button"
                    variant="secondary"
                    size="md"
                    className="min-h-11"
                    loading={busyAction === "proposal"}
                    disabled={!pendingPatch && currentIntentVersion <= 1}
                    onClick={createProposal}
                  >
                    <Send size={16} aria-hidden="true" />
                    {labels.proposal}
                  </Button>
                </CardContent>
              </Card>
            ) : null}
          </>
        ) : null}
      </div>
    </section>
  );
}
