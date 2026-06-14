"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  KeyRound,
  RefreshCw,
  Save,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import {
  SETTINGS_DETAIL_GRID_CLASS,
  SettingsSupplementalPanels,
} from "@/components/settings/SettingsPreviewPanels";
import {
  ApiError,
  api,
  type OciConfigReadData,
  type OciConfigTestResult,
  type OciSettingsData,
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
type ConfigTestState =
  | { phase: "idle" }
  | { phase: "loading" }
  | { phase: "success"; data: OciConfigTestResult }
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
] as const satisfies readonly OciSettingsField[];

export function OciSettingsClient() {
  const [draft, setDraft] = useState<OciSettingsDraft>(DEFAULT_OCI_SETTINGS);
  const [errors, setErrors] = useState<OciValidationResult>({});
  const [authSaveState, setAuthSaveState] = useState<FeedbackState>("idle");
  const [storageSaveState, setStorageSaveState] = useState<FeedbackState>("idle");
  const [configImportState, setConfigImportState] = useState<FeedbackState>("idle");
  const [configImportMessage, setConfigImportMessage] = useState("");
  const [keyFileState, setKeyFileState] = useState<FeedbackState>("idle");
  const [keyFileMessage, setKeyFileMessage] = useState("");
  const [keyFileExists, setKeyFileExists] = useState<boolean | null>(null);
  const [namespaceFetchState, setNamespaceFetchState] = useState<FeedbackState>("idle");
  const [namespaceFetchMessage, setNamespaceFetchMessage] = useState("");
  const [configTestState, setConfigTestState] = useState<ConfigTestState>({ phase: "idle" });
  const keyFileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let active = true;
    const storedDraft = readStoredOciSettingsDraft();
    setDraft(storedDraft);

    void Promise.allSettled([api.getOciSettings(), api.getUploadStorageSettings()]).then(
      ([ociResult, storageResult]) => {
        if (!active) return;

        setDraft((current) => {
          let next = current;
          if (ociResult.status === "fulfilled" && ociResult.value) {
            next = normalizeOciSettingsDraft({
              ...next,
              ...runtimeOciSettingsToDraft(ociResult.value, next),
            });
          }
          if (storageResult.status === "fulfilled" && storageResult.value) {
            next = normalizeOciSettingsDraft({
              ...next,
              ...runtimeObjectStorageSettingsToDraft(storageResult.value, next),
            });
          }
          return next;
        });

        if (ociResult.status === "fulfilled" && ociResult.value) {
          setKeyFileExists(ociResult.value.key_file_exists);
        }
      }
    );

    return () => {
      active = false;
    };
  }, []);

  const liveErrors = useMemo(() => validateOciSettingsDraft(draft), [draft]);
  const operationWarnings = useMemo(() => ociValidationMessages(liveErrors), [liveErrors]);
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
    if (fieldInGroup(AUTH_PROFILE_FIELDS, field)) setConfigTestState({ phase: "idle" });
    if (fieldInGroup(OBJECT_STORAGE_FIELDS, field)) setStorageSaveState("idle");
    setConfigImportState("idle");
    setConfigImportMessage("");
    setKeyFileState("idle");
    setKeyFileMessage("");
    if (field === "objectStorageRegion" || field === "objectStorageNamespace") {
      setNamespaceFetchState("idle");
      setNamespaceFetchMessage("");
    }
  }

  async function saveAuthDraft() {
    setErrors((current) => clearSectionErrors(current, AUTH_PROFILE_FIELDS));
    setAuthSaveState("loading");
    try {
      persistDraftFields(AUTH_PROFILE_FIELDS, draft);
      const saved = await api.updateOciSettings({
        user: draft.userOcid,
        fingerprint: draft.fingerprint,
        tenancy: draft.tenancyOcid,
        region: draft.region,
      });
      setKeyFileExists(saved.key_file_exists);
      setDraft((current) =>
        normalizeOciSettingsDraft({
          ...current,
          ...runtimeOciSettingsToDraft(saved, current),
        })
      );
      setAuthSaveState("success");
    } catch {
      setAuthSaveState("error");
      setConfigTestState({ phase: "idle" });
    }
  }

  async function testAuthConfig() {
    setErrors((current) => clearSectionErrors(current, AUTH_PROFILE_FIELDS));
    setConfigTestState({ phase: "loading" });
    try {
      setConfigTestState({ phase: "success", data: await api.testOciConfig() });
    } catch (error) {
      setConfigTestState({
        phase: "error",
        message:
          error instanceof ApiError ? error.message : t("settings.oci.configTest.error"),
      });
    }
  }

  async function saveStorageDraft() {
    setErrors((current) => clearSectionErrors(current, OBJECT_STORAGE_FIELDS));
    setStorageSaveState("loading");
    try {
      const saved = await api.updateOciObjectStorageSettings({
        object_storage_region: draft.objectStorageRegion,
        object_storage_namespace: draft.objectStorageNamespace,
      });
      persistDraftFields(OBJECT_STORAGE_FIELDS, draft);
      setDraft((current) =>
        normalizeOciSettingsDraft({
          ...current,
          ...runtimeObjectStorageSettingsToDraft(saved, current),
        })
      );
      setStorageSaveState("success");
    } catch {
      setStorageSaveState("error");
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
      setKeyFileExists(true);
      setKeyFileState("success");
      setConfigTestState({ phase: "idle" });
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

  return (
    <div className="p-8">
      <div className={SETTINGS_DETAIL_GRID_CLASS}>
        <div className="space-y-6">
          <Card className="rounded-md">
            <CardHeader className="p-6 pb-0">
              <div className="flex items-center gap-2 border-b border-border pb-5">
                <KeyRound size={18} aria-hidden />
                <CardTitle className="text-lg">{t("settings.oci.auth.cardTitle")}</CardTitle>
              </div>
            </CardHeader>
            <CardContent className="space-y-5 p-6">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
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
                  id="oci-tenancy-ocid"
                  label={t("settings.oci.field.tenancyOcid")}
                  value={draft.tenancyOcid}
                  onChange={(value) => updateDraft("tenancyOcid", value)}
                  error={errorText(errors.tenancyOcid)}
                  helper={t("settings.oci.helper.tenancyOcid")}
                  placeholder="ocid1.tenancy.oc1.."
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
                  buttonClassName="h-11"
                />
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
              </div>

              <PrivateKeyDropzoneField
                id="oci-key-file"
                label={t("settings.oci.field.keyFile")}
                value={draft.keyFile}
                error={errorText(errors.keyFile)}
                inputRef={keyFileInputRef}
                fileState={keyFileState}
                fileMessage={keyFileMessage}
                keyFileExists={keyFileExists}
                onFileChange={selectKeyFile}
                required
              />

              <SectionActions
                ariaContext={t("nav.settingsOci")}
                saveState={authSaveState}
                saveLabel={t("settings.oci.actions.saveAuth")}
                savingLabel={t("settings.oci.actions.saving")}
                onSave={() => void saveAuthDraft()}
                testState={configTestState.phase}
                testLabel={t("settings.oci.actions.test")}
                testingLabel={t("settings.oci.actions.testing")}
                onTest={() => void testAuthConfig()}
              />
              <ConfigTestContent state={configTestState} />
              <p className="text-xs leading-relaxed text-muted">{t("settings.oci.hint")}</p>
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
                saveLabel={t("settings.oci.actions.save")}
                savingLabel={t("settings.oci.actions.saving")}
                onSave={saveStorageDraft}
              />
            </CardContent>
          </Card>
        </div>

        <SettingsSupplementalPanels
          status={(
            <Card>
              <CardHeader>
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                    <ShieldCheck size={18} aria-hidden />
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
          )}
          env={{
            description: t("settings.oci.env.description"),
            value: envPreview,
          }}
          operation={{
            description: t("settings.oci.ops.description"),
            notes: [
              t("settings.oci.ops.nonBlockingSave"),
              t("settings.oci.ops.config"),
              t("settings.oci.ops.key"),
              t("settings.oci.ops.storage"),
            ],
            warnings: operationWarnings,
          }}
        />
      </div>
    </div>
  );
}

function SectionActions({
  ariaContext,
  saveState,
  saveLabel: idleSaveLabel,
  savingLabel,
  onSave,
  testState,
  testLabel,
  testingLabel,
  onTest,
}: {
  ariaContext: string;
  saveState: FeedbackState;
  saveLabel: string;
  savingLabel: string;
  onSave: () => void;
  testState?: ConfigTestState["phase"];
  testLabel?: string;
  testingLabel?: string;
  onTest?: () => void;
}) {
  const currentSaveLabel =
    saveState === "loading"
      ? savingLabel
      : saveState === "success"
        ? t("settings.oci.actions.saved")
        : idleSaveLabel;
  const currentTestLabel =
    testState === "loading" && testingLabel ? testingLabel : testLabel;
  const isTesting = testState === "loading";

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <Button
        type="button"
        size="lg"
        className="whitespace-nowrap"
        aria-label={`${ariaContext}: ${currentSaveLabel}`}
        loading={saveState === "loading"}
        disabled={isTesting}
        onClick={onSave}
      >
        {saveState !== "loading" ? <Save size={15} aria-hidden /> : null}
        {currentSaveLabel}
      </Button>
      {onTest && currentTestLabel ? (
        <Button
          type="button"
          variant="secondary"
          size="lg"
          className="whitespace-nowrap"
          aria-label={`${ariaContext}: ${currentTestLabel}`}
          loading={isTesting}
          disabled={saveState === "loading"}
          onClick={onTest}
        >
          {!isTesting ? <ShieldCheck size={15} aria-hidden /> : null}
          {currentTestLabel}
        </Button>
      ) : null}
      {saveState === "error" ? (
        <FormStatus tone="danger" message={t("settings.oci.status.invalid")} />
      ) : null}
    </div>
  );
}

