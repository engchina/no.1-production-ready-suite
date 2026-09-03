"use client";

import {
  Cloud,
  HardDrive,
  Save,
  Settings2,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "@engchina/production-ready-ui";

import { ErrorState } from "@/components/StateViews";
import { TimedLoadingState } from "@/components/ProcessingState";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { FieldLabel, RequiredFieldsNote } from "@/components/ui/required-field";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type UploadStorageBackend,
  type UploadStorageSettingsData,
  type UploadStorageSettingsUpdate,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useUpdateUploadStorageSettings, useUploadStorageSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { cn } from "@/lib/utils";

interface UploadStorageForm {
  backend: UploadStorageBackend;
  localStorageDir: string;
  objectStorageRegion: string;
  objectStorageNamespace: string;
  objectStorageBucket: string;
}

type FieldErrors = Partial<Record<keyof UploadStorageForm, string>>;

const EMPTY_FORM: UploadStorageForm = {
  backend: "local",
  localStorageDir: "",
  objectStorageRegion: "",
  objectStorageNamespace: "",
  objectStorageBucket: "",
};

const DEFAULT_LOCAL_STORAGE_DIR = "/u01/data/production-ready-nl2sql";
const DEFAULT_OBJECT_STORAGE_BUCKET = "nl2sql-originals";
const OBJECT_STORAGE_NAME_PATTERN = /^[A-Za-z0-9._-]+$/;
const OCI_REGION_OPTIONS = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] as const satisfies readonly SelectFieldOption<string>[];

/** ドキュメントアップロード原本の保存先設定。 */
export function UploadStorageSettingsClient() {
  const query = useUploadStorageSettings();
  const save = useUpdateUploadStorageSettings();
  const navigate = useNavigate();
  const [form, setForm] = useState<UploadStorageForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
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
    const validationErrors = validateUploadStorageForm(form);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      save.reset();
      return;
    }

    setErrors({});
    save.mutate(payloadFromForm(form), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setErrors({});
        toast.success(t("settings.uploadStorage.actions.saved"));
      },
    });
  }

  if (query.isPending) {
    return (
      <div className="p-8">
        <TimedLoadingState
          label={t("settings.uploadStorage.loading")}
          operationKey="settings-upload-storage-load"
          placement="page"
          testId="settings-upload-storage-loading"
        >
          <Skeleton className="h-64 w-full rounded-lg" />
          <Skeleton className="h-72 w-full rounded-lg" />
        </TimedLoadingState>
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
  const ociSettingsMissing =
    form.backend === "oci" &&
    (!form.objectStorageRegion.trim() || !form.objectStorageNamespace.trim());

  return (
    <div className="space-y-5 p-8">
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
            <RequiredFieldsNote />
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
                required
              />
            ) : (
              <div className="space-y-4">
                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                  <SelectField
                    id="upload-storage-object-storage-region"
                    label={t("settings.uploadStorage.field.objectStorageRegion")}
                    value={form.objectStorageRegion}
                    options={OCI_REGION_OPTIONS}
                    onValueChange={(value) => updateForm({ objectStorageRegion: value })}
                    helper={t("settings.uploadStorage.helper.objectStorageRegion")}
                    placeholder={t("settings.oci.placeholder.region")}
                    error={errors.objectStorageRegion}
                    required
                    requiredLabel={t("settings.oci.required")}
                    buttonClassName="h-11"
                  />
                  <TextField
                    id="upload-storage-object-storage-namespace"
                    label={t("settings.uploadStorage.field.objectStorageNamespace")}
                    value={form.objectStorageNamespace}
                    onChange={() => undefined}
                    helper={t("settings.uploadStorage.helper.objectStorageNamespace")}
                    placeholder="mytenancynamespace"
                    error={errors.objectStorageNamespace}
                    readOnly
                    required
                  />
                </div>
                <TextField
                  id="upload-storage-bucket"
                  label={t("settings.uploadStorage.field.objectStorageBucket")}
                  value={form.objectStorageBucket}
                  onChange={(value) => updateForm({ objectStorageBucket: value })}
                  helper={t("settings.uploadStorage.helper.objectStorageBucket")}
                  placeholder={DEFAULT_OBJECT_STORAGE_BUCKET}
                  error={errors.objectStorageBucket}
                  required
                />
                {ociSettingsMissing ? (
                  <div className="flex flex-wrap items-center gap-3 rounded-md border border-warning/30 bg-warning-bg p-3">
                    <FormStatus
                      tone="warning"
                      message={t("settings.uploadStorage.status.ociSettingsIncomplete")}
                    />
                    <Button
                      type="button"
                      variant="secondary"
                      size="lg"
                      className="min-h-[44px]"
                      onClick={() => navigate(APP_ROUTES.settingsOci)}
                    >
                      <Settings2 size={15} aria-hidden />
                      {t("settings.uploadStorage.actions.openOciSettings")}
                    </Button>
                  </div>
                ) : null}
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
  readOnly = false,
  required = false,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  helper: string;
  placeholder: string;
  error?: string;
  readOnly?: boolean;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  return (
    <div className="space-y-1.5">
      <FieldLabel htmlFor={id} label={label} required={required} />
      <input
        id={id}
        type="text"
        value={value}
        readOnly={readOnly}
        aria-readonly={readOnly || undefined}
        required={required}
        aria-required={required}
        onChange={(event) => {
          if (!readOnly) onChange(event.target.value);
        }}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        className={cn(
          "h-11 w-full rounded-md border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
          readOnly && "cursor-default bg-background text-muted",
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

function formFromSettings(settings: UploadStorageSettingsData): UploadStorageForm {
  return {
    backend: settings.backend,
    localStorageDir: settings.local_storage_dir,
    objectStorageRegion: settings.object_storage_region,
    objectStorageNamespace: settings.object_storage_namespace,
    objectStorageBucket: settings.object_storage_bucket,
  };
}

function payloadFromForm(form: UploadStorageForm): UploadStorageSettingsUpdate {
  const payload: UploadStorageSettingsUpdate = {
    backend: form.backend,
    local_storage_dir: form.localStorageDir,
    object_storage_bucket: form.objectStorageBucket,
  };
  if (form.backend === "oci") {
    payload.object_storage_region = form.objectStorageRegion;
    payload.object_storage_namespace = form.objectStorageNamespace;
  }
  return payload;
}

function validateUploadStorageForm(form: UploadStorageForm): FieldErrors {
  const errors: FieldErrors = {};
  if (form.backend === "local") {
    if (!form.localStorageDir.trim()) {
      errors.localStorageDir = t("settings.uploadStorage.validation.localStorageDir");
    }
    return errors;
  }

  const region = form.objectStorageRegion.trim();
  const namespace = form.objectStorageNamespace.trim();
  const bucket = form.objectStorageBucket.trim();
  if (!region) {
    errors.objectStorageRegion = t("settings.uploadStorage.validation.objectStorageRegion");
  }
  if (!namespace) {
    errors.objectStorageNamespace = t(
      "settings.uploadStorage.validation.objectStorageNamespace"
    );
  } else if (!OBJECT_STORAGE_NAME_PATTERN.test(namespace)) {
    errors.objectStorageNamespace = t("settings.uploadStorage.validation.objectStorageName");
  }
  if (!bucket) {
    errors.objectStorageBucket = t("settings.uploadStorage.validation.required");
  } else if (!OBJECT_STORAGE_NAME_PATTERN.test(bucket)) {
    errors.objectStorageBucket = t("settings.uploadStorage.validation.objectStorageName");
  }
  return errors;
}
