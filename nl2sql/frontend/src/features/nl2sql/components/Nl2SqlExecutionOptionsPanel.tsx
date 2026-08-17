import { Eye, FileText, Sparkles } from "lucide-react";

import { t } from "@/lib/i18n";
import type { Nl2SqlEngine } from "../types";

function OptionCheckbox({
  checked,
  disabled,
  icon: Icon,
  label,
  onChange,
}: {
  checked: boolean;
  disabled: boolean;
  icon: typeof Sparkles;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex min-h-11 items-start gap-3 rounded-md border border-border bg-card p-3 text-foreground">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.checked)}
        className="mt-1 h-4 w-4 shrink-0 rounded border-border text-primary focus:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60"
      />
      <span className="flex min-w-0 items-start gap-2">
        <Icon size={16} className="mt-0.5 shrink-0 text-muted" aria-hidden="true" />
        <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
      </span>
    </label>
  );
}

export function Nl2SqlExecutionOptionsPanel({
  disabled,
  engine,
  includeInterpretation,
  includeShowPrompt,
  onIncludeInterpretationChange,
  onIncludeShowPromptChange,
  onRewriteUseGlossaryChange,
  onRewriteUseSchemaChange,
  rewriteUseGlossary,
  rewriteUseSchema,
}: {
  disabled: boolean;
  engine: Nl2SqlEngine;
  includeInterpretation: boolean;
  includeShowPrompt: boolean;
  onIncludeInterpretationChange: (checked: boolean) => void;
  onIncludeShowPromptChange: (checked: boolean) => void;
  onRewriteUseGlossaryChange: (checked: boolean) => void;
  onRewriteUseSchemaChange: (checked: boolean) => void;
  rewriteUseGlossary: boolean;
  rewriteUseSchema: boolean;
}) {
  const showPromptUnavailable = includeShowPrompt && engine !== "select_ai";

  return (
    <section
      className="grid gap-3 rounded-md border border-border bg-background p-3 text-sm"
      aria-labelledby="nl2sql-execution-options-heading"
      data-testid="nl2sql-execution-options"
    >
      <div className="grid gap-1">
        <p id="nl2sql-execution-options-heading" className="font-semibold text-foreground">
          {t("nl2sql.executionOptions.title")}
        </p>
        <p className="text-xs leading-5 text-muted">{t("nl2sql.executionOptions.hint")}</p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <OptionCheckbox
          checked={rewriteUseGlossary}
          disabled={disabled}
          icon={Sparkles}
          label={t("nl2sql.rewrite.useGlossary")}
          onChange={onRewriteUseGlossaryChange}
        />
        <OptionCheckbox
          checked={rewriteUseSchema}
          disabled={disabled}
          icon={Sparkles}
          label={t("nl2sql.rewrite.useSchema")}
          onChange={onRewriteUseSchemaChange}
        />
        <OptionCheckbox
          checked={includeInterpretation}
          disabled={disabled}
          icon={Eye}
          label={t("nl2sql.executionOptions.includeInterpretation")}
          onChange={onIncludeInterpretationChange}
        />
        <OptionCheckbox
          checked={includeShowPrompt}
          disabled={disabled}
          icon={FileText}
          label={t("nl2sql.executionOptions.includeShowPrompt")}
          onChange={onIncludeShowPromptChange}
        />
      </div>
      {showPromptUnavailable ? (
        <p className="rounded-md border border-border bg-card px-3 py-2 text-xs leading-5 text-muted">
          {t("nl2sql.executionOptions.showPromptUnsupported")}
        </p>
      ) : null}
    </section>
  );
}
