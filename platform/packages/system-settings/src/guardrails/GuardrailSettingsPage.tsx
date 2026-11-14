import {
  ButtonLink,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  ErrorState,
  Fieldset,
  FormActionBar,
  FormSkeleton,
  FormStatus,
  PageBody,
  TimedLoadingState,
  toast,
} from "@production-ready/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RotateCcw, Save, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";

import { useSettingsDraftGuard, type DraftGuardMessages } from "../guards/useSettingsDraftGuard";
import { SYSTEM_SETTINGS_PATHS } from "../paths";
import { GUARDRAIL_SETTINGS_MESSAGES, type GuardrailSettingsMessages } from "./messages";
import { GUARDRAIL_SETTINGS_QUERY_KEY, type GuardrailSettingsApi, type GuardrailSettingsUpdate } from "./types";

const OFFICIAL_DOCS_URL = "https://docs.oracle.com/en-us/iaas/Content/generative-ai/guardrails.htm";

export interface GuardrailSettingsPageProps {
  /** 製品の API 関数（GET / PATCH /api/settings/guardrail）。 */
  api: GuardrailSettingsApi;
  /** API エラーから画面に出すメッセージを取り出す（製品の ApiError など）。undefined なら既定の文言。 */
  errorMessage?: (error: unknown) => string | undefined;
  /** 既定の文言の上書き。 */
  messages?: Partial<GuardrailSettingsMessages>;
  /** 離脱確認ダイアログの文言の上書き。 */
  draftGuardMessages?: Partial<DraftGuardMessages>;
}

/** 3製品共通の安全チェック（OCI Guardrails）の入力・出力の検査の有効・無効（#5061）。 */
export function GuardrailSettingsPage({ api, errorMessage, messages, draftGuardMessages }: GuardrailSettingsPageProps) {
  const m = { ...GUARDRAIL_SETTINGS_MESSAGES, ...messages };
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: GUARDRAIL_SETTINGS_QUERY_KEY, queryFn: ({ signal }) => api.get({ signal }) });
  const save = useMutation({
    mutationFn: (payload: GuardrailSettingsUpdate) => api.update(payload),
    onSuccess: (data) => queryClient.setQueryData(GUARDRAIL_SETTINGS_QUERY_KEY, data),
  });
  const [selection, setSelection] = useState<GuardrailSettingsUpdate | null>(null);
  useEffect(() => {
    if (query.data) setSelection({ input_enabled: query.data.input_enabled, output_enabled: query.data.output_enabled });
  }, [query.data]);
  const settings = query.data;
  const dirty = selection !== null && settings !== undefined
    && (selection.input_enabled !== settings.input_enabled || selection.output_enabled !== settings.output_enabled);
  useSettingsDraftGuard(dirty, save.isPending, draftGuardMessages);

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState label={m.loading} operationKey="settings-guardrail-load" placement="page" testId="settings-guardrail-loading">
          <FormSkeleton />
        </TimedLoadingState>
      </PageBody>
    );
  }
  if (query.isError || !settings) {
    return (
      <PageBody wide>
        <ErrorState message={errorMessage?.(query.error) ?? m.loadError} onRetry={() => void query.refetch()} retryLabel={m.retry} />
      </PageBody>
    );
  }
  if (selection === null) return null;
  const warning = (selection.input_enabled || selection.output_enabled) && !settings.oci_configured
    ? settings.oci_warning_code === "oci_guardrails_compartment_missing" ? m.compartmentMissing : m.credentialsInvalid
    : null;
  const sides = [
    { key: "input_enabled", label: m.input, description: m.inputDescription, id: "guardrail-input-description" },
    { key: "output_enabled", label: m.output, description: m.outputDescription, id: "guardrail-output-description" },
  ] as const;

  return (
    <PageBody wide>
      <Card>
        <CardHeader>
          <div className="flex items-start gap-3">
            <div data-surface-tint="info" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
              <ShieldCheck size={20} aria-hidden />
            </div>
            <div className="min-w-0">
              <CardTitle>{m.ociLabel}</CardTitle>
              <CardDescription>{m.ociDescription}</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          <Fieldset legend={m.selection}>
            <div className="space-y-3">
              {sides.map((side) => (
                <div key={side.key}>
                  <label className="flex cursor-pointer items-center gap-2 rounded-md border border-border px-3 py-3 text-sm text-fg">
                    <input type="checkbox" aria-label={side.label} aria-describedby={side.id}
                      className="accent-accent-emphasis focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
                      checked={selection[side.key]} disabled={save.isPending}
                      onChange={(event) => { save.reset(); setSelection({ ...selection, [side.key]: event.target.checked }); }} />
                    {side.label}
                  </label>
                  <p id={side.id} className="mt-1 text-sm text-fg-muted">{side.description}</p>
                </div>
              ))}
            </div>
          </Fieldset>
          <p className="text-sm leading-relaxed text-fg-muted">{m.resultHandling}</p>
          <p>
            <a href={OFFICIAL_DOCS_URL} target="_blank" rel="noopener noreferrer" className="text-sm text-accent-fg underline underline-offset-2">
              {m.officialDocs}
            </a>
          </p>
          <FormStatus tone="info" message={m.overview} />
          {warning ? (
            <div className="space-y-2">
              <FormStatus tone="warning" message={warning} />
              <ButtonLink to={SYSTEM_SETTINGS_PATHS.oci} variant="secondary">{m.ociSettingsLink}</ButtonLink>
            </div>
          ) : null}
          <FormActionBar
            ariaLabel={m.actionsLabel}
            primaryActions={[{
              id: "save", label: m.save, icon: Save, loading: save.isPending, disabled: !dirty,
              onClick: () => save.mutate(selection, {
                onSuccess: (data) => {
                  setSelection({ input_enabled: data.input_enabled, output_enabled: data.output_enabled });
                  toast.success(m.saved);
                },
              }),
            }]}
            secondaryActions={[{
              id: "reset", label: m.reset, icon: RotateCcw, disabled: !dirty || save.isPending,
              onClick: () => { save.reset(); setSelection({ input_enabled: settings.input_enabled, output_enabled: settings.output_enabled }); },
            }]}
            status={save.isError ? <FormStatus tone="danger" message={(save.error && errorMessage?.(save.error)) || m.saveError} />
              : dirty ? <FormStatus tone="warning" message={m.unsaved} /> : null}
          />
        </CardContent>
      </Card>
    </PageBody>
  );
}
