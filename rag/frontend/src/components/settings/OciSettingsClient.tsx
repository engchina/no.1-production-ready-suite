"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Clipboard,
  Cloud,
  FileKey2,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import {
  ApiError,
  api,
  type HealthData,
  type OciConfigReadData,
  type UploadStorageSettingsData,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import {
  DEFAULT_OCI_SETTINGS,
  FIXED_OCI_CONFIG_FILE,
  FIXED_OCI_CONFIG_PROFILE,
  FIXED_OCI_KEY_FILE,
  OCI_SETTINGS_STORAGE_KEY,
  REQUIRED_OCI_SETTINGS_FIELDS,
  buildOciEnvFile,
  normalizeOciSettingsDraft,
  readStoredOciSettingsDraft,
  validateOciSettingsDraft,
  type OciSettingsDraft,
  type OciSettingsField,
  type OciValidationCode,
  type OciValidationResult,
} from "@/lib/oci-settings";
import { cn } from "@/lib/utils";

type FeedbackState = "idle" | "loading" | "success" | "error";
type ReadyState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "success"; data: HealthData }
  | { phase: "error"; message: string };

const OCI_REGION_OPTIONS = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] as const satisfies readonly SelectFieldOption<string>[];

const FIELD_LABEL_KEYS: Record<OciSettingsField, I18nKey> = {
  configFile: "settings.oci.field.configFile",
  configProfile: "settings.oci.field.configProfile",
  userOcid: "settings.oci.field.userOcid",
  fingerprint: "settings.oci.field.fingerprint",
  tenancyOcid: "settings.oci.field.tenancyOcid",
  keyFile: "settings.oci.field.keyFile",
  region: "settings.oci.field.region",
  objectStorageRegion: "settings.oci.field.objectStorageRegion",
  objectStorageNamespace: "settings.oci.field.objectStorageNamespace",
  objectStorageBucket: "settings.oci.field.objectStorageBucket",
};

const AUTH_PROFILE_FIELDS = [
  "configFile",
  "configProfile",
  "userOcid",
  "fingerprint",
  "tenancyOcid",
  "keyFile",
  "region",
] as const satisfies readonly OciSettingsField[];

const OBJECT_STORAGE_FIELDS = [
  "objectStorageRegion",
  "objectStorageNamespace",
  "objectStorageBucket",
] as const satisfies readonly OciSettingsField[];

