"use client";

import {
  Eye,
  EyeOff,
  HardDriveDownload,
  Save,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  SETTINGS_DETAIL_GRID_CLASS,
  SettingsSupplementalPanels,
  formatSettingsEnvValue,
} from "@/components/settings/SettingsPreviewPanels";
import {
  ApiError,
  type HuggingFaceSettingsData,
  type HuggingFaceSettingsUpdate,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useHuggingFaceSettings, useUpdateHuggingFaceSettings } from "@/lib/queries";
import { cn } from "@/lib/utils";

interface HuggingFaceForm {
  endpoint: string;
  token: string;
  clearToken: boolean;
}

const EMPTY_FORM: HuggingFaceForm = {
  endpoint: "",
  token: "",
  clearToken: false,
};

/** HuggingFace モデルダウンロード(token / ミラー)の runtime 設定フォーム。 */
export function HuggingFaceSettingsClient() {
  const query = useHuggingFaceSettings();
  const save = useUpdateHuggingFaceSettings();

  const [form, setForm] = useState<HuggingFaceForm>(EMPTY_FORM);
  const [tokenVisible, setTokenVisible] = useState(false);
  const [saved, setSaved] = useState(false);
  const [optimistic, setOptimistic] = useState<HuggingFaceSettingsData | null>(null);

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
      setOptimistic(null);
    }
  }, [query.data]);

  function updateForm(update: Partial<HuggingFaceForm>) {
    setForm((current) => ({ ...current, ...update }));
    setSaved(false);
  }

  function submit() {
    save.mutate(payloadFromForm(form), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimistic(data);
        setSaved(true);
      },
    });
  }

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-20 w-full rounded-lg" />
        <Skeleton className="h-[360px] w-full rounded-lg" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="p-8">
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.huggingface.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = optimistic ?? query.data;
  if (!settings) return null;

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.huggingface.saveError");
  const envPreview = buildEnvFile(form, settings);

  return (
    <div className="p-8">
      <div className={SETTINGS_DETAIL_GRID_CLASS}>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Card className="rounded-md">
            <CardHeader className="p-6 pb-0">
              <div className="flex items-center gap-2 border-b border-border pb-5">
                <HardDriveDownload size={18} aria-hidden />
                <CardTitle className="text-lg">{t("settings.huggingface.cardTitle")}</CardTitle>
              </div>
            </CardHeader>

            <CardContent className="space-y-5 p-6">
              <TextField
                id="hf-endpoint"
                label={t("settings.huggingface.field.endpoint")}
                value={form.endpoint}
                onChange={(value) => updateForm({ endpoint: value })}
                placeholder={t("settings.huggingface.placeholder.endpoint")}
                helper={t("settings.huggingface.helper.endpoint")}
              />

              <TokenField
                value={form.token}
                visible={tokenVisible}
                disabled={form.clearToken}
                hasSavedSecret={settings.token_configured}
                onToggleVisible={() => setTokenVisible((current) => !current)}
                onChange={(value) => updateForm({ token: value })}
              />

              {settings.token_configured ? (
                <SecretClearCheckbox
                  checked={form.clearToken}
                  label={t("settings.huggingface.secrets.clearToken")}
                  onChange={(clear) => updateForm({ clearToken: clear, token: clear ? "" : form.token })}
                />
              ) : null}

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button type="submit" size="lg" loading={save.isPending}>
                  <Save size={16} aria-hidden />
                  {save.isPending
                    ? t("settings.huggingface.actions.saving")
                    : t("settings.huggingface.actions.save")}
                </Button>
                {saved ? (
                  <FormStatus tone="success" message={t("settings.huggingface.actions.saved")} />
                ) : null}
                {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
              </div>

              <p className="text-xs leading-relaxed text-muted">{t("settings.huggingface.hint")}</p>
            </CardContent>
          </Card>
        </form>

        <SettingsSupplementalPanels
          status={<StatusPanel settings={settings} />}
          env={{
            description: t("settings.huggingface.env.description"),
            value: envPreview,
          }}
          operation={{
            description: t("settings.huggingface.ops.description"),
            notes: [
              t("settings.huggingface.ops.persist"),
              t("settings.huggingface.ops.mount"),
              t("settings.huggingface.ops.bake"),
            ],
          }}
        />
      </div>
    </div>
  );
}

