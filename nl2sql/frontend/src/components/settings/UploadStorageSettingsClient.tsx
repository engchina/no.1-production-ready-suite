"use client";

import {
  AlertCircle,
  CheckCircle2,
  Cloud,
  HardDrive,
  Save,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { toast } from "@engchina/production-ready-ui";

import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  SETTINGS_DETAIL_GRID_CLASS,
  SettingsSupplementalPanels,
  formatSettingsEnvValue,
} from "@/components/settings/SettingsPreviewPanels";
import {
  ApiError,
  type UploadStorageBackend,
  type UploadStorageSettingsData,
  type UploadStorageSettingsUpdate,
} from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useUpdateUploadStorageSettings,
  useUploadStorageSettings,
} from "@/lib/queries";
import { readStoredOciSettingsDraft } from "@/lib/oci-settings";
import { cn } from "@/lib/utils";

interface UploadStorageForm {
  backend: UploadStorageBackend;
  localStorageDir: string;
  objectStorageBucket: string;
}

type FieldErrors = Partial<Record<keyof UploadStorageForm | "objectStorageNamespace", string>>;

const EMPTY_FORM: UploadStorageForm = {
  backend: "local",
  localStorageDir: "",
  objectStorageBucket: "",
};

const DEFAULT_LOCAL_STORAGE_DIR = "/u01/production-ready-nl2sql";
const DEFAULT_OBJECT_STORAGE_BUCKET = "nl2sql-originals";
const OBJECT_STORAGE_NAME_PATTERN = /^[A-Za-z0-9._-]+$/;