export function OciSettingsClient() {
  const [draft, setDraft] = useState<OciSettingsDraft>(DEFAULT_OCI_SETTINGS);
  const [errors, setErrors] = useState<OciValidationResult>({});
  const [authSaveState, setAuthSaveState] = useState<FeedbackState>("idle");
  const [storageSaveState, setStorageSaveState] = useState<FeedbackState>("idle");
  const [copyState, setCopyState] = useState<FeedbackState>("idle");
  const [configImportState, setConfigImportState] = useState<FeedbackState>("idle");
  const [configImportMessage, setConfigImportMessage] = useState("");
  const [keyFileState, setKeyFileState] = useState<FeedbackState>("idle");
  const [keyFileMessage, setKeyFileMessage] = useState("");
  const [namespaceFetchState, setNamespaceFetchState] = useState<FeedbackState>("idle");
  const [namespaceFetchMessage, setNamespaceFetchMessage] = useState("");
  const [readyState, setReadyState] = useState<ReadyState>({ phase: "idle" });
  const keyFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let active = true;
    const storedDraft = readStoredOciSettingsDraft();
    setDraft(storedDraft);

    void api
      .getUploadStorageSettings()
      .then((settings) => {
        if (!active || !settings) return;
        setDraft((current) =>
          normalizeOciSettingsDraft({
            ...current,
            ...runtimeObjectStorageSettingsToDraft(settings, current),
          })
        );
      })
      .catch(() => {
        // 下書き保存画面なので、runtime 設定が読めない場合はブラウザ内の下書きを優先する。
      });

    return () => {
      active = false;
    };
  }, []);

  const liveErrors = useMemo(() => validateOciSettingsDraft(draft), [draft]);
  const envPreview = useMemo(() => buildOciEnvFile(draft), [draft]);
  const completedCount = REQUIRED_OCI_SETTINGS_FIELDS.filter((field) =>
    draft[field].trim()
  ).length;

  function updateDraft<K extends OciSettingsField>(field: K, value: OciSettingsDraft[K]) {
    if (field === "objectStorageNamespace") return;
    setDraft((current) => ({ ...current, [field]: value }));
    setErrors((current) => {
      if (!current[field]) return current;
      const next = { ...current };
      delete next[field];
      return next;
    });
    if (fieldInGroup(AUTH_PROFILE_FIELDS, field)) setAuthSaveState("idle");
    if (fieldInGroup(OBJECT_STORAGE_FIELDS, field)) setStorageSaveState("idle");
    setCopyState("idle");
    setConfigImportState("idle");
    setConfigImportMessage("");
    setKeyFileState("idle");
    setKeyFileMessage("");
    if (field === "objectStorageRegion" || field === "objectStorageNamespace") {
      setNamespaceFetchState("idle");
      setNamespaceFetchMessage("");
    }
  }

  function saveAuthDraft() {
    saveDraftFields(AUTH_PROFILE_FIELDS, setAuthSaveState);
  }

  function saveStorageDraft() {
    saveDraftFields(OBJECT_STORAGE_FIELDS, setStorageSaveState);
  }

  function saveDraftFields(
    fields: readonly OciSettingsField[],
    setFeedbackState: (state: FeedbackState) => void
  ) {
    const validation = validateOciSettingsDraft(draft);
    const sectionErrors = pickValidationErrors(validation, fields);
    setErrors((current) => mergeSectionErrors(current, sectionErrors, fields));
    if (Object.keys(sectionErrors).length > 0) {
      setFeedbackState("error");
      return;
    }

    persistDraftFields(fields, draft);
    setFeedbackState("success");
  }

  function resetAuthDraft() {
    resetDraftFields(AUTH_PROFILE_FIELDS, setAuthSaveState);
  }

  function resetStorageDraft() {
    resetDraftFields(OBJECT_STORAGE_FIELDS, setStorageSaveState);
  }

  function resetDraftFields(
    fields: readonly OciSettingsField[],
    setFeedbackState: (state: FeedbackState) => void
  ) {
    const defaultValues = pickDraftFields(DEFAULT_OCI_SETTINGS, fields);
    setDraft((current) => normalizeOciSettingsDraft({ ...current, ...defaultValues }));
    setErrors((current) => clearSectionErrors(current, fields));
    persistDraftFields(fields, DEFAULT_OCI_SETTINGS);
    setFeedbackState("idle");
    setCopyState("idle");
    setConfigImportState("idle");
    setConfigImportMessage("");
    setKeyFileState("idle");
    setKeyFileMessage("");
    setNamespaceFetchState("idle");
    setNamespaceFetchMessage("");
  }

  async function copyEnv() {
    try {
      await navigator.clipboard.writeText(envPreview);
      setCopyState("success");
    } catch {
      setCopyState("error");
    }
  }

  async function importConfigFromPath() {
    const pathAndProfileErrors: OciValidationResult = {};
    if (!draft.configFile.trim()) pathAndProfileErrors.configFile = "required";
    if (Object.keys(pathAndProfileErrors).length > 0) {
      setErrors((current) => ({ ...current, ...pathAndProfileErrors }));
      setConfigImportState("error");
      setConfigImportMessage(t("settings.oci.configContent.applyError"));
      return;
    }

    setConfigImportState("loading");
    setConfigImportMessage("");
    try {
      const imported = await api.readOciConfig({
        config_file: FIXED_OCI_CONFIG_FILE,
        profile: FIXED_OCI_CONFIG_PROFILE,
      });
      const parsed = ociConfigReadDataToDraft(imported);
      if (parsed.appliedFields.length <= 1) {
        setConfigImportState("error");
        setConfigImportMessage(t("settings.oci.configContent.applyError"));
        return;
      }

      setDraft((current) => normalizeOciSettingsDraft({ ...current, ...parsed.values }));
      setErrors((current) => {
        const next = { ...current };
        for (const field of parsed.appliedFields) {
          delete next[field];
        }
        return next;
      });
      setConfigImportState("success");
      setAuthSaveState("idle");
      setCopyState("idle");
      setKeyFileState("idle");
    } catch (error) {
      setConfigImportState("error");
      setConfigImportMessage(
        error instanceof ApiError ? error.message : t("settings.oci.configContent.applyError")
      );
    }
  }

  async function selectKeyFile(file: File | undefined) {
    if (!file) return;
    if (!/\.(pem|key)$/i.test(file.name)) {
      setKeyFileState("error");
      setKeyFileMessage(t("settings.oci.validation.invalidKeyFile"));
      return;
    }
    setKeyFileState("loading");
    setKeyFileMessage("");
    try {
      await api.uploadOciPrivateKey(file);
      updateDraft("keyFile", FIXED_OCI_KEY_FILE);
      setKeyFileState("success");
    } catch (error) {
      setKeyFileState("error");
      setKeyFileMessage(
        error instanceof ApiError ? error.message : t("settings.oci.actions.keyFileUploadFailed")
      );
    }
  }

  async function fetchObjectStorageNamespace() {
    if (!draft.objectStorageRegion.trim()) {
      setErrors((current) => ({ ...current, objectStorageRegion: "required" }));
      setNamespaceFetchState("error");
      setNamespaceFetchMessage(t("settings.oci.validation.required"));
      return;
    }

    setNamespaceFetchState("loading");
    setNamespaceFetchMessage("");
    try {
      const data = await api.readOciObjectStorageNamespace({
        config_file: FIXED_OCI_CONFIG_FILE,
        profile: FIXED_OCI_CONFIG_PROFILE,
        region: draft.objectStorageRegion,
      });
      const namespace = data.namespace.trim();
      if (!namespace) {
        setNamespaceFetchState("error");
        setNamespaceFetchMessage(t("settings.oci.actions.namespaceFetchFailed"));
        return;
      }

      setDraft((current) =>
        normalizeOciSettingsDraft({ ...current, objectStorageNamespace: namespace })
      );
      setErrors((current) => {
        const next = { ...current };
        delete next.objectStorageNamespace;
        return next;
      });
      setStorageSaveState("idle");
      setCopyState("idle");
      setNamespaceFetchState("success");
    } catch (error) {
      setNamespaceFetchState("error");
      setNamespaceFetchMessage(
        error instanceof ApiError
          ? error.message
          : t("settings.oci.actions.namespaceFetchFailed")
      );
    }
  }

  async function checkReadiness() {
    setReadyState({ phase: "loading" });
    try {
      setReadyState({ phase: "success", data: await api.getReadiness() });
    } catch (error) {
      setReadyState({
        phase: "error",
        message:
          error instanceof ApiError
            ? error.message
            : "バックエンド readiness の確認に失敗しました。",
      });
    }
  }

  return (
    <div className="p-8">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                  <FileKey2 size={20} aria-hidden />
                </div>
                <div>
                  <CardTitle>{t("settings.oci.auth.title")}</CardTitle>
                  <CardDescription>{t("settings.oci.auth.description")}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <ConfigFileField
                  id="oci-config-file"
                  label={t("settings.oci.field.configFile")}
                  value={draft.configFile}
                  error={errorText(errors.configFile)}
                  helper={t("settings.oci.helper.configFile")}
                  placeholder="~/.oci/config"
                  importState={configImportState}
                  importError={configImportMessage}
                  onApply={() => void importConfigFromPath()}
                  readOnly
                  required
                />
                <TextField
                  id="oci-config-profile"
                  label={t("settings.oci.field.configProfile")}
                  value={draft.configProfile}
                  error={errorText(errors.configProfile)}
                  helper={t("settings.oci.helper.configProfile")}
                  placeholder="DEFAULT"
                  readOnly
                  required
                />
                <TextField
                  id="oci-user-ocid"
                  label={t("settings.oci.field.userOcid")}
                  value={draft.userOcid}
                  onChange={(value) => updateDraft("userOcid", value)}
                  error={errorText(errors.userOcid)}
                  helper={t("settings.oci.helper.userOcid")}
                  placeholder="ocid1.user.oc1.."
                  required
                />
                <TextField
                  id="oci-fingerprint"
                  label={t("settings.oci.field.fingerprint")}
                  value={draft.fingerprint}
                  onChange={(value) => updateDraft("fingerprint", value)}
                  error={errorText(errors.fingerprint)}
                  helper={t("settings.oci.helper.fingerprint")}
                  placeholder="12:34:56:78:90:ab:cd:ef"
                  required
                />
                <TextField
                  id="oci-tenancy-ocid"
                  label={t("settings.oci.field.tenancyOcid")}
                  value={draft.tenancyOcid}
                  onChange={(value) => updateDraft("tenancyOcid", value)}
                  error={errorText(errors.tenancyOcid)}
                  helper={t("settings.oci.helper.tenancyOcid")}
                  placeholder="ocid1.tenancy.oc1.."
                  required
                />
                <FilePickerField
                  id="oci-key-file"
                  label={t("settings.oci.field.keyFile")}
                  value={draft.keyFile}
                  error={errorText(errors.keyFile)}
                  helper={t("settings.oci.helper.keyFile")}
                  placeholder="~/.oci/oci_api_key.pem"
                  buttonLabel={t("settings.oci.actions.selectKeyFile")}
                  selectedLabel={t("settings.oci.actions.keyFileSelected")}
                  loadingLabel={t("settings.oci.actions.uploadingKeyFile")}
                  inputRef={keyFileInputRef}
                  accept=".pem,.key"
                  fileState={keyFileState}
                  fileMessage={keyFileMessage}
                  onFileChange={selectKeyFile}
                  required
                />
                <SelectField
                  id="oci-region"
                  label={t("settings.oci.field.region")}
                  value={draft.region}
                  options={OCI_REGION_OPTIONS}
                  onValueChange={(value) => updateDraft("region", value)}
                  error={errorText(errors.region)}
                  helper={t("settings.oci.helper.region")}
                  placeholder="us-chicago-1"
                  required
                  requiredLabel={t("settings.oci.required")}
                />
              </div>

              <SectionActions
                ariaContext={t("nav.settingsOci")}
                saveState={authSaveState}
                onSave={saveAuthDraft}
                onReset={resetAuthDraft}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                  <Cloud size={20} aria-hidden />
                </div>
                <div>
                  <CardTitle>{t("settings.oci.storage.title")}</CardTitle>
                  <CardDescription>{t("settings.oci.storage.description")}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <NamespaceField
                  id="oci-object-storage-namespace"
                  label={t("settings.oci.field.objectStorageNamespace")}
                  value={draft.objectStorageNamespace}
                  error={errorText(errors.objectStorageNamespace)}
                  helper={t("settings.oci.helper.objectStorageNamespace")}
                  placeholder="mytenancynamespace"
                  fetchState={namespaceFetchState}
                  fetchError={namespaceFetchMessage}
                  onFetch={() => void fetchObjectStorageNamespace()}
                  required
                />
                <TextField
                  id="oci-object-storage-bucket"
                  label={t("settings.oci.field.objectStorageBucket")}
                  value={draft.objectStorageBucket}
                  onChange={(value) => updateDraft("objectStorageBucket", value)}
                  error={errorText(errors.objectStorageBucket)}
                  helper={t("settings.oci.helper.objectStorageBucket")}
                  placeholder="rag-originals"
                  required
                />
                <SelectField
                  id="oci-object-storage-region"
                  label={t("settings.oci.field.objectStorageRegion")}
                  value={draft.objectStorageRegion}
                  options={OCI_REGION_OPTIONS}
                  onValueChange={(value) => updateDraft("objectStorageRegion", value)}
                  error={errorText(errors.objectStorageRegion)}
                  helper={t("settings.oci.helper.objectStorageRegion")}
                  placeholder="ap-osaka-1"
                  required
                  requiredLabel={t("settings.oci.required")}
                />
              </div>

              <SectionActions
                ariaContext={t("settings.oci.storage.title")}
                saveState={storageSaveState}
                onSave={saveStorageDraft}
                onReset={resetStorageDraft}
              />
            </CardContent>
          </Card>
        </div>

        <aside className="space-y-6">
          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
                  <ShieldCheck size={20} aria-hidden />
                </div>
                <div>
                  <CardTitle>{t("settings.oci.status.title")}</CardTitle>
                  <CardDescription>{t("settings.oci.status.description")}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground">
                {t("settings.oci.status.complete", {
                  done: completedCount,
                  total: REQUIRED_OCI_SETTINGS_FIELDS.length,
                })}
              </div>
              <ul className="space-y-2">
                {REQUIRED_OCI_SETTINGS_FIELDS.map((field) => (
                  <FieldStatusRow key={field} field={field} error={liveErrors[field]} />
                ))}
              </ul>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <ActionCardHeader
                title={t("settings.oci.ready.title")}
                description={t("settings.oci.ready.description")}
                action={(
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="min-h-10 w-full shrink-0 whitespace-nowrap sm:w-auto"
                    loading={readyState.phase === "loading"}
                    onClick={() => void checkReadiness()}
                  >
                    <RefreshCw size={14} aria-hidden />
                    {t("settings.oci.actions.check")}
                  </Button>
                )}
              />
            </CardHeader>
            <CardContent>
              <ReadinessContent state={readyState} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <ActionCardHeader
                title={t("settings.oci.env.title")}
                description={t("settings.oci.env.description")}
                action={(
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    className="min-h-10 w-full shrink-0 whitespace-nowrap sm:w-auto"
                    onClick={() => void copyEnv()}
                  >
                    <Clipboard size={14} aria-hidden />
                    {copyState === "success"
                      ? t("settings.oci.actions.copied")
                      : t("settings.oci.actions.copyEnv")}
                  </Button>
                )}
              />
            </CardHeader>
            <CardContent className="space-y-3">
              <textarea
                readOnly
                value={envPreview}
                aria-label={t("settings.oci.env.title")}
                className="h-64 w-full resize-none rounded-md border border-border bg-background p-3 font-mono text-xs leading-relaxed text-foreground outline-none focus-visible:border-primary"
              />
              {copyState === "error" ? (
                <p className="text-xs text-danger" role="alert">
                  {t("settings.oci.actions.copyFailed")}
                </p>
              ) : null}
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function ActionCardHeader({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action: ReactNode;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
      <div className="min-w-0 space-y-1">
        <CardTitle>{title}</CardTitle>
        <CardDescription className="leading-relaxed">{description}</CardDescription>
      </div>
      <div className="min-w-0 sm:justify-self-end">{action}</div>
    </div>
  );
}

function SectionActions({
  ariaContext,
  saveState,
  onSave,
  onReset,
}: {
  ariaContext: string;
  saveState: FeedbackState;
  onSave: () => void;
  onReset: () => void;
}) {
  const saveLabel =
    saveState === "success" ? t("settings.oci.actions.saved") : t("settings.oci.actions.save");

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <Button
        type="button"
        className="min-h-10 whitespace-nowrap"
        aria-label={`${ariaContext}: ${saveLabel}`}
        onClick={onSave}
      >
        <Save size={15} aria-hidden />
        {saveLabel}
      </Button>
      <Button
        type="button"
        variant="secondary"
        className="min-h-10 whitespace-nowrap"
        aria-label={`${ariaContext}: ${t("settings.oci.actions.reset")}`}
        onClick={onReset}
      >
        <RotateCcw size={15} aria-hidden />
        {t("settings.oci.actions.reset")}
      </Button>
      {saveState === "error" ? (
        <p className="text-sm text-danger" role="alert">
          {t("settings.oci.status.invalid")}
        </p>
      ) : null}
    </div>
  );
}

