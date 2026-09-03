"use client";

import {
  AlertTriangle,
  Cloud,
  KeyRound,
  RefreshCw,
  Save,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "@engchina/production-ready-ui";

import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "@/components/settings/SettingsTestResultPanel";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { FormStatus } from "@/components/ui/form-status";
import { InputActionField } from "@/components/ui/input-action-field";
import { RequiredFieldsNote, RequiredIndicator } from "@/components/ui/required-field";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import {
  ApiError,
  api,
  isAbortError,
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
  normalizeOciSettingsDraft,
  readStoredOciSettingsDraft,
  validateOciSettingsDraft,
  type OciSettingsDraft,
  type OciSettingsField,
  type OciValidationCode,
  type OciValidationResult,
} from "@/lib/oci-settings";
import { useRequestScope } from "@/lib/useRequestScope";
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
  const { abortAll, run: runScopedRequest } = useRequestScope();

  useEffect(() => {
    const storedDraft = readStoredOciSettingsDraft();
    setDraft(storedDraft);

    void runScopedRequest(async (signal) => {
      const [ociResult, storageResult] = await Promise.allSettled([
        api.getOciSettings({ signal }),
        api.getUploadStorageSettings({ signal }),
      ]);
      if (signal.aborted) return;

      setDraft((current) => {
        let next = current;
        if (ociResult.status === "fulfilled" && ociResult.value) {
          next = normalizeOciSettingsDraft({
            ...next,
            ...runtimeOciSettingsToDraft(ociResult.value),
          });
        }
        if (storageResult.status === "fulfilled" && storageResult.value) {
          next = normalizeOciSettingsDraft({
            ...next,
            ...runtimeObjectStorageSettingsToDraft(storageResult.value),
          });
        }
        return next;
      });

      if (ociResult.status === "fulfilled" && ociResult.value) {
        setKeyFileExists(ociResult.value.key_file_exists);
      }
    }).catch((cause: unknown) => {
      if (isAbortError(cause)) return;
    });

    return () => {
      abortAll();
    };
  }, []);

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
    const validationErrors = validationErrorsForFields(draft, AUTH_PROFILE_FIELDS);
    if (hasValidationErrors(validationErrors)) {
      setErrors((current) => ({
        ...clearSectionErrors(current, AUTH_PROFILE_FIELDS),
        ...validationErrors,
      }));
      setAuthSaveState("error");
      setConfigTestState({ phase: "idle" });
      return;
    }

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
          ...runtimeOciSettingsToDraft(saved),
        })
      );
      setAuthSaveState("idle");
      toast.success(t("settings.oci.message.saved"));
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
          error instanceof ApiError
            ? t("settings.oci.configTest.apiError", { message: error.message })
            : t("settings.oci.configTest.error"),
      });
    }
  }

  async function saveStorageDraft() {
    const validationErrors = validationErrorsForFields(draft, OBJECT_STORAGE_FIELDS);
    if (hasValidationErrors(validationErrors)) {
      setErrors((current) => ({
        ...clearSectionErrors(current, OBJECT_STORAGE_FIELDS),
        ...validationErrors,
      }));
      setStorageSaveState("error");
      return;
    }

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
          ...runtimeObjectStorageSettingsToDraft(saved),
        })
      );
      setStorageSaveState("idle");
      toast.success(t("settings.oci.message.storageSaved"));
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
      setConfigImportState("idle");
      setAuthSaveState("idle");
      setKeyFileState("idle");
      toast.success(t("settings.oci.message.configImported"));
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
      toast.success(t("settings.oci.message.keyUploaded"));
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
      toast.success(t("settings.oci.message.namespaceFetched"));
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
      <div className="space-y-6">
        <Card className="rounded-md">
          <CardHeader className="p-6 pb-0">
            <div className="flex items-center gap-2 border-b border-border pb-5">
              <KeyRound size={18} aria-hidden />
              <CardTitle className="text-lg">{t("settings.oci.auth.cardTitle")}</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="space-y-5 p-6">
            <RequiredFieldsNote />
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
                placeholder={t("settings.oci.placeholder.region")}
                required
                requiredLabel={t("settings.oci.required")}
                buttonClassName="h-11"
              />
            </div>

            <PrivateKeyDropzoneField
              id="oci-key-file"
              label={t("settings.oci.field.keyFile")}
              value={draft.keyFile}
              error={errorText(errors.keyFile)}
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
                placeholder={t("settings.oci.placeholder.region")}
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
  if (state.phase === "idle" || state.phase === "loading") return null;

  if (state.phase === "error") {
    return (
      <SettingsTestResultPanel
        tone="danger"
        message={state.message}
        testId="settings-oci-test-result"
      />
    );
  }

  const result = state.data;
  const failed = result.status === "failed";
  const troubleshooting = [
    ...result.missing_fields.map((field) =>
      t("settings.oci.configTest.missingField", { field })
    ),
    ...result.permission_issues,
    !result.key_file_exists ? t("settings.oci.configTest.missingKey") : "",
  ].filter(Boolean);

  return (
    <SettingsTestResultPanel
      tone={failed ? "danger" : "success"}
      message={result.message}
      elapsedMs={result.elapsed_ms}
      details={toSettingsTestResultDetails({
        profile: result.profile,
        config_file_exists: result.config_file_exists,
        key_file_exists: result.key_file_exists,
        oci_directory_mode: result.oci_directory_mode,
        config_file_mode: result.config_file_mode,
        key_file_mode: result.key_file_mode,
        error_type: result.error_type,
      })}
      troubleshooting={troubleshooting}
      testId="settings-oci-test-result"
    />
  );
}

