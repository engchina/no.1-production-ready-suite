"use client";

import {
  AlertCircle,
  CheckCircle2,
  CloudDownload,
  Database,
  Eye,
  EyeOff,
  KeyRound,
  PlugZap,
  Power,
  PowerOff,
  RefreshCw,
  RotateCcw,
  Save,
  Server,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Spinner, toast } from "@engchina/production-ready-ui";

import { ErrorState } from "@/components/StateViews";
import { TimedLoadingState } from "@/components/ProcessingState";
import { Button } from "@/components/ui/button";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FileDropzone } from "@/components/ui/file-dropzone";
import { FormStatus } from "@/components/ui/form-status";
import { FieldLabel, RequiredFieldsNote } from "@/components/ui/required-field";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import { StatusBadge } from "@/components/ui/status-badge";
import { SavedSecretBadge } from "@/components/settings/SavedSecretBadge";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "@/components/settings/SettingsTestResultPanel";
import {
  ApiError,
  type AdbInfoData,
  type DatabaseConnectionSecurity,
  type DatabaseConnectionTestResult,
  type DatabaseSettingsData,
  type DatabaseSettingsUpdate,
  type SelectAiCredentialRegion,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useAdbInfo,
  useCreateSelectAiCredential,
  useDatabaseSettings,
  useDownloadDatabaseWallet,
  useRevealDatabasePassword,
  useSelectAiCredential,
  useStartAdb,
  useStopAdb,
  useTestDatabaseSettings,
  useUpdateAdbSettings,
  useUpdateDatabaseSettings,
  useUploadDatabaseWallet,
} from "@/lib/queries";
import { cn } from "@/lib/utils";
import { ExecutionConfirmationField } from "@/features/nl2sql/components/DbAdminShared";

interface DatabaseSettingsForm {
  user: string;
  dsn: string;
  connectionSecurity: DatabaseConnectionSecurity;
  password: string;
  clearPassword: boolean;
}

interface DatabaseSettingsFormErrors {
  user?: string;
  dsn?: string;
  password?: string;
  wallet?: string;
}

type WalletDownloadSource = "auto" | "wallet-field" | "adb-refresh";
type AdbManagementOperation = "save" | "refresh" | "start" | "stop";

const EMPTY_FORM: DatabaseSettingsForm = {
  user: "",
  dsn: "",
  connectionSecurity: "wallet_mtls",
  password: "",
  clearPassword: false,
};

function databaseConnectionSecurityOptions() {
  return [
    {
      value: "wallet_mtls",
      label: t("settings.database.connectionSecurity.walletMtlS"),
      description: t("settings.database.connectionSecurity.walletMtlS.description"),
    },
    {
      value: "walletless_tls",
      label: t("settings.database.connectionSecurity.walletlessTls"),
      description: t("settings.database.connectionSecurity.walletlessTls.description"),
    },
  ] satisfies SelectFieldOption<DatabaseConnectionSecurity>[];
}