function runtimeObjectStorageSettingsToDraft(
  settings: UploadStorageSettingsData,
  current: OciSettingsDraft
): Pick<
  OciSettingsDraft,
  "objectStorageRegion" | "objectStorageNamespace" | "objectStorageBucket"
> {
  return {
    objectStorageRegion:
      (settings.object_storage_region ?? "").trim() || current.objectStorageRegion,
    objectStorageNamespace:
      (settings.object_storage_namespace ?? "").trim() || current.objectStorageNamespace,
    objectStorageBucket:
      (settings.object_storage_bucket ?? "").trim() || current.objectStorageBucket,
  };
}

function ociConfigReadDataToDraft(data: OciConfigReadData): {
  values: Partial<OciSettingsDraft>;
  appliedFields: OciSettingsField[];
} {
  const values: Partial<OciSettingsDraft> = {
    configProfile: FIXED_OCI_CONFIG_PROFILE,
  };
  const appliedFields: OciSettingsField[] = ["configProfile"];

  addImportedValue(values, appliedFields, "userOcid", data.user);
  addImportedValue(values, appliedFields, "fingerprint", data.fingerprint);
  addImportedValue(values, appliedFields, "tenancyOcid", data.tenancy);
  addImportedValue(values, appliedFields, "region", data.region);
  values.keyFile = FIXED_OCI_KEY_FILE;
  appliedFields.push("keyFile");

  return { values, appliedFields };
}