/** ドキュメントアップロード原本の保存先設定。 */
export function UploadStorageSettingsClient() {
  const query = useUploadStorageSettings();
  const save = useUpdateUploadStorageSettings();
  const [form, setForm] = useState<UploadStorageForm>(EMPTY_FORM);
  const [objectStorageNamespace, setObjectStorageNamespace] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
      setObjectStorageNamespace(resolveObjectStorageNamespace(query.data));
      setErrors({});
    }
  }, [query.data]);

  function updateForm(update: Partial<UploadStorageForm>) {
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(update) as Array<keyof UploadStorageForm>) {
        delete next[key];
      }
      if ("backend" in update) delete next.objectStorageNamespace;
      return next;
    });
    save.reset();
  }

  function submit() {
    setErrors({});
    save.mutate(payloadFromForm(form, objectStorageNamespace), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setObjectStorageNamespace(resolveObjectStorageNamespace(data));
        setErrors({});
        toast.success(t("settings.uploadStorage.actions.saved"));
      },
    });
  }

  const operationWarnings = useMemo(
    () => Object.values(validateForm(form, objectStorageNamespace)),
    [form, objectStorageNamespace]
  );

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-64 w-full rounded-lg" />
        <Skeleton className="h-72 w-full rounded-lg" />
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
              : t("settings.uploadStorage.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings) return null;

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.uploadStorage.saveError");
  const envPreview = buildUploadStorageEnvFile(form, objectStorageNamespace, settings);

  return (
    <div className="space-y-5 p-8">
      <div className={SETTINGS_DETAIL_GRID_CLASS}>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                  {form.backend === "oci" ? (
                    <Cloud size={20} aria-hidden />
                  ) : (
                    <HardDrive size={20} aria-hidden />
                  )}
                </div>
                <div>
                  <CardTitle>{t("settings.uploadStorage.destination.title")}</CardTitle>
                  <CardDescription>
                    {t("settings.uploadStorage.destination.description")}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <fieldset className="space-y-3">
                <legend className="text-sm font-medium text-foreground">
                  {t("settings.uploadStorage.field.backend")}
                </legend>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <BackendOption
                    id="upload-storage-local"
                    value="local"
                    checked={form.backend === "local"}
                    icon={<HardDrive size={20} aria-hidden />}
                    title={t("settings.uploadStorage.backend.local")}
                    description={t("settings.uploadStorage.backend.localDescription")}
                    onChange={(backend) => updateForm({ backend })}
                  />
                  <BackendOption
                    id="upload-storage-oci"
                    value="oci"
                    checked={form.backend === "oci"}
                    icon={<Cloud size={20} aria-hidden />}
                    title={t("settings.uploadStorage.backend.oci")}
                    description={t("settings.uploadStorage.backend.ociDescription")}
                    onChange={(backend) => updateForm({ backend })}
                  />
                </div>
              </fieldset>

              {form.backend === "local" ? (
                <TextField
                  id="upload-storage-local-dir"
                  label={t("settings.uploadStorage.field.localStorageDir")}
                  value={form.localStorageDir}
                  onChange={(value) => updateForm({ localStorageDir: value })}
                  helper={t("settings.uploadStorage.helper.localStorageDir")}
                  placeholder={DEFAULT_LOCAL_STORAGE_DIR}
                  error={errors.localStorageDir}
                />
              ) : (
                <div className="max-w-xl">
                  <TextField
                    id="upload-storage-bucket"
                    label={t("settings.uploadStorage.field.objectStorageBucket")}
                    value={form.objectStorageBucket}
                    onChange={(value) => updateForm({ objectStorageBucket: value })}
                    helper={t("settings.uploadStorage.helper.objectStorageBucket")}
                    placeholder={DEFAULT_OBJECT_STORAGE_BUCKET}
                    error={errors.objectStorageBucket}
                  />
                  <FieldError
                    id="uploadStorage-objectStorageNamespace-error"
                    className="mt-2"
                    message={errors.objectStorageNamespace}
                  />
                </div>
              )}
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" loading={save.isPending}>
              <Save size={15} aria-hidden />
              {save.isPending
                ? t("settings.uploadStorage.actions.saving")
                : t("settings.uploadStorage.actions.save")}
            </Button>
            {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
          </div>
        </form>

        <SettingsSupplementalPanels
          status={<StatusPanel settings={settings} />}
          env={{
            description: t("settings.uploadStorage.env.description"),
            value: envPreview,
          }}
          operation={{
            description: t("settings.uploadStorage.ops.description"),
            notes: [
              t("settings.uploadStorage.ops.nonBlockingSave"),
              t("settings.uploadStorage.ops.runtime"),
              t("settings.uploadStorage.ops.local"),
              t("settings.uploadStorage.ops.oci"),
            ],
            warnings: operationWarnings,
          }}
        />
      </div>
    </div>
  );
}

function BackendOption({
  id,
  value,
  checked,
  icon,
  title,
  description,
  onChange,
}: {
  id: string;
  value: UploadStorageBackend;
  checked: boolean;
  icon: ReactNode;
  title: string;
  description: string;
  onChange: (value: UploadStorageBackend) => void;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex min-h-32 cursor-pointer items-start gap-3 rounded-md border bg-card p-4 text-left transition-colors",
        checked
          ? "border-primary bg-info-bg/40"
          : "border-border hover:border-primary/60 hover:bg-background"
      )}
    >
      <input
        id={id}
        type="radio"
        name="upload-storage-backend"
        value={value}
        checked={checked}
        onChange={() => onChange(value)}
        className="mt-1 h-4 w-4 cursor-pointer accent-[var(--primary)]"
      />
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-background text-primary">
        {icon}
      </span>
      <span>
        <span className="block text-sm font-semibold text-foreground">{title}</span>
        <span className="mt-1 block text-xs leading-relaxed text-muted">{description}</span>
      </span>
    </label>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  helper,
  placeholder,
  error,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  helper: string;
  placeholder: string;
  error?: string;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

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
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        className={cn(
          "h-11 w-full rounded-md border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
          error ? "border-danger" : "border-border"
        )}
      />
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function StatusPanel({ settings }: { settings: UploadStorageSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <ShieldCheck size={18} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.uploadStorage.status.title")}</CardTitle>
            <CardDescription>{t("settings.uploadStorage.status.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <ReadinessBadge readiness={settings.readiness} />
        <MetadataRow
          label={t("settings.uploadStorage.status.backend")}
          value={backendLabel(settings.backend)}
        />
        <MetadataRow
          label={t("settings.uploadStorage.status.source")}
          value={t("settings.uploadStorage.source.runtime")}
        />
        <MetadataRow
          label={t("settings.uploadStorage.status.maxUploadSize")}
          value={formatBytes(settings.max_upload_bytes)}
        />
        <MetadataRow
          label={t("settings.uploadStorage.status.localStorageDir")}
          value={settings.local_storage_dir}
        />
        <MetadataRow
          label={t("settings.uploadStorage.status.objectStorage")}
          value={
            settings.object_storage_namespace && settings.object_storage_bucket
              ? `${settings.object_storage_namespace}/${settings.object_storage_bucket}`
              : "—"
          }
        />
      </CardContent>
    </Card>
  );
}

function ReadinessBadge({ readiness }: { readiness: string }) {
  const ok = readiness === "ok";
  const warning = readiness === "missing" || readiness === "missing_credentials";
  const Icon = ok ? CheckCircle2 : warning ? AlertCircle : XCircle;

  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
        ok && "border-success/30 bg-success-bg/50 text-success",
        warning && "border-warning/30 bg-warning-bg/60 text-warning",
        !ok && !warning && "border-danger/30 bg-danger-bg/50 text-danger"
      )}
    >
      <Icon size={16} aria-hidden />
      <span>
        {t("settings.uploadStorage.status.readiness")}: {readinessLabel(readiness)}
      </span>
    </div>
  );
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-border pt-3 text-sm first:border-t-0 first:pt-0">
      <span className="text-muted">{label}</span>
      <span className="break-all text-right font-medium text-foreground">{value || "—"}</span>
    </div>
  );
}