function StatusPanel({ settings }: { settings: HuggingFaceSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <ShieldCheck size={18} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.huggingface.status.title")}</CardTitle>
            <CardDescription>{t("settings.huggingface.status.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <MetadataRow
          label={t("settings.huggingface.status.token")}
          value={
            settings.token_configured
              ? t("settings.huggingface.status.tokenConfigured")
              : t("settings.huggingface.status.tokenNotConfigured")
          }
          ok={settings.token_configured}
        />
        <MetadataRow
          label={t("settings.huggingface.status.endpoint")}
          value={settings.endpoint || t("settings.huggingface.status.endpointDefault")}
        />
      </CardContent>
    </Card>
  );
}

function MetadataRow({ label, value, ok }: { label: string; value: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-border pt-3 text-sm first:border-t-0 first:pt-0">
      <span className="text-muted">{label}</span>
      <span
        className={cn(
          "break-all text-right font-medium",
          ok === undefined ? "text-foreground" : ok ? "text-success" : "text-warning"
        )}
      >
        {value || "—"}
      </span>
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  placeholder,
  helper,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  helper?: string;
}) {
  const hintId = `${id}-hint`;

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-describedby={helper ? hintId : undefined}
        className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring"
      />
      {helper ? (
        <p id={hintId} className="text-xs leading-relaxed text-muted">
          {helper}
        </p>
      ) : null}
    </div>
  );
}

function TokenField({
  value,
  visible,
  disabled,
  hasSavedSecret,
  onChange,
  onToggleVisible,
}: {
  value: string;
  visible: boolean;
  disabled: boolean;
  hasSavedSecret: boolean;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const id = "hf-token";
  const hintId = `${id}-hint`;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {t("settings.huggingface.field.token")}
        </label>
        {hasSavedSecret ? (
          <span className="rounded-full border border-success/30 bg-success-bg px-2 py-0.5 text-xs font-medium text-success">
            {t("settings.huggingface.secrets.saved")}
          </span>
        ) : null}
      </div>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          disabled={disabled}
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
          placeholder={
            hasSavedSecret
              ? t("settings.huggingface.placeholder.tokenSaved")
              : t("settings.huggingface.placeholder.token")
          }
          aria-describedby={hintId}
          className={cn(
            "h-11 w-full rounded-md border border-border bg-card px-3 pr-12 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:bg-background disabled:text-muted"
          )}
        />
        <button
          type="button"
          onClick={onToggleVisible}
          disabled={disabled}
          aria-label={
            visible ? t("settings.huggingface.secrets.hide") : t("settings.huggingface.secrets.show")
          }
          className="absolute right-0 top-0 flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-md text-muted transition-colors hover:bg-background hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {hasSavedSecret
          ? t("settings.huggingface.helper.tokenSaved")
          : t("settings.huggingface.helper.token")}
      </p>
    </div>
  );
}

function SecretClearCheckbox({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-background px-4 py-3 text-sm transition-colors hover:bg-info-bg/30">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 cursor-pointer accent-[var(--primary)]"
      />
      <span className="text-foreground">{label}</span>
    </label>
  );
}

function formFromSettings(settings: HuggingFaceSettingsData): HuggingFaceForm {
  return {
    endpoint: settings.endpoint,
    token: "",
    clearToken: false,
  };
}

function payloadFromForm(form: HuggingFaceForm): HuggingFaceSettingsUpdate {
  const payload: HuggingFaceSettingsUpdate = {
    endpoint: form.endpoint,
  };
  if (form.clearToken) payload.clear_token = true;
  else if (form.token !== "") payload.token = form.token;
  return payload;
}

function buildEnvFile(form: HuggingFaceForm, settings: HuggingFaceSettingsData): string {
  const token = form.clearToken
    ? ""
    : form.token.trim()
      ? t("settings.preview.secret.entered")
      : settings.token_configured
        ? t("settings.preview.secret.saved")
        : "";
  const entries: [string, string][] = [
    ["HF_TOKEN", token],
    ["HF_ENDPOINT", form.endpoint],
  ];
  return [
    "# HuggingFace モデルダウンロード",
    ...entries.map(([key, value]) => `${key}=${formatSettingsEnvValue(value)}`),
  ].join("\n");
}