function addImportedValue(
  values: Partial<OciSettingsDraft>,
  appliedFields: OciSettingsField[],
  field: OciSettingsField,
  value: string
) {
  const cleaned = value.trim();
  if (!cleaned) return;
  values[field] = cleaned as never;
  appliedFields.push(field);
}

function configImportButtonLabel(state: FeedbackState): string {
  if (state === "loading") return t("settings.oci.actions.applyingConfig");
  if (state === "success") return t("settings.oci.actions.applied");
  return t("settings.oci.actions.applyConfig");
}

function namespaceFetchButtonLabel(state: FeedbackState): string {
  if (state === "loading") return t("settings.oci.actions.fetchingNamespace");
  if (state === "success") return t("settings.oci.actions.namespaceFetched");
  return t("settings.oci.actions.fetchNamespace");
}

function ConfigFileField({
  id,
  label,
  value,
  onChange,
  error,
  helper,
  placeholder,
  importState,
  importError,
  onApply,
  readOnly = false,
  required,
}: {
  id: string;
  label: string;
  value: string;
  onChange?: (value: string) => void;
  error?: string;
  helper: string;
  placeholder: string;
  importState: FeedbackState;
  importError: string;
  onApply: () => void;
  readOnly?: boolean;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const importErrorId = `${id}-import-error`;
  const describedBy = [
    hintId,
    error ? errorId : "",
    importState === "error" ? importErrorId : "",
  ].filter(Boolean).join(" ");

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-foreground">
        {label}
        {required ? <RequiredBadge /> : null}
      </label>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <input
          id={id}
          type="text"
          value={value}
          readOnly={readOnly}
          aria-readonly={readOnly || undefined}
          onChange={(event) => {
            if (!readOnly) onChange?.(event.target.value);
          }}
          placeholder={placeholder}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(
            "h-10 w-full rounded-md border px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
            readOnly ? "cursor-default bg-background text-muted" : "bg-card",
            error ? "border-danger" : "border-border"
          )}
        />
        <Button
          type="button"
          variant="secondary"
          className="min-h-10 w-full whitespace-nowrap"
          loading={importState === "loading"}
          onClick={onApply}
        >
          {importState !== "loading" ? <RefreshCw size={14} aria-hidden /> : null}
          {configImportButtonLabel(importState)}
        </Button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
      {importState === "error" ? (
        <p id={importErrorId} className="text-xs text-danger" role="alert">
          {importError || t("settings.oci.configContent.applyError")}
        </p>
      ) : null}
    </div>
  );
}

