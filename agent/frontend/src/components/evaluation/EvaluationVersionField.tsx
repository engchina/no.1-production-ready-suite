import { SelectField } from "@engchina/production-ready-ui";

import type { AgentProfile, EvaluatedAgentVersion, EvaluationTarget } from "@/lib/api";
import { t } from "@/lib/i18n";

/**
 * 評価する版の既定（#810）。公開していない変更があれば下書き、なければ公開中の版。
 * 公開した版が無い業務 Agent は下書きだけ。
 */
export function defaultEvaluationTarget(agent: AgentProfile | undefined): EvaluationTarget {
  if (!agent || agent.published_version === null || agent.unpublished_changes) return "draft";
  return "published";
}

/** 選んだ版が今の業務 Agent で使えなければ既定に戻す（公開した版が無いのに「公開中の版」など）。 */
export function usableEvaluationTarget(agent: AgentProfile | undefined, chosen: EvaluationTarget | null): EvaluationTarget {
  if (chosen === "draft") return "draft";
  if (chosen === "published" && agent && agent.published_version !== null) return "published";
  return defaultEvaluationTarget(agent);
}

/** 評価した版の短い表示（「v3」「下書き」。#810 より前の評価は「—」）。 */
export function evaluatedVersionLabel(version: EvaluatedAgentVersion | undefined): string {
  if (version === "draft") return t("evaluation.version.draftShort");
  if (typeof version === "number") return t("evaluation.version.number", { version });
  return "—";
}

/**
 * 「評価する版」（#810）。評価の開始で、公開中の版か下書きかを選ぶ（Copilot Studio・LangSmith の
 * 評価の対象の選択と同じ）。選んだ版は評価の job に残り、一覧・概要に出る。
 */
export function EvaluationVersionField({
  id,
  agent,
  value,
  onChange,
  disabled,
}: {
  id: string;
  agent: AgentProfile | undefined;
  value: EvaluationTarget;
  onChange: (value: EvaluationTarget) => void;
  disabled?: boolean;
}) {
  const published = agent?.published_version ?? null;
  const options: { value: EvaluationTarget; label: string }[] = [
    ...(published !== null
      ? [{ value: "published" as const, label: t("evaluation.version.published", { version: published }) }]
      : []),
    {
      value: "draft",
      label: agent?.unpublished_changes ? t("evaluation.version.draftChanged") : t("evaluation.version.draftSame"),
    },
  ];
  return (
    <SelectField<EvaluationTarget>
      id={id}
      label={t("evaluation.version.label")}
      helper={published === null ? t("evaluation.version.noPublished") : t("evaluation.version.helper")}
      width="md"
      value={value}
      options={options}
      onValueChange={onChange}
      disabled={disabled || !agent}
      data-testid={id}
    />
  );
}