function formFromSettings(settings: UploadStorageSettingsData): UploadStorageForm {
  return {
    backend: settings.backend,
    localStorageDir: settings.local_storage_dir,
    objectStorageBucket: settings.object_storage_bucket,
  };
}

function payloadFromForm(
  form: UploadStorageForm,
  objectStorageNamespace: string
): UploadStorageSettingsUpdate {
  const payload: UploadStorageSettingsUpdate = {
    backend: form.backend,
    local_storage_dir: form.localStorageDir,
    object_storage_bucket: form.objectStorageBucket,
  };
  if (form.backend === "oci") {
    payload.object_storage_namespace = objectStorageNamespace;
  }
  return payload;
}

function buildUploadStorageEnvFile(
  form: UploadStorageForm,
  objectStorageNamespace: string,
  settings: UploadStorageSettingsData
): string {
  const entries: [string, string][] = [["UPLOAD_STORAGE_BACKEND", form.backend]];
  if (form.backend === "local") {
    entries.push(["LOCAL_STORAGE_DIR", form.localStorageDir]);
  } else {
    entries.push(["OBJECT_STORAGE_REGION", settings.object_storage_region]);
    entries.push(["OBJECT_STORAGE_NAMESPACE", objectStorageNamespace]);
    entries.push(["OBJECT_STORAGE_BUCKET", form.objectStorageBucket]);
  }
  return [
    "# アップロード保存先",
    ...entries.map(([key, value]) => `${key}=${formatSettingsEnvValue(value)}`),
  ].join("\n");
}

function validateForm(
  form: UploadStorageForm,
  objectStorageNamespace: string
): FieldErrors {
  const errors: FieldErrors = {};
  if (form.backend === "local" && !form.localStorageDir.trim()) {
    errors.localStorageDir = t("settings.uploadStorage.validation.localStorageDir");
  }
  if (form.backend === "oci") {
    if (!objectStorageNamespace.trim()) {
      errors.objectStorageNamespace = t(
        "settings.uploadStorage.validation.objectStorageNamespace"
      );
    } else if (!OBJECT_STORAGE_NAME_PATTERN.test(objectStorageNamespace.trim())) {
      errors.objectStorageNamespace = t("settings.uploadStorage.validation.objectStorageName");
    }
    if (!form.objectStorageBucket.trim()) {
      errors.objectStorageBucket = t("settings.uploadStorage.validation.required");
    } else if (!OBJECT_STORAGE_NAME_PATTERN.test(form.objectStorageBucket.trim())) {
      errors.objectStorageBucket = t("settings.uploadStorage.validation.objectStorageName");
    }
  }
  return errors;
}

function resolveObjectStorageNamespace(settings: UploadStorageSettingsData): string {
  const runtimeNamespace = settings.object_storage_namespace.trim();
  if (runtimeNamespace) return runtimeNamespace;
  return readStoredOciSettingsDraft().objectStorageNamespace;
}

function backendLabel(backend: UploadStorageBackend): string {
  return backend === "oci"
    ? t("settings.uploadStorage.backend.oci")
    : t("settings.uploadStorage.backend.local");
}

function readinessLabel(readiness: string): string {
  switch (readiness) {
    case "ok":
      return t("settings.uploadStorage.readiness.ok");
    case "missing":
      return t("settings.uploadStorage.readiness.missing");
    case "missing_credentials":
      return t("settings.uploadStorage.readiness.missingCredentials");
    case "invalid":
      return t("settings.uploadStorage.readiness.invalid");
    case "wallet_not_found":
      return t("settings.uploadStorage.readiness.walletNotFound");
    case "error":
      return t("settings.uploadStorage.readiness.error");
    default:
      return readiness || t("settings.uploadStorage.readiness.unknown");
  }
}