function NamespaceField({
  id,
  label,
  value,
  error,
  helper,
  placeholder,
  fetchState,
  fetchError,
  onFetch,
  required,
}: {
  id: string;
  label: string;
  value: string;
  error?: string;
  helper: string;
  placeholder: string;
  fetchState: FeedbackState;
  fetchError: string;
  onFetch: () => void;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const fetchErrorId = `${id}-fetch-error`;
  const buttonLabel = namespaceFetchButtonLabel(fetchState);
  const describedBy = [
    hintId,
    error ? errorId : "",
    fetchState === "error" ? fetchErrorId : "",
  ].filter(Boolean).join(" ");

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-foreground">
        {label}
        {required ? <RequiredBadge /> : null}
      </label>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <input
          id={id}
          type="text"
          value={value}
          readOnly
          aria-readonly="true"
          placeholder={placeholder}
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(
            "h-10 w-full cursor-default rounded-md border bg-background px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
            error ? "border-danger" : "border-border"
          )}
        />
        <Button
          type="button"
          variant="secondary"
          className="min-h-10 w-full whitespace-nowrap"
          aria-label={`${label}: ${buttonLabel}`}
          loading={fetchState === "loading"}
          onClick={onFetch}
        >
          {fetchState !== "loading" ? <RefreshCw size={14} aria-hidden /> : null}
          {buttonLabel}
        </Button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
      {fetchState === "error" ? (
        <p id={fetchErrorId} className="text-xs text-danger" role="alert">
          {fetchError || t("settings.oci.actions.namespaceFetchFailed")}
        </p>
      ) : null}
    </div>
  );
}