function runtimeOciSettingsToDraft(
  settings: OciSettingsData
): Pick<
  OciSettingsDraft,
  "configFile" | "configProfile" | "userOcid" | "fingerprint" | "tenancyOcid" | "keyFile" | "region"
> {
  return {
    configFile: (settings.config_file ?? "").trim() || FIXED_OCI_CONFIG_FILE,
    configProfile: (settings.profile ?? "").trim() || FIXED_OCI_CONFIG_PROFILE,
    userOcid: (settings.user ?? "").trim(),
    fingerprint: (settings.fingerprint ?? "").trim(),
    tenancyOcid: (settings.tenancy ?? "").trim(),
    keyFile: FIXED_OCI_KEY_FILE,
    region: (settings.region ?? "").trim(),
  };
}

function runtimeObjectStorageSettingsToDraft(
  settings: UploadStorageSettingsData
): Pick<OciSettingsDraft, "objectStorageRegion" | "objectStorageNamespace"> {
  return {
    objectStorageRegion: (settings.object_storage_region ?? "").trim(),
    objectStorageNamespace: (settings.object_storage_namespace ?? "").trim(),
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
  return (
    <InputActionField
      id={id}
      label={label}
      value={value}
      onChange={onChange}
      placeholder={placeholder}
      helper={helper}
      error={error}
      actionError={
        importState === "error"
          ? importError || t("settings.oci.configContent.applyError")
          : undefined
      }
      readOnly={readOnly}
      required={required}
      requiredLabel={t("settings.oci.required")}
      action={{
        label: configImportButtonLabel(importState),
        icon: <RefreshCw size={14} aria-hidden />,
        loading: importState === "loading",
        onClick: onApply,
      }}
    />
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
  const buttonLabel = namespaceFetchButtonLabel(fetchState);

  return (
    <InputActionField
      id={id}
      label={label}
      value={value}
      placeholder={placeholder}
      helper={helper}
      error={error}
      actionError={
        fetchState === "error"
          ? fetchError || t("settings.oci.actions.namespaceFetchFailed")
          : undefined
      }
      readOnly
      required={required}
      requiredLabel={t("settings.oci.required")}
      inputClassName="text-foreground"
      action={{
        label: buttonLabel,
        ariaLabel: `${label}: ${buttonLabel}`,
        icon: <RefreshCw size={14} aria-hidden />,
        loading: fetchState === "loading",
        onClick: onFetch,
      }}
    />
  );
}

function PrivateKeyDropzoneField({
  id,
  label,
  value,
  error,
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
  fileState: FeedbackState;
  fileMessage: string;
  keyFileExists: boolean | null;
  onFileChange: (file: File | undefined) => void | Promise<void>;
  required?: boolean;
}) {
  const statusId = `${id}-status`;
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
  return (
    <div id={id} className="space-y-2">
      <FileDropzone
        label={label}
        ariaLabel={t("settings.oci.keyFileInput.aria")}
        accept=".pem,.key"
        formatLabel=".PEM / .KEY"
        selectedText={
          isConfigured ? t("settings.oci.privateKey.replaceCta") : ""
        }
        hint={helper}
        errorText={
          fileState === "error"
            ? fileMessage || t("settings.oci.validation.invalidKeyFile")
            : error
        }
        required={required}
        loading={fileState === "loading"}
        loadingText={t("settings.oci.actions.uploadingKeyFile")}
        dataTestId="oci-key-file-upload"
        onFiles={([file]) => void onFileChange(file)}
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
        required={required}
        aria-required={required}
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
  return <RequiredIndicator label={t("settings.oci.required")} />;
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
  const persistedFields = fields.filter((field) => !fieldInGroup(AUTH_PROFILE_FIELDS, field));
  if (persistedFields.length === 0) {
    window.localStorage.removeItem(OCI_SETTINGS_STORAGE_KEY);
    return;
  }
  const next = normalizeOciSettingsDraft({
    ...readStoredOciSettingsDraft(),
    ...pickDraftFields(source, persistedFields),
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

function validationErrorsForFields(
  draft: OciSettingsDraft,
  fields: readonly OciSettingsField[]
): OciValidationResult {
  const allErrors = validateOciSettingsDraft(draft);
  const sectionErrors: OciValidationResult = {};
  for (const field of fields) {
    if (allErrors[field]) {
      sectionErrors[field] = allErrors[field];
    }
  }
  return sectionErrors;
}

function hasValidationErrors(errors: OciValidationResult): boolean {
  return Object.keys(errors).length > 0;
}

function sameDraft(left: OciSettingsDraft, right: OciSettingsDraft): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
