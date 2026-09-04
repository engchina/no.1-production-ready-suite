import { lazy, Suspense, useMemo, useState, type SyntheticEvent } from "react";
import { Copy, FileText, ListOrdered, Network, Play } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DisclosureChevron } from "@/components/ui/disclosure-chevron";
import { Banner, toast } from "@engchina/production-ready-ui";

import { ContentActionBar } from "@/components/ContentActionBar";
import { LogicalStepsList } from "./LogicalStepsList";
import { StatusBadge } from "@/components/ui/status-badge";
import { copyTextToClipboard } from "@/lib/clipboard";
import { t } from "@/lib/i18n";
import { toastError } from "@/lib/toast";
import { engineLabel } from "../labels";
import { QUESTION_FILTER_LABELS, QUESTION_SLOT_LABELS } from "../questionTemplates";
import {
  groundSqlSemanticGraphOnOntologyGraph,
  isSqlSemanticGraph,
  type SqlOntologyGroundingResult,
} from "../ontology/sqlGrounding";
import type { OntologyGraph, SqlSemanticGraph } from "../ontology/types";
import type {
  GeneratedSqlPanelData,
  Nl2SqlInterpretationArtifact,
  Nl2SqlShowPromptArtifact,
} from "../types";

const LazyOntologyGraphCanvas = lazy(() => import("../ontology/OntologyGraphCanvas"));

const FILTER_LABELS = QUESTION_FILTER_LABELS;
const QUESTION_SLOT_PATTERN = /^\s*([^：:\n]{1,80})\s*[：:]\s*(.*)$/u;

function normalizeQuestionSlotLabel(value: string) {
  return value
    .normalize("NFKC")
    .trim()
    .toLowerCase()
    .replace(/[\s　（）()・/／]/gu, "");
}

const QUESTION_SLOT_LABEL_KEYS = new Map(
  QUESTION_SLOT_LABELS.map((label) => [normalizeQuestionSlotLabel(label), label])
);

function parseQuestionTemplate(question: string) {
  const slots: Record<string, string> = {};
  let currentLabel = "";
  let hasTemplate = false;
  for (const rawLine of question.split(/\r?\n/u)) {
    const match = QUESTION_SLOT_PATTERN.exec(rawLine);
    if (match) {
      const label = QUESTION_SLOT_LABEL_KEYS.get(normalizeQuestionSlotLabel(match[1]));
      if (label) {
        hasTemplate = true;
        currentLabel = label;
        slots[label] = slots[label] ? `${slots[label]}\n${match[2]}` : match[2];
        continue;
      }
    }
    if (currentLabel) {
      slots[currentLabel] = slots[currentLabel] ? `${slots[currentLabel]}\n${rawLine}` : rawLine;
    }
  }
  return {
    hasTemplate,
    slots: Object.fromEntries(Object.entries(slots).map(([label, value]) => [label, value.trim()])),
  };
}

function questionSlotValue(slots: Record<string, string>, labels: string[]) {
  const labelKeys = new Set(labels.map(normalizeQuestionSlotLabel));
  for (const [label, value] of Object.entries(slots)) {
    if (labelKeys.has(normalizeQuestionSlotLabel(label))) return value;
  }
  return undefined;
}

function GroundingStatusBadge({ result }: { result: SqlOntologyGroundingResult }) {
  if (result.status === "matched") {
    return <StatusBadge variant="success" label={t("nl2sql.interpretation.graphStatus.matched")} />;
  }
  if (result.status === "partial") {
    return <StatusBadge variant="warning" label={t("nl2sql.interpretation.graphStatus.partial")} />;
  }
  if (result.status === "unmatched") {
    return <StatusBadge variant="warning" label={t("nl2sql.interpretation.graphStatus.unmatched")} />;
  }
  return <StatusBadge variant="neutral" label={t("nl2sql.interpretation.graphStatus.unavailable")} />;
}