function ConfigTestContent({ state }: { state: ConfigTestState }) {
  if (state.phase === "idle") return null;

  if (state.phase === "loading") {
    return (
      <div
        className="rounded-md border border-border bg-background px-3 py-2 text-sm text-muted"
        role="status"
      >
        {t("settings.oci.configTest.checking")}
      </div>
    );
  }

  if (state.phase === "error") {
    return (
      <div
        className="space-y-2 rounded-md border border-danger/30 bg-danger-bg px-3 py-3"
        role="alert"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground">
            {t("settings.oci.configTest.title")}
          </span>
          <StatusPill kind="danger">{t("settings.oci.configTest.failed")}</StatusPill>
        </div>
        <p className="text-sm text-foreground">{state.message}</p>
      </div>
    );
  }

  const result = state.data;
  const failed = result.status === "failed";
  const detailItems = [
    ...result.missing_fields.map((field) =>
      t("settings.oci.configTest.missingField", { field })
    ),
    ...result.permission_issues,
    !result.key_file_exists ? t("settings.oci.configTest.missingKey") : "",
  ].filter(Boolean);

  return (
    <div
      className={cn(
        "space-y-2 rounded-md border px-3 py-3",
        failed
          ? "border-warning/40 bg-warning-bg"
          : "border-success/30 bg-success-bg"
      )}
      role={failed ? "alert" : "status"}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">
          {t("settings.oci.configTest.title")}
        </span>
        <StatusPill kind={failed ? "warning" : "success"}>
          {failed ? t("settings.oci.configTest.failed") : t("settings.oci.configTest.success")}
        </StatusPill>
      </div>
      <p className="text-sm text-foreground">{result.message}</p>
      {detailItems.length > 0 ? (
        <ul className="space-y-1 text-xs leading-relaxed text-foreground">
          {detailItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
        {result.oci_directory_mode ? (
          <span className="tnum">.oci {result.oci_directory_mode}</span>
        ) : null}
        {result.config_file_mode ? (
          <span className="tnum">config {result.config_file_mode}</span>
        ) : null}
        {result.key_file_mode ? (
          <span className="tnum">key {result.key_file_mode}</span>
        ) : null}
      </div>
    </div>
  );
}

function runtimeOciSettingsToDraft(
  settings: OciSettingsData,
  current: OciSettingsDraft
): Pick<
  OciSettingsDraft,
  "configFile" | "configProfile" | "userOcid" | "fingerprint" | "tenancyOcid" | "keyFile" | "region"
> {
  return {
    configFile: (settings.config_file ?? "").trim() || current.configFile,
    configProfile: (settings.profile ?? "").trim() || current.configProfile,
    userOcid: (settings.user ?? "").trim() || current.userOcid,
    fingerprint: (settings.fingerprint ?? "").trim() || current.fingerprint,
    tenancyOcid: (settings.tenancy ?? "").trim() || current.tenancyOcid,
    keyFile: FIXED_OCI_KEY_FILE,
    region: (settings.region ?? "").trim() || current.region,
  };
}

function runtimeObjectStorageSettingsToDraft(
  settings: UploadStorageSettingsData,
  current: OciSettingsDraft
): Pick<OciSettingsDraft, "objectStorageRegion" | "objectStorageNamespace"> {
  return {
    objectStorageRegion:
      (settings.object_storage_region ?? "").trim() || current.objectStorageRegion,
    objectStorageNamespace:
      (settings.object_storage_namespace ?? "").trim() || current.objectStorageNamespace,
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
            "h-11 w-full rounded-md border px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
            readOnly ? "cursor-default bg-background text-muted" : "bg-card",
            error ? "border-danger" : "border-border"
          )}
        />
        <Button
          type="button"
          variant="secondary"
          size="lg"
          className="h-11 w-full whitespace-nowrap"
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
      <FieldError id={errorId} message={error} />
      {importState === "error" ? (
        <FieldError
          id={importErrorId}
          message={importError || t("settings.oci.configContent.applyError")}
        />
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
            "h-11 w-full cursor-default rounded-md border bg-background px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
            error ? "border-danger" : "border-border"
          )}
        />
        <Button
          type="button"
          variant="secondary"
          size="lg"
          className="h-11 w-full whitespace-nowrap"
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
      <FieldError id={errorId} message={error} />
      {fetchState === "error" ? (
        <FieldError
          id={fetchErrorId}
          message={fetchError || t("settings.oci.actions.namespaceFetchFailed")}
        />
      ) : null}
    </div>
  );
}

function PrivateKeyDropzoneField({
  id,
  label,
  value,
  error,
  inputRef,
  fileState,
  fileMessage,
  keyFileExists,
  onFileChange,
  required,
}: {
  id: string;
  label: string;
  value: string;
  error?: string;
  inputRef: RefObject<HTMLInputElement | null>;
  fileState: FeedbackState;
  fileMessage: string;
  keyFileExists: boolean | null;
  onFileChange: (file: File | undefined) => void | Promise<void>;
  required?: boolean;
}) {
  const hintId = `${id}-hint`;
  const statusId = `${id}-status`;
  const errorId = `${id}-error`;
  const fileErrorId = `${id}-file-error`;
  const warningId = `${id}-warning`;
  const isConfigured = keyFileExists === true || fileState === "success";
  const warning =
    keyFileExists === false && fileState !== "success" ? t("settings.oci.keyFile.missing") : "";
  const statusMessage =
    fileState === "success"
      ? t("settings.oci.privateKey.loaded")
      : keyFileExists === true
        ? t("settings.oci.privateKey.configuredOnServer")
        : "";
  const helper = isConfigured
    ? t("settings.oci.privateKey.helpConfigured")
    : t("settings.oci.privateKey.helpUpload");
  const describedBy = [
    hintId,
    statusMessage ? statusId : "",
    error ? errorId : "",
    fileState === "error" ? fileErrorId : "",
    warning ? warningId : "",
  ].filter(Boolean).join(" ");

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (fileState === "loading") return;
    void onFileChange(event.dataTransfer.files?.[0]);
  }

  return (
    <div id={id} className="space-y-2">
      <label
        htmlFor={`${id}-button`}
        className="flex items-center gap-1 text-sm font-medium text-foreground"
      >
        {label}
        {required ? <RequiredBadge /> : null}
      </label>
      <button
        id={`${id}-button`}
        type="button"
        className={cn(
          "flex min-h-32 w-full cursor-pointer flex-col items-center justify-center gap-2 rounded-md border border-dashed bg-background px-4 py-7 text-center transition-colors hover:border-primary/60 hover:bg-primary/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60",
          error || fileState === "error" ? "border-danger" : "border-border"
        )}
        aria-invalid={Boolean(error) || fileState === "error"}
        aria-describedby={describedBy}
        aria-busy={fileState === "loading"}
        disabled={fileState === "loading"}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <Upload size={22} className="text-muted" aria-hidden />
        <span className="text-sm font-semibold text-foreground">
          {fileState === "loading"
            ? t("settings.oci.actions.uploadingKeyFile")
            : t("settings.oci.privateKey.uploadCta")}
        </span>
        <span id={hintId} className="max-w-2xl text-sm leading-relaxed text-foreground">
          {helper}
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pem,.key"
        className="sr-only"
        aria-label={t("settings.oci.keyFileInput.aria")}
        onChange={(event) => {
          void onFileChange(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
      {statusMessage ? (
        <div id={statusId}>
          <FormStatus
            tone="success"
            message={statusMessage}
            className="text-xs"
          />
        </div>
      ) : null}
      {value ? (
        <p className="break-all text-xs leading-relaxed text-muted">
          {t("settings.oci.privateKey.path", { path: value })}
        </p>
      ) : null}
      {warning ? (
        <p
          id={warningId}
          className="flex items-start gap-1.5 text-xs leading-relaxed text-warning"
          role="status"
        >
          <AlertTriangle size={13} className="mt-0.5 shrink-0" aria-hidden />
          <span>{warning}</span>
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
      {fileState === "error" ? (
        <FieldError
          id={fileErrorId}
          message={fileMessage || t("settings.oci.validation.invalidKeyFile")}
        />
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
          "h-11 w-full rounded-md border px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary",
          readOnly ? "cursor-default bg-background text-muted" : "bg-card",
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

function RequiredBadge() {
  return (
    <>
      <span aria-hidden className="text-danger">
        *
      </span>
      <span className="sr-only">{t("settings.oci.required")}</span>
    </>
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

function ociValidationMessages(validation: OciValidationResult): string[] {
  return REQUIRED_OCI_SETTINGS_FIELDS.flatMap((field) => {
    const code = validation[field];
    if (!code) return [];
    return [`${t(FIELD_LABEL_KEYS[field])}: ${t(validationMessageKey(code))}`];
  });
}

function sameDraft(left: OciSettingsDraft, right: OciSettingsDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
