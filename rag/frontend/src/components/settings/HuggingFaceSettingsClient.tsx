"use client";

import {
  PageBody,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  FormStatus,
  Skeleton,
  TextField,
} from "@engchina/production-ready-ui";
import {
  Eye,
  EyeOff,
  HardDriveDownload,
  Save,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";

import { ErrorState } from "@/components/StateViews";
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
      <PageBody>
        <Skeleton className="h-20 w-full rounded-lg" />
        <Skeleton className="h-[360px] w-full rounded-lg" />
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody>
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.huggingface.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = optimistic ?? query.data;
  if (!settings) return null;

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.huggingface.saveError");
  const envPreview = buildEnvFile(form, settings);

  return (
    <PageBody>
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
                <HardDriveDownload size={20} aria-hidden />
                <CardTitle className="text-lg">{t("settings.huggingface.cardTitle")}</CardTitle>
              </div>
            </CardHeader>

            <CardContent className="space-y-5 p-6">
              <TextField
                id="hf-endpoint"
                label={t("settings.huggingface.field.endpoint")}
                value={form.endpoint}
                onValueChange={(value) => updateForm({ endpoint: value })}
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
                <Button type="submit" size="lg" loading={save.isPending} icon={Save}>
                  {t("settings.huggingface.actions.save")}
                </Button>
                {saved ? (
                  <FormStatus tone="success" message={t("settings.huggingface.actions.saved")} />
                ) : null}
                {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
              </div>

              <p className="text-xs leading-relaxed text-fg-muted">{t("settings.huggingface.hint")}</p>
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
    </PageBody>
  );
}

function StatusPanel({ settings }: { settings: HuggingFaceSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
            <ShieldCheck size={20} aria-hidden />
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
      <span className="text-fg-muted">{label}</span>
      <span
        className={cn(
          "break-all text-right font-medium",
          ok === undefined ? "text-fg" : ok ? "text-success-fg" : "text-warning-fg"
        )}
      >
        {value || "—"}
      </span>
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
        <label htmlFor={id} className="text-sm font-medium text-fg">
          {t("settings.huggingface.field.token")}
        </label>
        {hasSavedSecret ? (
          <span className="rounded-full border border-success-border bg-success-subtle px-2 py-0.5 text-xs font-medium text-success-fg">
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
            "h-11 w-full rounded-md border border-border-control bg-surface px-3 pr-12 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled"
          )}
        />
        <button
          type="button"
          onClick={onToggleVisible}
          disabled={disabled}
          aria-label={
            visible ? t("settings.huggingface.secrets.hide") : t("settings.huggingface.secrets.show")
          }
          className="absolute right-0 top-0 flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-md text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
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
    <label className="flex cursor-pointer items-start gap-3 rounded-md border border-border bg-surface-sunken px-4 py-3 text-sm transition-colors hover:bg-info-subtle">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 h-4 w-4 cursor-pointer accent-[var(--color-accent-emphasis)]"
      />
      <span className="text-fg">{label}</span>
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