function GroundingMatchRows({
  title,
  values,
}: {
  title: string;
  values: Array<{ sql: string; ontologyLabels: string[] }>;
}) {
  return (
    <div className="grid gap-1">
      <p className="text-xs font-semibold text-muted">{title}</p>
      {values.length === 0 ? (
        <p className="text-xs leading-5 text-muted">-</p>
      ) : (
        <ul className="grid gap-1 text-xs leading-5">
          {values.slice(0, 5).map((value) => (
            <li key={`${title}:${value.sql}`} className="min-w-0 [overflow-wrap:anywhere]">
              <span className="font-mono text-foreground">{value.sql}</span>
              <span className="text-muted"> → {value.ontologyLabels.join("、") || "-"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UnmatchedList({ values }: { values: string[] }) {
  if (values.length === 0) return null;
  return (
    <ul className="mt-1 grid gap-1 text-xs leading-5 text-warning">
      {values.slice(0, 6).map((value) => (
        <li key={value} className="min-w-0 font-mono [overflow-wrap:anywhere]">
          {value}
        </li>
      ))}
    </ul>
  );
}

function SqlOntologyGroundingPanel({
  sqlGraph,
  profileId,
  ontologyGraph,
}: {
  sqlGraph: SqlSemanticGraph | null;
  profileId: string;
  ontologyGraph?: OntologyGraph | null;
}) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const grounding = useMemo(
    () => groundSqlSemanticGraphOnOntologyGraph(sqlGraph, ontologyGraph ?? null),
    [ontologyGraph, sqlGraph]
  );
  const unmatchedValues = [
    ...grounding.unmatchedTables,
    ...grounding.unmatchedColumns,
    ...grounding.unmatchedJoins,
  ];

  if (!sqlGraph) return null;

  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-card p-3"
      aria-labelledby="nl2sql-sql-grounding-title"
      data-testid="nl2sql-sql-grounding-panel"
    >
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Network size={16} className="shrink-0 text-foreground" aria-hidden="true" />
          <h4 id="nl2sql-sql-grounding-title" className="text-sm font-semibold text-foreground">
            {t("nl2sql.interpretation.graphTitle")}
          </h4>
        </div>
        <div className="flex flex-wrap gap-2">
          <GroundingStatusBadge result={grounding} />
          <StatusBadge
            variant="neutral"
            label={t("nl2sql.interpretation.graphMatchedCount", {
              count:
                grounding.matchedTables.length +
                grounding.matchedColumns.length +
                grounding.matchedJoins.length,
            })}
          />
        </div>
      </div>

      {!profileId ? (
        <Banner severity="info">{t("nl2sql.interpretation.graphNoProfile")}</Banner>
      ) : !ontologyGraph ? (
        <Banner severity="warning">{t("nl2sql.interpretation.graphLoadFailed")}</Banner>
      ) : ontologyGraph.nodes.length === 0 ? (
        <Banner severity="info">{t("nl2sql.interpretation.graphEmpty")}</Banner>
      ) : (
        <>
          {grounding.status === "unmatched" ? (
            <Banner severity="warning">{t("nl2sql.interpretation.graphNoMatch")}</Banner>
          ) : unmatchedValues.length > 0 ? (
            <Banner severity="warning" title={t("nl2sql.interpretation.graphPartialTitle")}>
              {t("nl2sql.interpretation.graphPartial")}
              <UnmatchedList values={unmatchedValues} />
            </Banner>
          ) : null}
          <div className="min-w-0 overflow-hidden" data-testid="nl2sql-sql-grounding-graph">
            <Suspense
              fallback={
                <div className="grid h-80 place-items-center rounded-md border border-border bg-background text-sm text-muted">
                  {t("nl2sql.interpretation.graphLoading")}
                </div>
              }
            >
              <LazyOntologyGraphCanvas
                graph={ontologyGraph}
                selectedNodeId={selectedNodeId}
                selectedEdgeId={selectedEdgeId}
                onSelectNode={setSelectedNodeId}
                onSelectEdge={setSelectedEdgeId}
                highlightNodeIds={grounding.highlightNodeIds}
                highlightEdgeIds={grounding.highlightEdgeIds}
                defaultViewMode="grounding"
              />
            </Suspense>
          </div>
          <div
            className="grid gap-3 rounded-md border border-border bg-background p-3 sm:grid-cols-3"
            aria-label={t("nl2sql.interpretation.graphListAria")}
            data-testid="nl2sql-sql-grounding-list"
          >
            <GroundingMatchRows
              title={t("nl2sql.interpretation.graphMatchedTables")}
              values={grounding.matchedTables}
            />
            <GroundingMatchRows
              title={t("nl2sql.interpretation.graphMatchedColumns")}
              values={grounding.matchedColumns}
            />
            <GroundingMatchRows
              title={t("nl2sql.interpretation.graphMatchedJoins")}
              values={grounding.matchedJoins}
            />
          </div>
        </>
      )}
    </section>
  );
}

function InterpretationArtifactPanel({
  artifact,
  profileId,
}: {
  artifact?: Nl2SqlInterpretationArtifact | null;
  profileId?: string;
}) {
  const sqlGraph = isSqlSemanticGraph(artifact?.sql.semantic_graph)
    ? artifact.sql.semantic_graph
    : null;
  const effectiveProfileId = profileId || artifact?.question.profile_id || "";

  if (!artifact) return null;
  if (!artifact.available) {
    return (
      <Banner severity="info" title={t("nl2sql.interpretation.graphTitle")}>
        {artifact.warnings.join(" ") || t("nl2sql.interpretation.unavailable")}
      </Banner>
    );
  }
  const template = parseQuestionTemplate(artifact.question.original_question);
  const inputFilter = questionSlotValue(template.slots, FILTER_LABELS);
  const inputFilterSlotExists = inputFilter !== undefined;
  const inputFilterEmpty = template.hasTemplate && inputFilterSlotExists && !inputFilter.trim();
  const sqlFilters = artifact.sql.filters.filter(Boolean);
  const hasFilterMismatch = inputFilterEmpty && sqlFilters.length > 0;
  // 「Ontology を使う」OFF(backend echo)のときは接地確認を出さない。未指定の旧データは互換で表示。
  const groundingEnabled = artifact.ontology_grounding_enabled !== false;

  if (!hasFilterMismatch && !(groundingEnabled && sqlGraph)) return null;

  return (
    <div
      className="grid gap-3"
      aria-label={t("nl2sql.interpretation.graphTitle")}
      data-testid="nl2sql-interpretation-panel"
    >
      {hasFilterMismatch && (
        <Banner severity="danger" title={t("nl2sql.interpretation.mismatchTitle")}>
          {t("nl2sql.interpretation.emptyFilterMismatch")}
        </Banner>
      )}
      {groundingEnabled && (
        <SqlOntologyGroundingPanel
          sqlGraph={sqlGraph}
          profileId={effectiveProfileId}
          ontologyGraph={artifact.ontology_graph ?? null}
        />
      )}
    </div>
  );
}

/**
 * 生成 SQL の処理手順(決定論 logical_steps)を番号付きで表示する独立パネル。
 * 「処理手順を表示」OFF のときは backend が steps を空にするため描画されない。
 */
function SqlLogicalStepsPanel({
  artifact,
}: {
  artifact?: Nl2SqlInterpretationArtifact | null;
}) {
  const available = artifact?.available ?? false;
  const steps = available ? (artifact?.sql.logical_steps ?? []) : [];
  const stepDetails = available ? (artifact?.sql.logical_step_details ?? []) : [];
  if (steps.length === 0 && stepDetails.length === 0) return null;

  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-card p-3"
      aria-labelledby="nl2sql-logical-steps-title"
      data-testid="nl2sql-logical-steps-panel"
    >
      <div className="flex min-w-0 items-center gap-2">
        <ListOrdered size={16} className="shrink-0 text-foreground" aria-hidden="true" />
        <h4 id="nl2sql-logical-steps-title" className="text-sm font-semibold text-foreground">
          {t("nl2sql.logicalSteps.title")}
        </h4>
      </div>
      <LogicalStepsList
        steps={stepDetails}
        fallbackSteps={steps}
        listAriaLabel={t("nl2sql.logicalSteps.listAria")}
      />
    </section>
  );
}

function ShowPromptArtifactPanel({
  artifact,
}: {
  artifact?: Nl2SqlShowPromptArtifact | null;
}) {
  const [promptOpen, setPromptOpen] = useState(false);

  if (!artifact) return null;
  if (!artifact.available) {
    return (
      <Banner severity="info" title={t("nl2sql.showPrompt.title")}>
        {artifact.unavailable_reason || artifact.warnings.join(" ") || t("nl2sql.showPrompt.unavailable")}
      </Banner>
    );
  }

  const handlePromptToggle = (event: SyntheticEvent<HTMLDetailsElement>) => {
    setPromptOpen(event.currentTarget.open);
  };

  return (
    <details
      className="rounded-md border border-border bg-background p-3"
      data-testid="nl2sql-show-prompt-panel"
      onToggle={handlePromptToggle}
    >
      <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 [&::-webkit-details-marker]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <FileText size={16} className="shrink-0" aria-hidden="true" />
          <span>{t("nl2sql.showPrompt.title")}</span>
        </span>
        <DisclosureChevron
          expanded={promptOpen}
          size={16}
          className="text-muted"
          data-testid="nl2sql-show-prompt-chevron"
        />
      </summary>
      {/* スクロール領域はキーボードでも操作できるよう focus 可能にする(WCAG 2.1.1)。 */}
      <pre
        tabIndex={0}
        role="region"
        aria-label={t("nl2sql.showPrompt.title")}
        className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-code p-3 text-xs leading-5 text-code-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        <code>{artifact.prompt}</code>
      </pre>
    </details>
  );
}

/**
 * 生成 SQL のサマリ部（安全/エンジンバッジ・コピー/実行ボタン・SQL コード・説明文）。
 * タイムライン `generate_sql` ステップ内に埋め込めるよう Card に包まない。
 * `onExecute` が渡されたときだけ補助的な実行ボタンを出す。
 */
export function GeneratedSqlSummary({
  result,
  profileId = "",
  executeLoading = false,
  onExecute,
}: {
  result: GeneratedSqlPanelData;
  profileId?: string;
  executeLoading?: boolean;
  onExecute?: () => void;
}) {
  const displayedSql = result.executable_sql || result.generated_sql;

  const copySql = async () => {
    try {
      await copyTextToClipboard(displayedSql);
      toast.success(t("common.action.copied"));
    } catch {
      toastError(t("common.action.copyFailed"));
    }
  };

  return (
    <div className="min-w-0 space-y-3">
      <ContentActionBar
        ariaLabel={t("nl2sql.sql.copy")}
        leading={
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge
              variant={result.safety.is_safe ? "success" : "danger"}
              label={result.safety.is_safe ? t("nl2sql.safety.safe") : t("nl2sql.safety.blocked")}
            />
            <span className="rounded-md bg-muted/30 px-2 py-1 text-xs text-foreground">
              {engineLabel(result.engine)}
            </span>
            {result.fallback_reason && (
              <span
                className="rounded-md bg-warning-bg px-2 py-1 text-xs text-warning"
                title={result.fallback_reason}
                aria-label={t("nl2sql.result.fallbackWithReason", {
                  reason: result.fallback_reason,
                })}
              >
                {t("nl2sql.result.fallback")}
              </span>
            )}
          </div>
        }
        testId="generated-sql-content-actions"
      >
        {onExecute && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={executeLoading}
            disabled={!result.safety.is_safe}
            onClick={onExecute}
          >
            <Play size={15} aria-hidden="true" />
            <span>{t("nl2sql.action.executePreview")}</span>
          </Button>
        )}
        <Button type="button" variant="secondary" size="sm" onClick={copySql}>
          <Copy size={15} aria-hidden="true" />
          <span>{t("nl2sql.sql.copy")}</span>
        </Button>
      </ContentActionBar>
      {/* スクロール領域はキーボードでも操作できるよう focus 可能にする(WCAG 2.1.1)。 */}
      <pre
        tabIndex={0}
        role="region"
        aria-label={t("nl2sql.sql.region")}
        className="max-h-72 overflow-auto rounded-md border border-border bg-code p-4 text-sm leading-6 text-code-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
      >
        <code>{displayedSql}</code>
      </pre>
      {result.fallback_reason && (
        <div
          className="rounded-md border border-warning/30 bg-warning-bg px-3 py-2 text-sm leading-6 text-warning"
          role="status"
        >
          <p className="font-medium">{t("nl2sql.result.fallbackTitle")}</p>
          <p className="mt-1 [overflow-wrap:anywhere]">{result.fallback_reason}</p>
        </div>
      )}
      <p className="text-sm leading-6 text-foreground">{result.explanation}</p>
      <InterpretationArtifactPanel artifact={result.interpretation} profileId={profileId} />
      <SqlLogicalStepsPanel artifact={result.interpretation} />
      <ShowPromptArtifactPanel artifact={result.show_prompt} />
    </div>
  );
}
