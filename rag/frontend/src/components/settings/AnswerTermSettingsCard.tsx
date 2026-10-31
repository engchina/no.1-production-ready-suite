import { Card, CardContent, CardDescription, CardHeader, CardTitle, FormActionBar, FormSkeleton, FormStatus, Switch, TimedLoadingState } from "@production-ready/ui";
import { RotateCcw, Save } from "lucide-react";
import { useState } from "react";

import { ApiErrorState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useAnsweringSettings, useUpdateAnsweringSettings } from "@/lib/queries";
import { toast } from "@/lib/toast";

type UsageField = "runtime_terms_answer_enabled" | "runtime_rules_confirmation_enabled" | "runtime_rules_answer_enabled";
const labels = { runtime_terms_answer_enabled: "runtimeTermsAnswer", runtime_rules_confirmation_enabled: "runtimeRulesConfirmation", runtime_rules_answer_enabled: "runtimeRulesAnswer" } as const;

export function AnswerTermSettingsCard({ field = "runtime_terms_answer_enabled" }: { field?: UsageField }) {
  const label = labels[field];
  const query = useAnsweringSettings();
  return <Card data-testid={field === "runtime_terms_answer_enabled" ? "answer-term-settings-card" : field}>
    <CardHeader>
      <CardTitle>{t(`settings.answering.${label}`)}</CardTitle>
      <CardDescription>{t(`settings.answering.${label}Hint`)}</CardDescription>
    </CardHeader>
    <CardContent>
      {query.isPending ? <TimedLoadingState label={t("settings.answering.loading")} operationKey="answer-term-settings" framed={false}><FormSkeleton fields={1} /></TimedLoadingState> : null}
      {query.isError ? <ApiErrorState error={query.error} fallback={t("settings.answering.loadError")} onRetry={() => void query.refetch()} /> : null}
      {query.data ? <AnswerTermForm field={field} key={String(query.data[field] ?? false)} saved={query.data[field] ?? false} /> : null}
    </CardContent>
  </Card>;
}

function AnswerTermForm({ saved, field }: { saved: boolean; field: UsageField }) {
  const label = labels[field];
  const [enabled, setEnabled] = useState(saved);
  const save = useUpdateAnsweringSettings();
  const dirty = enabled !== saved;
  useLeaveGuard(dirty, save.isPending);
  return <div className="space-y-4">
    <Switch id={field} aria-label={t(`settings.answering.${label}`)} checked={enabled} disabled={save.isPending} onCheckedChange={(value) => { save.reset(); setEnabled(value); }} />
    <FormActionBar ariaLabel={t("settings.answering.actions.label")}
      primaryActions={[{ id: "save-answer-terms", label: t("settings.answering.save"), icon: Save, loading: save.isPending, disabled: !dirty, onClick: () => save.mutate({ [field]: enabled }, { onSuccess: () => toast.success(t("settings.answering.saved")) }) }]}
      secondaryActions={[{ id: "reset-answer-terms", label: t("settings.retrieval.actions.reset"), icon: RotateCcw, disabled: !dirty || save.isPending, onClick: () => { setEnabled(saved); save.reset(); } }]}
      status={save.isError ? <FormStatus tone="danger" message={save.error.message} /> : null} />
  </div>;
}