function FilePickerField({
  id,
  label,
  value,
  error,
  helper,
  placeholder,
  buttonLabel,
  selectedLabel,
  loadingLabel,
  inputRef,
  accept,
  fileState,
  fileMessage,
  onFileChange,
  required,
}: {
  id: string;
  label: string;
  value: string;
  error?: string;
  helper: string;
  placeholder: string;
  buttonLabel: string;
  selectedLabel: string;
  loadingLabel: string;
  inputRef: RefObject<HTMLInputElement | null>;
  accept: string;
  fileState: FeedbackState;
  fileMessage: string;
  onFileChange: (file: File | undefined) => void | Promise<void>;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const fileErrorId = `${id}-file-error`;
  const describedBy = [
    hintId,
    error ? errorId : "",
    fileState === "error" ? fileErrorId : "",
  ].filter(Boolean).join(" ");

  return (
    <div className="space-y-1.5">
      <label htmlFor={`${id}-button`} className="flex items-center gap-2 text-sm font-medium text-foreground">
        {label}
        {required ? <RequiredBadge /> : null}
      </label>
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto]">
        <div
          id={id}
          role="textbox"
          aria-readonly="true"
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(
            "flex min-h-10 w-full items-center rounded-md border bg-card px-3 text-sm text-foreground",
            error ? "border-danger" : "border-border",
            !value && "text-muted/70"
          )}
        >
          <span className="min-w-0 truncate">{value || placeholder}</span>
        </div>
        <Button
          id={`${id}-button`}
          type="button"
          variant="secondary"
          aria-label={
            fileState === "loading"
              ? loadingLabel
              : fileState === "success"
                ? selectedLabel
                : buttonLabel
          }
          className="min-h-10 w-full whitespace-nowrap"
          loading={fileState === "loading"}
          onClick={() => inputRef.current?.click()}
        >
          {fileState !== "loading" ? <Upload size={14} aria-hidden /> : null}
          {fileState === "loading"
            ? loadingLabel
            : fileState === "success"
              ? selectedLabel
              : buttonLabel}
        </Button>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        className="sr-only"
        aria-label={t("settings.oci.keyFileInput.aria")}
        onChange={(event) => {
          void onFileChange(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
      {fileState === "error" ? (
        <p id={fileErrorId} className="text-xs text-danger" role="alert">
          {fileMessage || t("settings.oci.validation.invalidKeyFile")}
        </p>
      ) : null}
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  error,
  helper,
  placeholder,
  readOnly = false,
  required,
}: {
  id: string;
  label: string;
  value: string;
  onChange?: (value: string) => void;
  error?: string;
  helper: string;
  placeholder: string;
  readOnly?: boolean;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium text-foreground">
        {label}
        {required ? <RequiredBadge /> : null}
      </label>
      <input
        id={id}
        type="text"
        value={value}
        readOnly={readOnly}
        aria-readonly={readOnly || undefined}
        onChange={(event) => {
          if (!readOnly) onChange?.(event.target.value);
        }}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        className={cn(
          "h-10 w-full rounded-md border px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
          readOnly ? "cursor-default bg-background text-muted" : "bg-card",
          error ? "border-danger" : "border-border"
        )}
      />
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
      {error ? (
        <p id={errorId} className="text-xs text-danger" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function RequiredBadge() {
  return (
    <span className="rounded-full bg-warning-bg px-2 py-0.5 text-[11px] font-semibold text-warning">
      {t("settings.oci.required")}
    </span>
  );
}

function FieldStatusRow({
  field,
  error,
}: {
  field: (typeof REQUIRED_OCI_SETTINGS_FIELDS)[number];
  error?: OciValidationCode;
}) {
  const kind = error === "required" ? "warning" : error ? "danger" : "success";
  const label = error
    ? error === "required"
      ? t("settings.oci.status.missing")
      : t("settings.oci.status.invalid")
    : t("settings.oci.status.ok");

  return (
    <li className="flex items-center justify-between gap-3 text-sm">
      <span className="text-foreground">{t(FIELD_LABEL_KEYS[field])}</span>
      <StatusPill kind={kind}>{label}</StatusPill>
    </li>
  );
}

function ReadinessContent({ state }: { state: ReadyState }) {
  if (state.phase === "idle") {
    return <p className="text-sm text-muted">{t("settings.oci.ready.notChecked")}</p>;
  }

  if (state.phase === "loading") {
    return <p className="text-sm text-muted">{t("settings.oci.ready.checking")}</p>;
  }

  if (state.phase === "error") {
    return (
      <div className="space-y-2" role="alert">
        <StatusPill kind="danger">{t("settings.oci.ready.error")}</StatusPill>
        <p className="text-sm text-foreground">{state.message}</p>
      </div>
    );
  }

  const ready = state.data.status === "ok";
  const adapter = state.data.message?.replace(/^adapter=/, "") ?? "";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill kind={ready ? "success" : "warning"}>
          {ready ? t("settings.oci.ready.ok") : t("settings.oci.ready.degraded")}
        </StatusPill>
        <span className="tnum text-xs text-muted">
          {t("settings.oci.ready.version")}: {state.data.version}
        </span>
        {adapter ? (
          <span className="text-xs text-muted">
            {t("settings.oci.ready.adapter")}: {adapter}
          </span>
        ) : null}
      </div>

      {Object.entries(state.data.checks).length > 0 ? (
        <ul className="space-y-2">
          {Object.entries(state.data.checks).map(([name, value]) => (
            <li key={name} className="flex items-center justify-between gap-3 text-sm">
              <span className="font-mono text-xs text-foreground">{name}</span>
              <StatusPill kind={readinessKind(value)}>{readinessLabel(value)}</StatusPill>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function StatusPill({
  kind,
  children,
}: {
  kind: "success" | "warning" | "danger" | "neutral";
  children: ReactNode;
}) {
  const Icon =
    kind === "success" ? CheckCircle2 : kind === "danger" ? XCircle : AlertTriangle;

  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium",
        kind === "success" && "border-success/30 bg-success-bg text-success",
        kind === "warning" && "border-warning/30 bg-warning-bg text-warning",
        kind === "danger" && "border-danger/30 bg-danger-bg text-danger",
        kind === "neutral" && "border-border bg-background text-muted"
      )}
    >
      <Icon size={13} aria-hidden />
      {children}
    </span>
  );
}

function errorText(code?: OciValidationCode): string | undefined {
  if (!code) return undefined;
  return t(validationMessageKey(code));
}

function validationMessageKey(code: OciValidationCode): I18nKey {
  switch (code) {
    case "invalid_bucket":
      return "settings.oci.validation.invalidBucket";
    case "invalid_user_ocid":
      return "settings.oci.validation.invalidUserOcid";
    case "invalid_tenancy_ocid":
      return "settings.oci.validation.invalidTenancyOcid";
    case "invalid_fingerprint":
      return "settings.oci.validation.invalidFingerprint";
    case "invalid_profile":
      return "settings.oci.validation.invalidProfile";
    case "required":
      return "settings.oci.validation.required";
  }
}

function fieldInGroup(
  fields: readonly OciSettingsField[],
  field: OciSettingsField
): boolean {
  return fields.includes(field);
}

function persistDraftFields(
  fields: readonly OciSettingsField[],
  source: OciSettingsDraft
) {
  const next = normalizeOciSettingsDraft({
    ...readStoredOciSettingsDraft(),
    ...pickDraftFields(source, fields),
  });

  if (sameDraft(next, DEFAULT_OCI_SETTINGS)) {
    window.localStorage.removeItem(OCI_SETTINGS_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(OCI_SETTINGS_STORAGE_KEY, JSON.stringify(next));
}

function pickDraftFields(
  source: OciSettingsDraft,
  fields: readonly OciSettingsField[]
): Partial<OciSettingsDraft> {
  const picked: Partial<OciSettingsDraft> = {};
  for (const field of fields) {
    picked[field] = source[field] as never;
  }
  return picked;
}

function pickValidationErrors(
  validation: OciValidationResult,
  fields: readonly OciSettingsField[]
): OciValidationResult {
  const picked: OciValidationResult = {};
  for (const field of fields) {
    if (validation[field]) picked[field] = validation[field];
  }
  return picked;
}

function mergeSectionErrors(
  current: OciValidationResult,
  sectionErrors: OciValidationResult,
  fields: readonly OciSettingsField[]
): OciValidationResult {
  return { ...clearSectionErrors(current, fields), ...sectionErrors };
}

function clearSectionErrors(
  current: OciValidationResult,
  fields: readonly OciSettingsField[]
): OciValidationResult {
  const next = { ...current };
  for (const field of fields) {
    delete next[field];
  }
  return next;
}

function sameDraft(left: OciSettingsDraft, right: OciSettingsDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function readinessKind(value: string): "success" | "warning" | "danger" | "neutral" {
  if (value === "ok") return "success";
  if (value === "missing" || value === "missing_credentials") return "warning";
  if (value === "invalid" || value === "wallet_not_found" || value === "error") {
    return "danger";
  }
  return "neutral";
}

function readinessLabel(value: string): string {
  switch (value) {
    case "ok":
      return t("settings.oci.ready.value.ok");
    case "missing":
      return t("settings.oci.ready.value.missing");
    case "missing_credentials":
      return t("settings.oci.ready.value.missingCredentials");
    case "invalid":
      return t("settings.oci.ready.value.invalid");
    case "wallet_not_found":
      return t("settings.oci.ready.value.walletNotFound");
    case "error":
      return t("settings.oci.ready.value.error");
    default:
      return value;
  }
}
