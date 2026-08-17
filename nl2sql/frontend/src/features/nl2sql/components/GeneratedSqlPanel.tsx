import { useState, type SyntheticEvent } from "react";
import { ChevronDown, Copy, FileText, Play, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Banner, StatusBadge, toast } from "@engchina/production-ready-ui";

import { ContentActionBar } from "@/components/ContentActionBar";
import { t } from "@/lib/i18n";
import { engineLabel } from "../labels";
import { profileRecordDisplayLabel } from "../profileDisplay";
import type {
  GeneratedSqlPanelData,
  Nl2SqlInterpretationArtifact,
  Nl2SqlShowPromptArtifact,
} from "../types";

const QUESTION_SLOT_LABELS = [
  "対象テーブル",
  "対象テーブル（複数可）",
  "テーブル間の関連",
  "抽出項目",
  "抽出条件",
  "条件",
  "WHERE条件",
  "WHERE 条件",
  "検索条件",
  "集計内容（件数・合計・平均など）",
  "集計単位（グループ化）",
  "並び替え（項目と昇順／降順）",
  "表示件数（上位N件）",
];
const TARGET_TABLE_LABELS = ["対象テーブル", "対象テーブル（複数可）"];
const SELECT_ITEM_LABELS = ["抽出項目"];
const FILTER_LABELS = ["抽出条件", "条件", "WHERE条件", "WHERE 条件", "検索条件"];
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

function TextValue({ value }: { value: string }) {
  return <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{value}</span>;
}

function CompactList({ items }: { items: string[] }) {
  const values = items.filter(Boolean);
  if (values.length === 0) return <span className="text-muted">-</span>;
  return (
    <span className="flex flex-wrap gap-1.5">
      {values.slice(0, 8).map((item) => (
        <span
          key={item}
          className="max-w-full rounded-md bg-muted/30 px-2 py-1 font-mono text-xs text-foreground [overflow-wrap:anywhere]"
        >
          {item}
        </span>
      ))}
      {values.length > 8 ? (
        <span className="rounded-md bg-muted/30 px-2 py-1 text-xs text-muted">
          {t("nl2sql.interpretation.more", { count: values.length - 8 })}
        </span>
      ) : null}
    </span>
  );
}

function InterpretationArtifactPanel({
  artifact,
}: {
  artifact?: Nl2SqlInterpretationArtifact | null;
}) {
  if (!artifact) return null;
  if (!artifact.available) {
    return (
      <Banner severity="info" title={t("nl2sql.interpretation.title")}>
        {artifact.warnings.join(" ") || t("nl2sql.interpretation.unavailable")}
      </Banner>
    );
  }
  const template = parseQuestionTemplate(artifact.question.original_question);
  const targetTable = questionSlotValue(template.slots, TARGET_TABLE_LABELS);
  const selectItems = questionSlotValue(template.slots, SELECT_ITEM_LABELS);
  const inputFilter = questionSlotValue(template.slots, FILTER_LABELS);
  const inputFilterSlotExists = inputFilter !== undefined;
  const inputFilterEmpty = template.hasTemplate && inputFilterSlotExists && !inputFilter.trim();
  const sqlFilters = artifact.sql.filters.filter(Boolean);
  const hasFilterMismatch = inputFilterEmpty && sqlFilters.length > 0;

  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-background p-3"
      aria-labelledby="nl2sql-interpretation-title"
      data-testid="nl2sql-interpretation-panel"
    >
      {hasFilterMismatch && (
        <Banner severity="danger" title={t("nl2sql.interpretation.mismatchTitle")}>
          {t("nl2sql.interpretation.emptyFilterMismatch")}
        </Banner>
      )}
      <div className="flex min-w-0 items-center gap-2">
        <Sparkles size={16} className="shrink-0 text-foreground" aria-hidden="true" />
        <h3 id="nl2sql-interpretation-title" className="text-sm font-semibold text-foreground">
          {t("nl2sql.interpretation.title")}
        </h3>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        <dl className="grid content-start gap-2 rounded-md border border-border bg-card p-3 text-xs">
          <div>
            <dt className="font-semibold text-foreground">{t("nl2sql.interpretation.inputTitle")}</dt>
            <dd className="mt-1 text-muted">
              {template.hasTemplate
                ? t("nl2sql.interpretation.templateDetected")
                : t("nl2sql.interpretation.noTemplate")}
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.inputTargetTable")}</dt>
            <dd className="mt-1 leading-5 text-foreground">
              <TextValue value={targetTable || "-"} />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.inputSelectItems")}</dt>
            <dd className="mt-1 leading-5 text-foreground">
              <TextValue
                value={
                  selectItems
                    || (template.hasTemplate ? t("nl2sql.interpretation.emptySlot") : "-")
                }
              />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.inputFilter")}</dt>
            <dd className="mt-1 leading-5 text-foreground">
              <TextValue
                value={
                  inputFilterEmpty
                    ? t("nl2sql.interpretation.emptyFilter")
                    : inputFilter || "-"
                }
              />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.profile")}</dt>
            <dd className="mt-1 leading-5 text-foreground">
              {profileRecordDisplayLabel(artifact.question)}
            </dd>
          </div>
        </dl>
        <dl className="grid content-start gap-2 rounded-md border border-border bg-card p-3 text-xs">
          <div>
            <dt className="font-semibold text-foreground">{t("nl2sql.interpretation.sqlTitle")}</dt>
            <dd className="mt-1 text-muted">{artifact.sql.source || "sql_semantics"}</dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.sqlSummary")}</dt>
            <dd className="mt-1 leading-5 text-foreground">{artifact.sql.summary || "-"}</dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.targetObjects")}</dt>
            <dd className="mt-1">
              <CompactList items={artifact.sql.tables.length ? artifact.sql.tables : artifact.question.target_objects} />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.sqlColumns")}</dt>
            <dd className="mt-1">
              <CompactList items={artifact.sql.columns} />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.sqlFilters")}</dt>
            <dd className="mt-1">
              <CompactList items={sqlFilters} />
            </dd>
          </div>
          <div>
            <dt className="font-medium text-muted">{t("nl2sql.interpretation.aggregations")}</dt>
            <dd className="mt-1">
              <CompactList items={artifact.sql.aggregations} />
            </dd>
          </div>
        </dl>
      </div>
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

  const promptState = promptOpen ? "expanded" : "collapsed";

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
        <ChevronDown
          size={16}
          className={`shrink-0 text-muted transition-transform duration-200 motion-reduce:transition-none ${
            promptOpen ? "rotate-180" : ""
          }`}
          data-state={promptState}
          data-testid="nl2sql-show-prompt-chevron"
          aria-hidden="true"
        />
      </summary>
      <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-code p-3 text-xs leading-5 text-code-fg">
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
  executeLoading = false,
  onExecute,
}: {
  result: GeneratedSqlPanelData;
  executeLoading?: boolean;
  onExecute?: () => void;
}) {
  const displayedSql = result.executable_sql || result.generated_sql;

  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(displayedSql);
      toast.success(t("common.action.copied"));
    } catch {
      toast.error(t("common.action.copyFailed"));
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
      <pre className="max-h-72 overflow-auto rounded-md border border-border bg-code p-4 text-sm leading-6 text-code-fg">
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
      <InterpretationArtifactPanel artifact={result.interpretation} />
      <ShowPromptArtifactPanel artifact={result.show_prompt} />
    </div>
  );
}