/** Oracle 26ai の runtime 接続設定フォーム。 */
export function DatabaseSettingsClient() {
  const query = useDatabaseSettings();
  const save = useUpdateDatabaseSettings();
  const walletUpload = useUploadDatabaseWallet();
  const walletDownload = useDownloadDatabaseWallet();
  const passwordReveal = useRevealDatabasePassword();
  const test = useTestDatabaseSettings();
  const resetTest = test.reset;

  const [form, setForm] = useState<DatabaseSettingsForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<DatabaseSettingsFormErrors>({});
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [optimisticSettings, setOptimisticSettings] = useState<DatabaseSettingsData | null>(null);
  const [walletDownloadSource, setWalletDownloadSource] =
    useState<WalletDownloadSource | null>(null);

  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const autoWalletAttemptedRef = useRef<Set<string>>(new Set());
  const resetPasswordReveal = passwordReveal.reset;

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
      setErrors({});
      setPasswordVisible(false);
      setOptimisticSettings(null);
      resetPasswordReveal();
    }
  }, [query.data, resetPasswordReveal]);

  const downloadWallet = walletDownload.mutate;
  const downloadWalletAsync = walletDownload.mutateAsync;
  const resetWalletDownload = walletDownload.reset;

  useEffect(() => {
    const settings = query.data;
    const adbOcid = settings?.adb_ocid.trim() ?? "";
    const usesWalletMtlS = settings?.connection_security !== "walletless_tls";
    if (!settings || settings.wallet_uploaded || !adbOcid) return;
    if (!usesWalletMtlS) return;
    if (autoWalletAttemptedRef.current.has(adbOcid)) return;

    autoWalletAttemptedRef.current.add(adbOcid);
    setWalletDownloadSource("auto");
    resetWalletDownload();
    downloadWallet();
  }, [downloadWallet, query.data, resetWalletDownload]);

  useEffect(() => {
    const result = walletDownload.data;
    if (!result) return;
    setForm(formFromSettings(result.settings));
    setOptimisticSettings(result.settings);
    resetTest();
    if (result.status === "downloaded") {
      toast.success(t("settings.database.wallet.autoDownload.success"));
    }
    setWalletDownloadSource(null);
  }, [resetTest, walletDownload.data]);

  function updateForm(update: Partial<DatabaseSettingsForm>) {
    if ("password" in update || "clearPassword" in update) resetPasswordReveal();
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => clearChangedErrors(current, update));
    resetTest();
  }

  function updatePasswordClear(clear: boolean) {
    if (clear) setPasswordVisible(false);
    updateForm({ clearPassword: clear, password: clear ? "" : form.password });
  }

  async function togglePasswordVisible(settings: DatabaseSettingsData) {
    if (passwordVisible) {
      setPasswordVisible(false);
      return;
    }
    if (settings.has_password && !form.password) {
      try {
        const data = await passwordReveal.mutateAsync();
        updateForm({ password: data.password });
        setPasswordVisible(true);
      } catch {
        setPasswordVisible(false);
      }
      return;
    }
    setPasswordVisible(true);
  }

  function submit(settings: DatabaseSettingsData) {
    if (!validateForm(settings, true)) return;
    save.mutate(payloadFromForm(form, settings), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimisticSettings(data);
        setErrors({});
        toast.success(t("settings.database.actions.saved"));
      },
    });
  }

  function runTest(settings: DatabaseSettingsData) {
    if (!validateForm(settings, false)) return;
    test.reset();
    test.mutate(payloadFromForm(form, settings));
  }

  function validateForm(settings: DatabaseSettingsData, requirePassword: boolean) {
    const nextErrors: DatabaseSettingsFormErrors = {};
    if (!form.user.trim()) nextErrors.user = t("settings.database.validation.required");
    if (!form.dsn.trim()) nextErrors.dsn = t("settings.database.validation.required");
    if (
      requirePassword &&
      !settings.has_password &&
      !form.password.trim() &&
      !form.clearPassword
    ) {
      nextErrors.password = t("settings.database.validation.passwordRequired");
    }
    setErrors(nextErrors);

    if (Object.keys(nextErrors).length > 0) {
      focusFirstInvalid(nextErrors, { user: userRef, password: passwordRef });
      return false;
    }
    return true;
  }

  function uploadWallet(file: File) {
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setErrors((current) => ({
        ...current,
        wallet: t("settings.database.validation.invalidWalletZip"),
      }));
      return;
    }

    setErrors((current) => ({ ...current, wallet: undefined }));
    setWalletDownloadSource(null);
    resetWalletDownload();
    resetTest();
    walletUpload.mutate(file, {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimisticSettings(data);
        setPasswordVisible(false);
        toast.success(t("settings.database.actions.walletUploaded", { fileName: file.name }));
      },
    });
  }

  async function ensureWalletFromOci() {
    setWalletDownloadSource("adb-refresh");
    resetWalletDownload();
    resetTest();
    return await downloadWalletAsync();
  }

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.database.saveError");
  const walletUploadError =
    walletUpload.error instanceof ApiError
      ? walletUpload.error.message
      : t("settings.database.walletUploadError");
  const walletDownloadError =
    walletDownload.error instanceof ApiError
      ? walletDownload.error.message
      : t("settings.database.wallet.autoDownload.error");
  const passwordRevealError =
    passwordReveal.error instanceof ApiError
      ? passwordReveal.error.message
      : t("settings.database.secrets.revealError");
  const testResult = test.data;
  const adbWalletDownloadActive = walletDownloadSource === "adb-refresh";
  const walletFieldDownloadActive =
    walletDownloadSource === "auto" || walletDownloadSource === "wallet-field";

  if (query.isPending) {
    return (
      <div className="p-8">
        <TimedLoadingState
          label={t("settings.database.loading")}
          operationKey="settings-database-load"
          placement="page"
          testId="settings-database-loading"
        >
          <Skeleton className="h-20 w-full rounded-lg" />
          <Skeleton className="h-[460px] w-full rounded-lg" />
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
              : t("settings.database.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = optimisticSettings ?? query.data;
  if (!settings) return null;

  return (
    <div className="p-8">
      <div className="space-y-6">
        <AdbManagementCard
          settings={settings}
          ensureWalletFromOci={ensureWalletFromOci}
          walletEnsureError={
            walletDownload.isError && adbWalletDownloadActive ? walletDownloadError : null
          }
          walletEnsurePending={walletDownload.isPending && adbWalletDownloadActive}
        />

        <form
          onSubmit={(event) => {
            event.preventDefault();
            submit(settings);
          }}
        >
          <Card className="rounded-md">
            <CardHeader className="p-6 pb-0">
              <div className="flex items-center gap-2 border-b border-border pb-5">
                <Database size={18} aria-hidden />
                <CardTitle className="text-lg">{t("settings.database.cardTitle")}</CardTitle>
              </div>
            </CardHeader>

            <CardContent className="space-y-5 p-6">
              <RequiredFieldsNote />
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <TextField
                  id="oracle-user"
                  label={t("settings.database.field.dbUser")}
                  required
                  value={form.user}
                  inputRef={userRef}
                  onChange={(value) => updateForm({ user: value })}
                  placeholder={t("settings.database.placeholder.dbUser")}
                  error={errors.user}
                />
                <PasswordField
                  id="oracle-password"
                  label={t("settings.database.field.dbPassword")}
                  required={!settings.has_password}
                  value={form.password}
                  visible={passwordVisible}
                  disabled={form.clearPassword}
                  inputRef={passwordRef}
                  hasSavedSecret={settings.has_password}
                  error={errors.password}
                  revealError={passwordReveal.isError ? passwordRevealError : null}
                  revealPending={passwordReveal.isPending}
                  onToggleVisible={() => void togglePasswordVisible(settings)}
                  onChange={(value) => updateForm({ password: value })}
                />
              </div>

              {settings.has_password ? (
                <SecretClearCheckbox
                  checked={form.clearPassword}
                  onChange={updatePasswordClear}
                  label={t("settings.database.secrets.clearPassword")}
                />
              ) : null}

              <SelectField<DatabaseConnectionSecurity>
                id="oracle-connection-security"
                label={t("settings.database.field.connectionSecurity")}
                value={form.connectionSecurity}
                options={databaseConnectionSecurityOptions()}
                onValueChange={(value) => updateForm({ connectionSecurity: value })}
                helper={t(`settings.database.connectionSecurity.${form.connectionSecurity}.helper`)}
                buttonClassName="h-11"
              />

              {form.connectionSecurity === "wallet_mtls" ? (
                <WalletUploadField
                  settings={settings}
                  uploadPending={walletUpload.isPending}
                  autoDownloadPending={walletDownload.isPending && walletFieldDownloadActive}
                  autoDownloadError={
                    walletDownload.isError && walletFieldDownloadActive ? walletDownloadError : null
                  }
                  canAutoDownload={Boolean(settings.adb_ocid.trim())}
                  uploadError={walletUpload.isError ? walletUploadError : null}
                  validationError={errors.wallet}
                  onUpload={uploadWallet}
                  onRetryDownload={() => {
                    setWalletDownloadSource("wallet-field");
                    resetWalletDownload();
                    downloadWallet();
                  }}
                />
              ) : (
                <FormStatus
                  tone="info"
                  className="text-xs"
                  message={t("settings.database.walletlessTls.walletSkipped")}
                />
              )}

              <WalletServiceField
                value={form.dsn}
                onChange={(value) => updateForm({ dsn: value })}
                services={settings.available_services}
                connectionSecurity={form.connectionSecurity}
                error={errors.dsn}
              />

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button type="submit" size="lg" loading={save.isPending}>
                  <Save size={16} aria-hidden />
                  {save.isPending
                    ? t("settings.database.actions.saving")
                    : t("settings.database.actions.saveDb")}
                </Button>
                <Button
                  type="button"
                  size="lg"
                  variant="secondary"
                  loading={test.isPending}
                  onClick={() => runTest(settings)}
                >
                  <PlugZap size={16} aria-hidden />
                  {test.isPending
                    ? t("settings.database.actions.testing")
                    : t("settings.database.actions.testDb")}
                </Button>
                {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
              </div>

              <DatabaseTestResultPanel result={testResult} error={test.error} />

              <p className="text-xs leading-relaxed text-muted">{t("settings.database.hint")}</p>
            </CardContent>
          </Card>
        </form>

        <SelectAiCredentialCard />
      </div>
    </div>
  );
}

const SELECT_AI_CREDENTIAL_CONFIRMATION = "ADMIN_EXECUTE";
const SELECT_AI_CREDENTIAL_REGION_OPTIONS = [
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] satisfies SelectFieldOption<SelectAiCredentialRegion>[];

const SELECT_AI_MISSING_FIELD_KEYS: Record<string, I18nKey> = {
  config_file: "settings.database.selectAiCredential.missing.configFile",
  user: "settings.database.selectAiCredential.missing.user",
  tenancy: "settings.database.selectAiCredential.missing.tenancy",
  fingerprint: "settings.database.selectAiCredential.missing.fingerprint",
  key_file: "settings.database.selectAiCredential.missing.keyFile",
  key_file_permissions: "settings.database.selectAiCredential.missing.keyPermissions",
  private_key_encrypted: "settings.database.selectAiCredential.missing.keyEncrypted",
  private_key: "settings.database.selectAiCredential.missing.keyInvalid",
};

/** DBMS_CLOUD.CREATE_CREDENTIAL を管理者の明示操作だけで実行する運用カード。 */
function SelectAiCredentialCard() {
  const status = useSelectAiCredential();
  const changeCredential = useCreateSelectAiCredential();
  const [region, setRegion] = useState<SelectAiCredentialRegion>("ap-osaka-1");
  const [confirmation, setConfirmation] = useState("");
  const data = status.data;
  const confirmed = confirmation.trim() === SELECT_AI_CREDENTIAL_CONFIRMATION;

  useEffect(() => {
    if (data?.region) setRegion(data.region);
  }, [data?.region]);

  const resetFeedback = () => {
    changeCredential.reset();
  };

  const execute = () => {
    if (!data || !confirmed || !data.oci_auth_ready) return;
    changeCredential.mutate(
      {
        region,
        confirmation: SELECT_AI_CREDENTIAL_CONFIRMATION,
        recreate: data.exists,
      },
      { onSuccess: () => setConfirmation("") }
    );
  };

  const errorMessage =
    changeCredential.error instanceof ApiError
      ? changeCredential.error.message
      : t("settings.database.selectAiCredential.error.change");
  const statusError =
    status.error instanceof ApiError
      ? status.error.message
      : t("settings.database.selectAiCredential.error.load");
  const hasStatusError = Boolean(status.error);
  const missingLabels = (data?.missing_fields ?? []).map((field) =>
    t(SELECT_AI_MISSING_FIELD_KEYS[field] ?? "settings.database.selectAiCredential.missing.unknown")
  );

  return (
    <Card
      id="select-ai-credential"
      className="min-w-0 max-w-full scroll-mt-24 rounded-md"
      aria-busy={status.isFetching || changeCredential.isPending}
      data-testid="select-ai-credential-card"
    >
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border pb-5">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <KeyRound size={18} aria-hidden />
              <CardTitle className="text-lg">
                {t("settings.database.selectAiCredential.title")}
              </CardTitle>
            </div>
            <p className="mt-2 text-sm leading-6 text-muted">
              {t("settings.database.selectAiCredential.description")}
            </p>
          </div>
          {data ? (
            <StatusBadge
              variant={data.exists ? "success" : "neutral"}
              label={t(
                data.exists
                  ? "settings.database.selectAiCredential.status.created"
                  : "settings.database.selectAiCredential.status.notCreated"
              )}
            />
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="space-y-5 p-6">
        {status.isPending ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <Skeleton className="h-20 w-full rounded-md" />
            <Skeleton className="h-20 w-full rounded-md" />
          </div>
        ) : !data ? (
          <ErrorState
            message={statusError}
            onRetry={() => void status.refetch()}
            retryLabel={t("settings.database.selectAiCredential.action.refresh")}
          />
        ) : (
          <>
            {hasStatusError ? (
              <Banner
                severity="danger"
                action={
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={status.isFetching}
                    disabled={status.isFetching}
                    onClick={() => void status.refetch()}
                  >
                    <RefreshCw size={15} aria-hidden />
                    {t("settings.database.selectAiCredential.action.refresh")}
                  </Button>
                }
              >
                {statusError}
              </Banner>
            ) : null}

            <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
              <CredentialSummary
                label={t("settings.database.selectAiCredential.field.name")}
                value={data.credential_name}
                mono
              />
              <CredentialSummary
                label={t("settings.database.selectAiCredential.field.schema")}
                value={data.schema_name || "-"}
                mono
              />
            </dl>

            <SelectField<SelectAiCredentialRegion>
              id="select-ai-credential-region"
              label={t("settings.database.selectAiCredential.field.region")}
              value={region}
              options={SELECT_AI_CREDENTIAL_REGION_OPTIONS}
              onValueChange={(value) => {
                setRegion(value);
                resetFeedback();
              }}
              helper={t("settings.database.selectAiCredential.field.regionHelper")}
              buttonClassName="h-11"
            />

            {data.oci_auth_ready ? (
              <FormStatus
                tone="success"
                message={t("settings.database.selectAiCredential.ociReady")}
              />
            ) : (
              <div className="space-y-3">
                <FormStatus
                  tone="warning"
                  message={t("settings.database.selectAiCredential.ociMissing", {
                    fields:
                      missingLabels.join("、") ||
                      t("settings.database.selectAiCredential.missing.unknown"),
                  })}
                />
                {!hasStatusError ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={status.isFetching}
                    disabled={status.isFetching}
                    onClick={() => void status.refetch()}
                  >
                    <RefreshCw size={15} aria-hidden />
                    {t("settings.database.selectAiCredential.action.refresh")}
                  </Button>
                ) : null}
              </div>
            )}

            <ExecutionConfirmationField
              value={confirmation}
              onChange={(value) => {
                setConfirmation(value);
                resetFeedback();
              }}
              confirmed={confirmed}
              placeholder={SELECT_AI_CREDENTIAL_CONFIRMATION}
              expectedLabel={SELECT_AI_CREDENTIAL_CONFIRMATION}
              helper={t(
                data.exists
                  ? "settings.database.selectAiCredential.confirmation.recreateHelper"
                  : "settings.database.selectAiCredential.confirmation.createHelper",
                { phrase: SELECT_AI_CREDENTIAL_CONFIRMATION }
              )}
              tone={data.exists ? "danger" : "neutral"}
              disabled={changeCredential.isPending || !data.oci_auth_ready}
              actions={
                <Button
                  size="sm"
                  variant={data.exists ? "danger" : "primary"}
                  className="w-full sm:w-auto"
                  loading={changeCredential.isPending}
                  disabled={!confirmed || !data.oci_auth_ready || changeCredential.isPending}
                  onClick={() => void execute()}
                >
                  {data.exists ? (
                    <RotateCcw size={15} aria-hidden />
                  ) : (
                    <KeyRound size={15} aria-hidden />
                  )}
                  {t(
                    data.exists
                      ? "settings.database.selectAiCredential.action.recreate"
                      : "settings.database.selectAiCredential.action.create"
                  )}
                </Button>
              }
            />

            {changeCredential.isError ? (
              <FormStatus tone="danger" message={errorMessage} />
            ) : null}
            {changeCredential.isSuccess ? (
              <div data-testid="select-ai-credential-success">
                <FormStatus
                  tone="success"
                  message={t("settings.database.selectAiCredential.success")}
                />
              </div>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function CredentialSummary({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0 rounded-md border border-border bg-card p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className={cn("mt-1 break-all text-sm font-semibold text-foreground", mono && "font-mono")}>
        {value}
      </dd>
    </div>
  );
}

interface AdbOperationLogEntry {
  status: AdbInfoData["status"];
  message: string;
  timestamp: string;
}

/** OCI 認証設定と揃えたリージョン候補。 */
const ADB_REGION_OPTIONS = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] satisfies SelectFieldOption<string>[];

const ADB_DEFAULT_REGION = "ap-osaka-1";

const ADB_LIFECYCLE_LABEL_KEYS: Record<string, I18nKey> = {
  AVAILABLE: "settings.adb.lifecycle.AVAILABLE",
  STARTING: "settings.adb.lifecycle.STARTING",
  STOPPING: "settings.adb.lifecycle.STOPPING",
  STOPPED: "settings.adb.lifecycle.STOPPED",
  UNAVAILABLE: "settings.adb.lifecycle.UNAVAILABLE",
  PROVISIONING: "settings.adb.lifecycle.PROVISIONING",
  TERMINATING: "settings.adb.lifecycle.TERMINATING",
  TERMINATED: "settings.adb.lifecycle.TERMINATED",
  FAILED: "settings.adb.lifecycle.FAILED",
  UPDATING: "settings.adb.lifecycle.UPDATING",
  RESTORING: "settings.adb.lifecycle.RESTORING",
  BACKUP_IN_PROGRESS: "settings.adb.lifecycle.BACKUP_IN_PROGRESS",
  MAINTENANCE_IN_PROGRESS: "settings.adb.lifecycle.MAINTENANCE_IN_PROGRESS",
  ROLE_CHANGE_IN_PROGRESS: "settings.adb.lifecycle.ROLE_CHANGE_IN_PROGRESS",
  UPGRADING: "settings.adb.lifecycle.UPGRADING",
  INACCESSIBLE: "settings.adb.lifecycle.INACCESSIBLE",
  STANDBY: "settings.adb.lifecycle.STANDBY",
};

/** Autonomous Database の情報取得・起動・停止を行う運用パネル。 */
function AdbManagementCard({
  settings,
  ensureWalletFromOci,
  walletEnsureError,
  walletEnsurePending,
}: {
  settings: DatabaseSettingsData;
  ensureWalletFromOci: () => Promise<unknown>;
  walletEnsureError: string | null;
  walletEnsurePending: boolean;
}) {
  const infoQuery = useAdbInfo();
  const saveSettings = useUpdateAdbSettings();
  const start = useStartAdb();
  const stop = useStopAdb();

  // ADB OCID は backend/.env を正本とする読み取り専用値。
  const ocid = settings.adb_ocid;
  const [region, setRegion] = useState(settings.region || ADB_DEFAULT_REGION);
  const [log, setLog] = useState<AdbOperationLogEntry[]>([]);
  const [refreshAttemptedWallet, setRefreshAttemptedWallet] = useState(false);
  const [activeOperation, setActiveOperation] = useState<AdbManagementOperation | null>(null);

  useEffect(() => {
    setRegion(settings.region || ADB_DEFAULT_REGION);
    setRefreshAttemptedWallet(false);
  }, [settings.region]);

  const info = infoQuery.data;
  const lifecycle = info?.lifecycle_state ?? null;
  const canStart = lifecycle === "STOPPED" || lifecycle === "UNAVAILABLE";
  const canStop = lifecycle === "AVAILABLE";
  const refreshWalletPending = refreshAttemptedWallet && walletEnsurePending;
  const saveSettingsFeedbackPending =
    (activeOperation === "save" || activeOperation === "refresh") && saveSettings.isPending;
  const refreshSettingsFeedbackPending =
    activeOperation === "refresh" && saveSettings.isPending;
  const saveButtonLoading =
    saveSettingsFeedbackPending ||
    ((activeOperation === "save" || activeOperation === "refresh") && refreshWalletPending);
  const refreshButtonLoading =
    activeOperation === "refresh" && (saveSettings.isPending || refreshWalletPending);
  const startButtonLoading =
    (activeOperation === "start" && (saveSettings.isPending || start.isPending)) ||
    lifecycle === "STARTING";
  const stopButtonLoading =
    (activeOperation === "stop" && (saveSettings.isPending || stop.isPending)) ||
    lifecycle === "STOPPING";
  // 遷移中は useAdbInfo が背景ポーリングするため、その isFetching で操作ボタンを
  // 無効化しない(4 秒ごとのちらつき/無効化を避ける)。明示的な操作の最中だけ busy。
  const busy =
    activeOperation !== null ||
    saveSettings.isPending ||
    start.isPending ||
    stop.isPending ||
    walletEnsurePending;

  function appendLog(result: AdbInfoData) {
    setLog((current) =>
      [
        {
          status: result.status,
          message: result.message,
          timestamp: formatDateTime(new Date().toISOString()),
        },
        ...current,
      ].slice(0, 3)
    );
  }

  async function persist(): Promise<AdbInfoData | null> {
    if (!ocid.trim()) return null;
    try {
      return await saveSettings.mutateAsync({ adb_ocid: ocid.trim(), region: region.trim() });
    } catch {
      return null;
    }
  }

  async function handleRefresh(operation: Extract<AdbManagementOperation, "save" | "refresh">) {
    setActiveOperation(operation);
    try {
      const result = await persist();
      if (!result) return;
      appendLog(result);
      setRefreshAttemptedWallet(true);
      try {
        await ensureWalletFromOci();
      } catch {
        /* Wallet 取得エラーは ADB 操作フィードバックの FormStatus が担う */
      }
    } finally {
      setActiveOperation(null);
    }
  }

  async function handleStart() {
    setActiveOperation("start");
    try {
      if (!(await persist())) return;
      const result = await start.mutateAsync();
      appendLog(result);
    } catch {
      /* mutation error surface は下部の FormStatus が担う */
    } finally {
      setActiveOperation(null);
    }
  }

  async function handleStop() {
    setActiveOperation("stop");
    try {
      if (!(await persist())) return;
      const result = await stop.mutateAsync();
      appendLog(result);
    } catch {
      /* mutation error surface は下部の FormStatus が担う */
    } finally {
      setActiveOperation(null);
    }
  }

  const actionError =
    saveSettings.error instanceof ApiError
      ? saveSettings.error.message
      : start.error instanceof ApiError
        ? start.error.message
        : stop.error instanceof ApiError
          ? stop.error.message
          : refreshAttemptedWallet && walletEnsureError
            ? walletEnsureError
            : start.isError || stop.isError || saveSettings.isError
              ? t("settings.adb.notify.actionFailed")
              : null;

  return (
    <Card id="adb-management" className="scroll-mt-4 rounded-md">
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
          <div className="flex items-center gap-2">
            <Server size={18} aria-hidden />
            <CardTitle className="text-lg">{t("settings.adb.title")}</CardTitle>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={refreshButtonLoading}
            disabled={busy}
            onClick={() => void handleRefresh("refresh")}
          >
            <RefreshCw size={15} aria-hidden />
            {refreshSettingsFeedbackPending
              ? t("settings.adb.action.refreshing")
              : t("settings.adb.action.refresh")}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 p-6">
        <p className="text-sm leading-relaxed text-muted">{t("settings.adb.description")}</p>

        <div className="space-y-4">
          <SelectField
            id="adb-region"
            label={t("settings.adb.field.region")}
            value={region}
            options={ADB_REGION_OPTIONS}
            onValueChange={setRegion}
            buttonClassName="h-11"
          />
          <div className="space-y-1.5">
            <label htmlFor="adb-ocid" className="text-sm font-medium text-foreground">
              {t("settings.adb.field.ocid")}
            </label>
            <input
              id="adb-ocid"
              type="text"
              value={ocid}
              readOnly
              aria-readonly="true"
              placeholder={t("settings.adb.placeholder.ocidEmpty")}
              className="h-11 w-full cursor-not-allowed rounded-md border border-border bg-background px-3 text-sm text-muted outline-none placeholder:text-muted/70"
            />
            <p className="text-xs leading-relaxed text-muted">
              {t("settings.adb.helper.ocidReadonly")}
            </p>
          </div>
        </div>

        <div className="space-y-2 border-t border-border pt-4">
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="lg"
              loading={saveButtonLoading}
              disabled={busy || !ocid.trim()}
              onClick={() => void handleRefresh("save")}
            >
              <Save size={16} aria-hidden />
              {saveSettingsFeedbackPending
                ? t("settings.database.actions.saving")
                : t("settings.database.actions.save")}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="secondary"
              loading={startButtonLoading}
              disabled={busy || !ocid.trim() || !canStart}
              onClick={() => void handleStart()}
            >
              <Power size={16} aria-hidden />
              {startButtonLoading
                ? t("settings.adb.action.starting")
                : t("settings.adb.action.start")}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="secondary"
              loading={stopButtonLoading}
              disabled={busy || !ocid.trim() || !canStop}
              onClick={() => void handleStop()}
            >
              <PowerOff size={16} aria-hidden />
              {stopButtonLoading
                ? t("settings.adb.action.stopping")
                : t("settings.adb.action.stop")}
            </Button>
          </div>
          {refreshWalletPending ? (
            <FormStatus
              tone="info"
              className="text-xs"
              message={t("settings.database.wallet.autoDownload.pending")}
            />
          ) : null}
          {actionError ? (
            <FormStatus tone="danger" className="text-xs" message={actionError} />
          ) : null}
        </div>

        {info && info.lifecycle_state ? <AdbInfoPanel info={info} /> : null}

        {log.length > 0 ? <AdbOperationLog entries={log} /> : null}
      </CardContent>
    </Card>
  );
}

function AdbInfoPanel({ info }: { info: AdbInfoData }) {
  const known = info.status === "success" || info.status === "accepted";
  // 自己完結したステータスバーを余分なパネルで囲まない。
  return (
    <div className="space-y-2">
      <AdbLifecycleBadge state={info.lifecycle_state} />
      {!known ? <FormStatus tone="warning" className="text-xs" message={info.message} /> : null}
    </div>
  );
}

function AdbLifecycleBadge({ state }: { state: string | null }) {
  const tone = adbLifecycleTone(state);
  const Icon = tone === "ok" ? CheckCircle2 : tone === "danger" ? XCircle : AlertCircle;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
        tone === "ok" && "border-success/30 bg-success-bg/50 text-success",
        tone === "danger" && "border-danger/30 bg-danger-bg/50 text-danger",
        tone === "warning" && "border-warning/30 bg-warning-bg/60 text-warning",
        tone === "muted" && "border-border bg-card text-muted"
      )}
    >
      <Icon size={16} aria-hidden />
      <span>
        {t("settings.adb.operational.lifecycle")}: {adbLifecycleLabel(state)}
      </span>
    </div>
  );
}

function AdbOperationLog({ entries }: { entries: AdbOperationLogEntry[] }) {
  return (
    <div className="space-y-2">
      <span className="block text-sm font-medium text-foreground">
        {t("settings.adb.operationResult.title")}
      </span>
      <ul className="space-y-1.5">
        {entries.map((entry, index) => (
          <li
            key={`${entry.timestamp}-${index}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-card px-3 py-2 text-xs"
          >
            <span className="text-muted">{entry.timestamp}</span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 font-medium",
                adbStatusBadgeClass(entry.status)
              )}
            >
              {entry.status}
            </span>
            <span className="text-foreground">{entry.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function adbLifecycleLabel(state: string | null): string {
  if (!state) return t("settings.adb.statusUnknown");
  const key = ADB_LIFECYCLE_LABEL_KEYS[state];
  return key ? t(key) : state;
}

function adbLifecycleTone(state: string | null): "ok" | "danger" | "warning" | "muted" {
  if (state === "AVAILABLE") return "ok";
  if (state === "FAILED" || state === "TERMINATED" || state === "INACCESSIBLE") return "danger";
  if (state === "STOPPED" || state === "UNAVAILABLE" || state === "STANDBY") return "muted";
  if (!state) return "muted";
  return "warning";
}

function adbStatusBadgeClass(status: AdbInfoData["status"]): string {
  switch (status) {
    case "success":
    case "accepted":
      return "bg-success-bg text-success";
    case "already_available":
    case "already_stopped":
      return "bg-info-bg text-info";
    case "error":
      return "bg-danger-bg text-danger";
    default:
      return "bg-warning-bg text-warning";
  }
}

function WalletServiceField({
  value,
  services,
  connectionSecurity,
  error,
  onChange,
}: {
  value: string;
  services: string[];
  connectionSecurity: DatabaseConnectionSecurity;
  error?: string;
  onChange: (value: string) => void;
}) {
  const usesWalletMtlS = connectionSecurity === "wallet_mtls";
  const serviceOptions = (
    usesWalletMtlS
      ? services.map((service) => ({
          value: service,
          label: service,
        }))
      : []
  ) satisfies SelectFieldOption<string>[];

  if (serviceOptions.length > 0) {
    return (
      <SelectField
        id="oracle-wallet-service"
        label={t("settings.database.field.serviceDsn")}
        value={value.trim()}
        options={serviceOptions}
        onValueChange={onChange}
        required
        requiredLabel={t("settings.database.requiredMark")}
        error={error}
        placeholder={t("settings.database.placeholder.serviceDsn")}
        helper={t("settings.database.helper.dsnService")}
        buttonClassName="h-11"
      />
    );
  }

  return (
    <TextField
      id="oracle-wallet-service"
      label={
        usesWalletMtlS
          ? t("settings.database.field.serviceDsn")
          : t("settings.database.field.directDsn")
      }
      required
      value={value}
      onChange={onChange}
      placeholder={
        usesWalletMtlS
          ? t("settings.database.placeholder.serviceDsnManual")
          : t("settings.database.placeholder.directDsn")
      }
      helper={
        usesWalletMtlS
          ? t("settings.database.helper.dsnServiceManual")
          : t("settings.database.helper.directDsn")
      }
      error={error}
    />
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  placeholder,
  helper,
  error,
  required = false,
  inputRef,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  helper?: string;
  error?: string;
  required?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const errorId = `${id}-error`;
  const helperId = `${id}-helper`;
  const describedBy = [helper ? helperId : "", error ? errorId : ""].filter(Boolean).join(" ");

  return (
    <div className="space-y-1.5">
      <RequiredLabel id={id} label={label} required={required} />
      <input
        ref={inputRef}
        id={id}
        type="text"
        value={value}
        required={required}
        aria-required={required}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy || undefined}
        className={cn(
          "h-11 w-full rounded-md border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
          error ? "border-danger" : "border-border"
        )}
      />
      {helper ? (
        <p id={helperId} className="text-xs leading-relaxed text-muted">
          {helper}
        </p>
      ) : null}
      <FieldError id={errorId} message={error} />
    </div>
  );
}

function PasswordField({
  id,
  label,
  value,
  visible,
  disabled,
  hasSavedSecret,
  required,
  error,
  revealError,
  revealPending,
  inputRef,
  onChange,
  onToggleVisible,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  disabled: boolean;
  hasSavedSecret: boolean;
  required: boolean;
  error?: string;
  revealError: string | null;
  revealPending: boolean;
  inputRef: RefObject<HTMLInputElement | null>;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const errorId = `${id}-error`;
  const revealErrorId = `${id}-reveal-error`;
  const hintId = `${id}-hint`;
  const describedBy = [hintId, error ? errorId : "", revealError ? revealErrorId : ""]
    .filter(Boolean)
    .join(" ");
  const revealButtonLabel = revealPending
    ? t("settings.database.secrets.revealingPassword")
    : visible
      ? t("settings.database.secrets.hide")
      : t("settings.database.secrets.show");

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <RequiredLabel id={id} label={label} required={required} />
        {hasSavedSecret ? (
          <SavedSecretBadge label={t("settings.database.secrets.saved")} />
        ) : null}
      </div>
      <div className="relative">
        <input
          ref={inputRef}
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          disabled={disabled}
          required={required}
          onChange={(event) => onChange(event.target.value)}
          placeholder={
            hasSavedSecret
              ? t("settings.database.placeholder.passwordSaved")
              : t("settings.database.placeholder.password")
          }
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(
            "h-11 w-full rounded-md border bg-card px-3 pr-12 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:bg-background disabled:text-muted",
            error ? "border-danger" : "border-border"
          )}
        />
        <button
          type="button"
          onClick={onToggleVisible}
          disabled={disabled || revealPending}
          aria-busy={revealPending}
          aria-label={revealButtonLabel}
          className="absolute right-0 top-0 flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-md text-muted transition-colors hover:bg-background hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {revealPending ? (
            <Spinner size={16} />
          ) : visible ? (
            <EyeOff size={16} aria-hidden />
          ) : (
            <Eye size={16} aria-hidden />
          )}
        </button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {hasSavedSecret
          ? t("settings.database.helper.passwordSavedCompact")
          : t("settings.database.helper.passwordRequired")}
      </p>
      <FieldError id={errorId} message={error} />
      {revealError ? (
        <div id={revealErrorId}>
          <FormStatus tone="danger" className="text-xs" message={revealError} />
        </div>
      ) : null}
    </div>
  );
}

function RequiredLabel({
  id,
  label,
  required,
}: {
  id: string;
  label: string;
  required?: boolean;
}) {
  return <FieldLabel htmlFor={id} label={label} required={required} />;
}

function WalletUploadField({
  settings,
  uploadPending,
  autoDownloadPending,
  autoDownloadError,
  canAutoDownload,
  uploadError,
  validationError,
  onUpload,
  onRetryDownload,
}: {
  settings: DatabaseSettingsData;
  uploadPending: boolean;
  autoDownloadPending: boolean;
  autoDownloadError: string | null;
  canAutoDownload: boolean;
  uploadError: string | null;
  validationError?: string;
  onUpload: (file: File) => void;
  onRetryDownload: () => void;
}) {
  return (
    <div className="space-y-2">
      <FileDropzone
        label={t("settings.database.wallet.title")}
        ariaLabel={t("settings.database.walletInput.aria")}
        accept=".zip,application/zip,application/x-zip-compressed,application/octet-stream"
        formatLabel=".ZIP"
        selectedText={
          settings.wallet_uploaded ? t("settings.database.wallet.replaceCta") : ""
        }
        hint={t("settings.database.wallet.help")}
        errorText={validationError}
        loading={uploadPending}
        loadingText={t("settings.database.actions.uploadingWallet")}
        disabled={autoDownloadPending}
        dataTestId="oracle-wallet-upload"
        onFiles={([file]) => onUpload(file)}
      />

      {autoDownloadPending ? (
        <FormStatus
          tone="info"
          className="text-xs"
          message={t("settings.database.wallet.autoDownload.pending")}
        />
      ) : null}
      {!settings.wallet_uploaded && !canAutoDownload ? (
        <FormStatus
          tone="info"
          className="text-xs"
          message={t("settings.database.wallet.autoDownload.missingOcid")}
        />
      ) : null}
      {autoDownloadError ? (
        <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center">
          <FormStatus tone="danger" className="min-w-0 flex-1 text-xs" message={autoDownloadError} />
          <Button
            type="button"
            size="md"
            variant="secondary"
            className="w-full shrink-0 sm:w-auto"
            aria-label={t("settings.database.wallet.autoDownload.retryAria")}
            onClick={onRetryDownload}
          >
            <CloudDownload size={16} aria-hidden />
            {t("settings.database.wallet.autoDownload.retry")}
          </Button>
        </div>
      ) : null}

      {uploadError ? <FormStatus tone="danger" className="text-xs" message={uploadError} /> : null}

      <div className="space-y-1 text-xs leading-relaxed text-muted">
        <StatusLine
          label={t("settings.database.wallet.status")}
          value={
            settings.wallet_uploaded
              ? t("settings.database.wallet.statusConfigured")
              : t("settings.database.wallet.statusNotConfigured")
          }
          ok={settings.wallet_uploaded}
        />
        <p>
          <span>{t("settings.database.wallet.location")}:</span>{" "}
          <span className="break-all text-foreground">{settings.wallet_dir || "—"}</span>
        </p>
      </div>
    </div>
  );
}

function StatusLine({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <p>
      <span>{label}:</span>{" "}
      <span className={ok ? "font-medium text-success" : "font-medium text-warning"}>
        {value}
      </span>
    </p>
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

function DatabaseTestResultPanel({
  result,
  error,
}: {
  result?: DatabaseConnectionTestResult;
  error: Error | null;
}) {
  if (!result && !error) return null;

  if (error) {
    const message =
      error instanceof ApiError
        ? t("settings.database.test.apiError", { message: error.message })
        : t("settings.database.test.apiFailed");
    return (
      <SettingsTestResultPanel
        tone="danger"
        message={message}
        testId="settings-database-test-result"
      />
    );
  }

  if (!result) return null;
  const tone =
    result.status === "success"
      ? "success"
      : result.status === "skipped"
        ? "warning"
        : "danger";

  return (
    <SettingsTestResultPanel
      tone={tone}
      message={result.message}
      elapsedMs={result.elapsed_ms}
      checkedAt={formatDateTime(result.checked_at)}
      details={toSettingsTestResultDetails(result.details)}
      troubleshooting={result.troubleshooting}
      errorType={result.error_type}
      testId="settings-database-test-result"
    />
  );
}

function formFromSettings(settings: DatabaseSettingsData): DatabaseSettingsForm {
  return {
    user: settings.user,
    dsn: settings.dsn,
    connectionSecurity: settings.connection_security ?? "wallet_mtls",
    password: "",
    clearPassword: false,
  };
}

function payloadFromForm(
  form: DatabaseSettingsForm,
  settings: DatabaseSettingsData
): DatabaseSettingsUpdate {
  const payload: DatabaseSettingsUpdate = {
    user: form.user,
    dsn: form.dsn,
    connection_security: form.connectionSecurity,
    wallet_dir: settings.wallet_dir,
  };
  if (form.clearPassword) payload.clear_password = true;
  else if (form.password !== "") payload.password = form.password;
  return payload;
}


function clearChangedErrors(
  errors: DatabaseSettingsFormErrors,
  update: Partial<DatabaseSettingsForm>
): DatabaseSettingsFormErrors {
  const next = { ...errors };
  if ("user" in update) next.user = undefined;
  if ("dsn" in update) next.dsn = undefined;
  if ("password" in update || "clearPassword" in update) next.password = undefined;
  return next;
}

function focusFirstInvalid(
  errors: DatabaseSettingsFormErrors,
  refs: {
    user: RefObject<HTMLInputElement | null>;
    password: RefObject<HTMLInputElement | null>;
  }
) {
  if (errors.user) refs.user.current?.focus();
  else if (errors.password) refs.password.current?.focus();
}
