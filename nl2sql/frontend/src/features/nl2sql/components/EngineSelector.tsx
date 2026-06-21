import { Bot, DatabaseZap, Sparkles } from "lucide-react";

import { Button } from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import type { Nl2SqlEngine } from "../types";

const ENGINE_OPTIONS: Array<{ value: Nl2SqlEngine; label: string; description: string }> = [
  {
    value: "auto",
    label: t("nl2sql.engine.auto"),
    description: t("nl2sql.engine.auto.desc"),
  },
  {
    value: "select_ai_agent",
    label: t("nl2sql.engine.agent"),
    description: t("nl2sql.engine.agent.desc"),
  },
  {
    value: "select_ai",
    label: t("nl2sql.engine.selectAi"),
    description: t("nl2sql.engine.selectAi.desc"),
  },
  {
    value: "enterprise_ai_direct",
    label: t("nl2sql.engine.direct"),
    description: t("nl2sql.engine.direct.desc"),
  },
];

function EngineIcon({ engine }: { engine: Nl2SqlEngine }) {
  if (engine === "select_ai_agent") return <Bot size={16} aria-hidden="true" />;
  if (engine === "select_ai") return <DatabaseZap size={16} aria-hidden="true" />;
  return <Sparkles size={16} aria-hidden="true" />;
}

export function EngineSelector({
  value,
  onChange,
  disabled,
}: {
  value: Nl2SqlEngine;
  onChange: (value: Nl2SqlEngine) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold text-slate-900">{t("nl2sql.engine.label")}</legend>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        {ENGINE_OPTIONS.map((option) => {
          const selected = option.value === value;
          return (
            <Button
              key={option.value}
              type="button"
              variant={selected ? "primary" : "secondary"}
              size="md"
              disabled={disabled}
              className="h-auto min-h-16 justify-start gap-3 whitespace-normal text-left"
              aria-pressed={selected}
              onClick={() => onChange(option.value)}
            >
              <EngineIcon engine={option.value} />
              <span className="grid gap-1">
                <span className="text-sm font-semibold">{option.label}</span>
                <span className="text-xs font-normal opacity-80">{option.description}</span>
              </span>
            </Button>
          );
        })}
      </div>
    </fieldset>
  );
}
