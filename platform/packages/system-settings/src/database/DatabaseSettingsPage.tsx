import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ErrorState,
  FieldError,
  FormStatus,
  PageBody,
  SelectField,
  Skeleton,
  Spinner,
  TextField,
  cn,
  toast,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";
import {
  AlertCircle,
  CheckCircle2,
  CloudDownload,
  Database,
  Eye,
  EyeOff,
  PlugZap,
  Power,
  PowerOff,
  RefreshCw,
  Save,
  Server,
  XCircle,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";

import {
  useSettingsDraftGuard,
  type DraftGuardMessages,
} from "../guards/useSettingsDraftGuard";
import { FileDropzone } from "../oci/FileDropzone";
import { FieldLabel } from "../oci/required-field";
import {
  SettingsTestResultPanel,
  toSettingsTestResultDetails,
} from "../oci/SettingsTestResultPanel";
import {
  useAdbInfo,
  useDatabaseSettings,
  useDownloadDatabaseWallet,
  useRevealDatabasePassword,
  useStartAdb,
  useStopAdb,
  useTestDatabaseSettings,
  useUpdateAdbSettings,
  useUpdateDatabaseSettings,
  useUploadDatabaseWallet,
  type DatabaseChangedHandler,
} from "./hooks";
import { t, type DatabaseMessageKey } from "./messages";
import type {
  AdbInfoData,
  DatabaseConnectionSecurity,
  DatabaseConnectionTestResult,
  DatabaseSettingsApi,
  DatabaseSettingsData,
  DatabaseSettingsUpdate,
} from "./types";

interface DatabaseSettingsForm {
  user: string;
  dsn: string;
  connectionSecurity: DatabaseConnectionSecurity;
  password: string;
  clearPassword: boolean;
  walletPassword: string;
  clearWalletPassword: boolean;
}

interface DatabaseSettingsFormErrors {
  user?: string;
  dsn?: string;
  password?: string;
  walletPassword?: string;
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
  walletPassword: "",
  clearWalletPassword: false,
};

function databaseConnectionSecurityOptions() {
  return [
    {
      value: "wallet_mtls",
      label: t("settings.database.connectionSecurity.walletMtlS"),
      description: t(
        "settings.database.connectionSecurity.walletMtlS.description",
      ),
    },
    {
      value: "walletless_tls",
      label: t("settings.database.connectionSecurity.walletlessTls"),
      description: t(
        "settings.database.connectionSecurity.walletlessTls.description",
      ),
    },
  ] satisfies SelectFieldOption<DatabaseConnectionSecurity>[];
}

export interface DatabaseSettingsPageProps {
  /** 製品の API 関数（GET / PATCH /api/settings/database ほか）。 */
  api: DatabaseSettingsApi;
  /** API エラーから画面に出すメッセージを取り出す（製品の ApiError など）。undefined なら既定の文言。 */
  errorMessage?: (error: unknown) => string | undefined;
  /** Walletless TLS を選べるようにする（backend の接続処理が対応している製品だけ）。 */
  connectionSecurity?: boolean;
  /** 接続設定・Wallet・ADB の起動を変えたあとに呼ぶ（製品の DB 接続状態の query を更新する）。 */
  onDatabaseChanged?: DatabaseChangedHandler;
  /** 読み込み中の表示（NL2SQL は経過時間付きの表示を渡す）。 */
  loadingFallback?: ReactNode;
  /** 離脱確認ダイアログの文言の上書き。 */
  draftGuardMessages?: Partial<DraftGuardMessages>;
  /** 接続設定の下に置く製品固有のカード（NL2SQL の Select AI、RAG のシステムテーブル）。 */
  children?: ReactNode;
}

/**
 * データベース設定（3製品共通。NL2SQL の画面を基準に移設。#108）。PageHeader は製品の route が描く。
 *
 * - ADB の情報取得・起動・停止、Oracle 26ai の接続設定、Wallet（アップロード / OCI から自動取得）
 * - 保存中・テスト中は入力を止め、未保存のまま離れようとすると確認する
 * - 背景の再取得では、利用者が編集していない項目だけを更新する
 */
export function DatabaseSettingsPage({
  api,
  errorMessage,
  connectionSecurity: connectionSecurityEnabled = false,
  onDatabaseChanged,
  loadingFallback,
  draftGuardMessages,
  children,
}: DatabaseSettingsPageProps) {
  const query = useDatabaseSettings(api);
  const save = useUpdateDatabaseSettings(api, onDatabaseChanged);
  const walletUpload = useUploadDatabaseWallet(api);
  const walletDownload = useDownloadDatabaseWallet(api, onDatabaseChanged);
  const passwordReveal = useRevealDatabasePassword(api);
  const test = useTestDatabaseSettings(api);
  const canRevealPassword = Boolean(api.revealDatabasePassword);
  const resetTest = test.reset;

  const [form, setForm] = useState<DatabaseSettingsForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<DatabaseSettingsFormErrors>({});
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [walletPasswordVisible, setWalletPasswordVisible] = useState(false);
  const [optimisticSettings, setOptimisticSettings] =
    useState<DatabaseSettingsData | null>(null);
  const [walletDownloadSource, setWalletDownloadSource] =
    useState<WalletDownloadSource | null>(null);

  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const walletPasswordRef = useRef<HTMLInputElement>(null);
  const autoWalletAttemptedRef = useRef<Set<string>>(new Set());
  const resetPasswordReveal = passwordReveal.reset;

  const baseline = useRef(EMPTY_FORM);
  const operationBusy =
    save.isPending ||
    test.isPending ||
    walletUpload.isPending ||
    walletDownload.isPending ||
    passwordReveal.isPending;
  useSettingsDraftGuard(
    JSON.stringify(form) !== JSON.stringify(baseline.current),
    operationBusy,
    draftGuardMessages,
  );
  const acceptSettings = useCallback(
    (data: DatabaseSettingsData, saved = false) => {
      const next = formFromSettings(data);
      const previous = baseline.current;
      baseline.current = next;
      setForm((current) =>
        saved
          ? next
          : (Object.fromEntries(
              Object.keys(next).map((key) => {
                const field = key as keyof DatabaseSettingsForm;
                return [
                  field,
                  current[field] === previous[field]
                    ? next[field]
                    : current[field],
                ];
              }),
            ) as unknown as DatabaseSettingsForm),
      );
    },
    [],
  );

  useEffect(() => {
    if (query.data) {
      acceptSettings(query.data);
      setOptimisticSettings(null);
    }
  }, [acceptSettings, query.data]);

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
    acceptSettings(result.settings);
    setOptimisticSettings(result.settings);
    resetTest();
    if (result.status === "downloaded") {
      toast.success(t("settings.database.wallet.autoDownload.success"));
    }
    setWalletDownloadSource(null);
  }, [acceptSettings, resetTest, walletDownload.data]);

  function updateForm(update: Partial<DatabaseSettingsForm>) {
    if ("password" in update || "clearPassword" in update)
      resetPasswordReveal();
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => clearChangedErrors(current, update));
    resetTest();
  }

  function updatePasswordClear(clear: boolean) {
    if (clear) setPasswordVisible(false);
    updateForm({ clearPassword: clear, password: clear ? "" : form.password });
  }

  function updateWalletPasswordClear(clear: boolean) {
    if (clear) setWalletPasswordVisible(false);
    updateForm({
      clearWalletPassword: clear,
      walletPassword: clear ? "" : form.walletPassword,
    });
  }

  async function togglePasswordVisible(settings: DatabaseSettingsData) {
    if (passwordVisible) {
      setPasswordVisible(false);
      return;
    }
    if (canRevealPassword && settings.has_password && !form.password) {
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
    if (operationBusy) return;
    if (!validateForm(settings, true)) return;
    save.mutate(payloadFromForm(form, settings), {
      onSuccess: (data) => {
        acceptSettings(data, true);
        setPasswordVisible(false);
        setWalletPasswordVisible(false);
        resetPasswordReveal();
        setOptimisticSettings(data);
        setErrors({});
        toast.success(t("settings.database.actions.saved"));
      },
    });
  }

  function runTest(settings: DatabaseSettingsData) {
    if (operationBusy) return;
    if (!validateForm(settings, false)) return;
    test.reset();
    test.mutate(payloadFromForm(form, settings));
  }

  function validateForm(
    settings: DatabaseSettingsData,
    requirePassword: boolean,
  ) {
    const nextErrors: DatabaseSettingsFormErrors = {};
    if (!form.user.trim())
      nextErrors.user = t("settings.database.validation.required");
    if (!form.dsn.trim())
      nextErrors.dsn = t("settings.database.validation.required");
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
      focusFirstInvalid(nextErrors, {
        user: userRef,
        password: passwordRef,
        walletPassword: walletPasswordRef,
      });
      return false;
    }
    return true;
  }

  function uploadWallet(file: File) {
    if (operationBusy) return;
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
        acceptSettings(data);
        setOptimisticSettings(data);
        setPasswordVisible(false);
        setWalletPasswordVisible(false);
        toast.success(
          t("settings.database.actions.walletUploaded", {
            fileName: file.name,
          }),
        );
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
    errorMessage?.(save.error) ?? t("settings.database.saveError");
  const walletUploadError =
    errorMessage?.(walletUpload.error) ??
    t("settings.database.walletUploadError");
  const walletDownloadError =
    errorMessage?.(walletDownload.error) ??
    t("settings.database.wallet.autoDownload.error");
  const passwordRevealError =
    errorMessage?.(passwordReveal.error) ??
    t("settings.database.secrets.revealError");
  const testResult = test.data;
  const adbWalletDownloadActive = walletDownloadSource === "adb-refresh";
  const walletFieldDownloadActive =
    walletDownloadSource === "auto" || walletDownloadSource === "wallet-field";

  if (query.isPending) {
    return (
      <PageBody wide>
        {loadingFallback ?? (
          <div
            role="status"
            aria-busy="true"
            className="space-y-4"
            data-testid="settings-database-loading"
          >
            <p className="text-sm text-fg-muted">
              {t("settings.database.loading")}
            </p>
            <Skeleton className="h-20 w-full rounded-lg" />
            <Skeleton className="h-[460px] w-full rounded-lg" />
          </div>
        )}
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={
            errorMessage?.(query.error) ?? t("settings.database.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = optimisticSettings ?? query.data;
  if (!settings) return null;

  return (
    <PageBody wide>
      <fieldset
        disabled={operationBusy}
        aria-busy={operationBusy}
        className="min-w-0 space-y-6"
      >
        <AdbManagementCard
          api={api}
          errorMessage={errorMessage}
          onDatabaseChanged={onDatabaseChanged}
          settings={settings}
          ensureWalletFromOci={ensureWalletFromOci}
          walletEnsureError={
            walletDownload.isError && adbWalletDownloadActive
              ? walletDownloadError
              : null
          }
          walletEnsurePending={
            walletDownload.isPending && adbWalletDownloadActive
          }
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
                <Database size={20} aria-hidden />
                <CardTitle className="text-base">
                  {t("settings.database.cardTitle")}
                </CardTitle>
              </div>
            </CardHeader>

            <CardContent className="space-y-5 p-6">
              {/* 接続情報は 2 列の grid。意味のペア（ユーザー / パスワード、接続方式 / Wallet ZIP、Wallet パスワード / サービス）を
                  入力順のまま同じ行に置き、チェックボックスと案内は全幅にする。 */}
              <div className="grid grid-cols-1 gap-x-6 gap-y-4 lg:grid-cols-2">
                <TextField
                  id="oracle-user"
                  label={t("settings.database.field.dbUser")}
                  required
                  requiredLabel={t("common.required")}
                  value={form.user}
                  ref={userRef}
                  onValueChange={(value) => updateForm({ user: value })}
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
                  revealError={
                    passwordReveal.isError ? passwordRevealError : null
                  }
                  revealPending={passwordReveal.isPending}
                  onToggleVisible={() => void togglePasswordVisible(settings)}
                  onChange={(value) => updateForm({ password: value })}
                />

                {settings.has_password ? (
                  <div className="lg:col-span-full">
                    <SecretClearCheckbox
                      checked={form.clearPassword}
                      onChange={updatePasswordClear}
                      label={t("settings.database.secrets.clearPassword")}
                    />
                  </div>
                ) : null}

                {connectionSecurityEnabled ? (
                  <SelectField<DatabaseConnectionSecurity>
                    id="oracle-connection-security"
                    label={t("settings.database.field.connectionSecurity")}
                    value={form.connectionSecurity}
                    options={databaseConnectionSecurityOptions()}
                    onValueChange={(value) =>
                      updateForm({
                        connectionSecurity: value,
                        ...(value === "walletless_tls"
                          ? {
                              walletPassword: "",
                              clearWalletPassword: false,
                            }
                          : {}),
                      })
                    }
                    helper={t(
                      `settings.database.connectionSecurity.${form.connectionSecurity}.helper`,
                    )}
                    buttonClassName="h-11"
                  />
                ) : null}

                {form.connectionSecurity === "wallet_mtls" ? (
                  <>
                    <div className="min-w-0">
                      <WalletUploadField
                        disabled={operationBusy}
                        settings={settings}
                        uploadPending={walletUpload.isPending}
                        autoDownloadPending={
                          walletDownload.isPending && walletFieldDownloadActive
                        }
                        autoDownloadError={
                          walletDownload.isError && walletFieldDownloadActive
                            ? walletDownloadError
                            : null
                        }
                        canAutoDownload={Boolean(settings.adb_ocid.trim())}
                        uploadError={
                          walletUpload.isError ? walletUploadError : null
                        }
                        validationError={errors.wallet}
                        onUpload={uploadWallet}
                        onRetryDownload={() => {
                          setWalletDownloadSource("wallet-field");
                          resetWalletDownload();
                          downloadWallet();
                        }}
                      />
                    </div>

                    <PasswordField
                      id="oracle-wallet-password"
                      label={t("settings.database.field.walletPassword")}
                      required={false}
                      value={form.walletPassword}
                      visible={walletPasswordVisible}
                      disabled={form.clearWalletPassword}
                      inputRef={walletPasswordRef}
                      hasSavedSecret={settings.has_wallet_password}
                      error={errors.walletPassword}
                      helper={
                        settings.has_wallet_password
                          ? t("settings.database.helper.walletPasswordSaved")
                          : t("settings.database.helper.walletPasswordEmpty")
                      }
                      placeholder={t("settings.database.placeholder.secret")}
                      revealError={null}
                      revealPending={false}
                      revealButtonLabels={{
                        show: t("settings.database.secrets.showWalletPassword"),
                        hide: t("settings.database.secrets.hideWalletPassword"),
                        revealing: t(
                          "settings.database.secrets.revealingWalletPassword",
                        ),
                      }}
                      onToggleVisible={() =>
                        setWalletPasswordVisible((current) => !current)
                      }
                      onChange={(value) =>
                        updateForm({ walletPassword: value })
                      }
                    />

                    <WalletServiceField
                      value={form.dsn}
                      onChange={(value) => updateForm({ dsn: value })}
                      services={settings.available_services}
                      connectionSecurity={form.connectionSecurity}
                      error={errors.dsn}
                    />

                    {settings.has_wallet_password ? (
                      <div className="lg:col-span-full">
                        <SecretClearCheckbox
                          checked={form.clearWalletPassword}
                          onChange={updateWalletPasswordClear}
                          label={t(
                            "settings.database.secrets.clearWalletPassword",
                          )}
                        />
                      </div>
                    ) : null}
                  </>
                ) : (
                  <>
                    <WalletServiceField
                      value={form.dsn}
                      onChange={(value) => updateForm({ dsn: value })}
                      services={settings.available_services}
                      connectionSecurity={form.connectionSecurity}
                      error={errors.dsn}
                    />
                    <FormStatus
                      tone="info"
                      className="text-xs lg:col-span-full"
                      message={t(
                        "settings.database.walletlessTls.walletSkipped",
                      )}
                    />
                  </>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
                <Button
                  type="submit"
                  size="lg"
                  loading={save.isPending}
                  icon={Save}
                >
                  {t("settings.database.actions.saveDb")}
                </Button>
                <Button
                  type="button"
                  size="lg"
                  variant="secondary"
                  loading={test.isPending}
                  onClick={() => runTest(settings)}
                  icon={PlugZap}
                >
                  {t("settings.database.actions.testDb")}
                </Button>
                {save.isError ? (
                  <FormStatus tone="danger" message={saveError} />
                ) : null}
              </div>

              <DatabaseTestResultPanel
                result={testResult}
                error={test.error}
                errorMessage={errorMessage}
              />

              <p className="text-xs leading-relaxed text-fg-muted">
                {t("settings.database.hint")}
              </p>
            </CardContent>
          </Card>
        </form>

        {children}
      </fieldset>
    </PageBody>
  );
}

interface AdbOperationLogEntry {
  status: AdbInfoData["status"];
  message: string;
  timestamp: string;
}

/** OCI 認証設定と揃えたリージョン候補（NL2SQL と同じ）。 */
const ADB_REGION_OPTIONS = [
  { value: "ap-tokyo-1", label: "ap-tokyo-1" },
  { value: "ap-osaka-1", label: "ap-osaka-1" },
  { value: "us-chicago-1", label: "us-chicago-1" },
] satisfies SelectFieldOption<string>[];

const ADB_DEFAULT_REGION = "ap-osaka-1";

const ADB_LIFECYCLE_LABEL_KEYS: Record<string, DatabaseMessageKey> = {
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
  api,
  errorMessage,
  onDatabaseChanged,
  settings,
  ensureWalletFromOci,
  walletEnsureError,
  walletEnsurePending,
}: {
  api: DatabaseSettingsApi;
  errorMessage?: (error: unknown) => string | undefined;
  onDatabaseChanged?: DatabaseChangedHandler;
  settings: DatabaseSettingsData;
  ensureWalletFromOci: () => Promise<unknown>;
  walletEnsureError: string | null;
  walletEnsurePending: boolean;
}) {
  const infoQuery = useAdbInfo(api);
  const saveSettings = useUpdateAdbSettings(api);
  const start = useStartAdb(api, onDatabaseChanged);
  const stop = useStopAdb(api);

  // ADB OCID は platform/.env を正本とする読み取り専用値。
  const ocid = settings.adb_ocid;
  const [region, setRegion] = useState(settings.region || ADB_DEFAULT_REGION);
  const [log, setLog] = useState<AdbOperationLogEntry[]>([]);
  const [refreshAttemptedWallet, setRefreshAttemptedWallet] = useState(false);
  const [activeOperation, setActiveOperation] =
    useState<AdbManagementOperation | null>(null);

  useEffect(() => {
    setRegion(settings.region || ADB_DEFAULT_REGION);
    setRefreshAttemptedWallet(false);
  }, [settings.region]);

  const info = infoQuery.data;
  const lifecycle = info?.lifecycle_state ?? null;
  const canStart = lifecycle === "STOPPED" || lifecycle === "UNAVAILABLE";
  const canStop = lifecycle === "AVAILABLE";
  const showInfoPanel = Boolean(
    info && (info.lifecycle_state || info.status !== "success"),
  );
  const refreshWalletPending = refreshAttemptedWallet && walletEnsurePending;
  const saveSettingsFeedbackPending =
    (activeOperation === "save" || activeOperation === "refresh") &&
    saveSettings.isPending;
  const saveButtonLoading =
    saveSettingsFeedbackPending ||
    ((activeOperation === "save" || activeOperation === "refresh") &&
      refreshWalletPending);
  const refreshButtonLoading =
    activeOperation === "refresh" &&
    (saveSettings.isPending || refreshWalletPending);
  const startButtonLoading =
    (activeOperation === "start" &&
      (saveSettings.isPending || start.isPending)) ||
    lifecycle === "STARTING";
  const stopButtonLoading =
    (activeOperation === "stop" &&
      (saveSettings.isPending || stop.isPending)) ||
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
      ].slice(0, 3),
    );
  }

  async function persist(): Promise<AdbInfoData | null> {
    if (!ocid.trim()) return null;
    try {
      return await saveSettings.mutateAsync({
        adb_ocid: ocid.trim(),
        region: region.trim(),
      });
    } catch {
      return null;
    }
  }

  async function handleRefresh(
    operation: Extract<AdbManagementOperation, "save" | "refresh">,
  ) {
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

  const apiMessage = (error: unknown) =>
    error ? errorMessage?.(error) : undefined;
  const actionError =
    apiMessage(saveSettings.error) ??
    apiMessage(start.error) ??
    apiMessage(stop.error) ??
    (refreshAttemptedWallet && walletEnsureError
      ? walletEnsureError
      : start.isError || stop.isError || saveSettings.isError
        ? t("settings.adb.notify.actionFailed")
        : null);
  const infoError =
    apiMessage(infoQuery.error) ??
    (infoQuery.isError ? t("settings.adb.notify.infoFailed") : null);

  return (
    <Card id="adb-management" className="scroll-mt-4 rounded-md">
      <CardHeader className="p-6 pb-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-5">
          <div className="flex items-center gap-2">
            <Server size={20} aria-hidden />
            <CardTitle className="text-base">
              {t("settings.adb.title")}
            </CardTitle>
          </div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={refreshButtonLoading}
            disabled={busy}
            onClick={() => void handleRefresh("refresh")}
            icon={RefreshCw}
          >
            {t("settings.adb.action.refresh")}
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 p-6">
        <p className="text-sm leading-relaxed text-fg-muted">
          {t("settings.adb.description")}
        </p>

        {/* リージョン（短い値）と OCID（長い値）を 1:2 で同じ行に置く。 */}
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:items-start">
          <SelectField
            id="adb-region"
            label={t("settings.adb.field.region")}
            value={region}
            options={ADB_REGION_OPTIONS}
            onValueChange={setRegion}
            buttonClassName="h-11"
          />
          <div className="space-y-1.5">
            <label htmlFor="adb-ocid" className="text-sm font-medium text-fg">
              {t("settings.adb.field.ocid")}
            </label>
            <input
              id="adb-ocid"
              type="text"
              value={ocid}
              readOnly
              aria-readonly="true"
              placeholder={t("settings.adb.placeholder.ocidEmpty")}
              className="h-11 w-full cursor-not-allowed rounded-md border border-border-control bg-surface-sunken px-3 text-sm text-fg-muted outline-none placeholder:text-fg-muted"
            />
            <p className="text-xs leading-relaxed text-fg-muted">
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
              icon={Save}
            >
              {t("settings.database.actions.save")}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="secondary"
              loading={startButtonLoading}
              disabled={busy || !ocid.trim() || !canStart}
              onClick={() => void handleStart()}
              icon={Power}
            >
              {t("settings.adb.action.start")}
            </Button>
            <Button
              type="button"
              size="lg"
              variant="secondary"
              loading={stopButtonLoading}
              disabled={busy || !ocid.trim() || !canStop}
              onClick={() => void handleStop()}
              icon={PowerOff}
            >
              {t("settings.adb.action.stop")}
            </Button>
          </div>
          {actionError ? (
            <FormStatus
              tone="danger"
              className="text-xs"
              message={actionError}
            />
          ) : null}
        </div>

        {infoError ? (
          <FormStatus tone="danger" className="text-xs" message={infoError} />
        ) : null}

        {showInfoPanel && info ? <AdbInfoPanel info={info} /> : null}

        {log.length > 0 ? <AdbOperationLog entries={log} /> : null}
      </CardContent>
    </Card>
  );
}

function AdbInfoPanel({ info }: { info: AdbInfoData }) {
  const known = info.status === "success" || info.status === "accepted";
  const messageTone = info.status === "error" ? "danger" : "warning";
  // 自己完結したステータスバーを余分なパネルで囲まない。
  return (
    <div className="space-y-2">
      {info.lifecycle_state ? (
        <AdbLifecycleBadge state={info.lifecycle_state} />
      ) : null}
      {!known ? (
        <FormStatus
          tone={messageTone}
          className="text-xs"
          message={info.message}
        />
      ) : null}
    </div>
  );
}

function AdbLifecycleBadge({ state }: { state: string | null }) {
  const tone = adbLifecycleTone(state);
  const Icon =
    tone === "ok" ? CheckCircle2 : tone === "danger" ? XCircle : AlertCircle;
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-3 py-2 text-sm font-medium",
        tone === "ok" &&
          "border-success-border bg-success-subtle text-success-fg",
        tone === "danger" &&
          "border-danger-border bg-danger-subtle text-danger-fg",
        tone === "warning" &&
          "border-warning-border bg-warning-subtle text-warning-fg",
        tone === "muted" && "border-border bg-surface text-fg-muted",
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
      <span className="block text-sm font-medium text-fg">
        {t("settings.adb.operationResult.title")}
      </span>
      <ul className="space-y-1.5">
        {entries.map((entry, index) => (
          <li
            key={`${entry.timestamp}-${index}`}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface px-3 py-2 text-xs"
          >
            <span className="text-fg-muted">{entry.timestamp}</span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 font-medium",
                adbStatusBadgeClass(entry.status),
              )}
            >
              {entry.status}
            </span>
            <span className="text-fg">{entry.message}</span>
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

function adbLifecycleTone(
  state: string | null,
): "ok" | "danger" | "warning" | "muted" {
  if (state === "AVAILABLE") return "ok";
  if (state === "FAILED" || state === "TERMINATED" || state === "INACCESSIBLE")
    return "danger";
  if (state === "STOPPED" || state === "UNAVAILABLE" || state === "STANDBY")
    return "muted";
  if (!state) return "muted";
  return "warning";
}

function adbStatusBadgeClass(status: AdbInfoData["status"]): string {
  switch (status) {
    case "success":
    case "accepted":
      return "bg-success-subtle text-success-fg";
    case "already_available":
    case "already_stopped":
      return "bg-info-subtle text-info-fg";
    case "error":
      return "bg-danger-subtle text-danger-fg";
    default:
      return "bg-warning-subtle text-warning-fg";
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
      requiredLabel={t("common.required")}
      value={value}
      onValueChange={onChange}
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

function PasswordField({
  id,
  label,
  value,
  visible,
  disabled,
  hasSavedSecret,
  required,
  error,
  helper,
  placeholder,
  revealError,
  revealPending,
  revealButtonLabels,
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
  helper?: string;
  placeholder?: string;
  revealError: string | null;
  revealPending: boolean;
  revealButtonLabels?: {
    show: string;
    hide: string;
    revealing: string;
  };
  inputRef: RefObject<HTMLInputElement | null>;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const errorId = `${id}-error`;
  const revealErrorId = `${id}-reveal-error`;
  const hintId = `${id}-hint`;
  const describedBy = [
    hintId,
    error ? errorId : "",
    revealError ? revealErrorId : "",
  ]
    .filter(Boolean)
    .join(" ");
  const revealButtonLabel = revealPending
    ? (revealButtonLabels?.revealing ??
      t("settings.database.secrets.revealingPassword"))
    : visible
      ? (revealButtonLabels?.hide ?? t("settings.database.secrets.hide"))
      : (revealButtonLabels?.show ?? t("settings.database.secrets.show"));
  const helperText =
    helper ??
    (hasSavedSecret
      ? t("settings.database.helper.passwordSavedCompact")
      : t("settings.database.helper.passwordRequired"));

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
          aria-required={required}
          onChange={(event) => onChange(event.target.value)}
          placeholder={
            hasSavedSecret
              ? t("settings.database.placeholder.passwordSaved")
              : (placeholder ?? t("settings.database.placeholder.password"))
          }
          aria-invalid={Boolean(error)}
          aria-describedby={describedBy}
          className={cn(
            "h-11 w-full rounded-md border bg-surface px-3 pr-12 text-sm text-fg outline-none transition-colors placeholder:text-fg-muted focus-visible:border-focus-ring focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-focus-ring disabled:cursor-not-allowed disabled:bg-surface-disabled disabled:text-fg-disabled",
            error ? "border-danger-fg" : "border-border-control",
          )}
        />
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          touchTarget
          type="button"
          onClick={onToggleVisible}
          disabled={disabled || revealPending}
          aria-busy={revealPending}
          aria-label={revealButtonLabel}
          className="absolute right-0 top-0 rounded-l-none"
        >
          {revealPending ? (
            <Spinner size={16} />
          ) : visible ? (
            <EyeOff size={16} aria-hidden />
          ) : (
            <Eye size={16} aria-hidden />
          )}
        </Button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-fg-muted">
        {helperText}
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
  disabled,
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
  disabled?: boolean;
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
          settings.wallet_uploaded
            ? t("settings.database.wallet.replaceCta")
            : ""
        }
        hint={t("settings.database.wallet.help")}
        errorText={validationError}
        loading={uploadPending}
        loadingText={t("settings.database.actions.uploadingWallet")}
        disabled={disabled || autoDownloadPending}
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
          <FormStatus
            tone="danger"
            className="min-w-0 flex-1 text-xs"
            message={autoDownloadError}
          />
          <Button
            type="button"
            size="md"
            variant="secondary"
            className="w-full shrink-0 sm:w-auto"
            aria-label={t("settings.database.wallet.autoDownload.retryAria")}
            onClick={onRetryDownload}
            icon={CloudDownload}
          >
            {t("settings.database.wallet.autoDownload.retry")}
          </Button>
        </div>
      ) : null}

      {uploadError ? (
        <FormStatus tone="danger" className="text-xs" message={uploadError} />
      ) : null}

      <div className="space-y-1 text-xs leading-relaxed text-fg-muted">
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
          <span className="break-all text-fg">
            {settings.wallet_dir || "—"}
          </span>
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
      <span
        className={
          ok ? "font-medium text-success-fg" : "font-medium text-warning-fg"
        }
      >
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

function DatabaseTestResultPanel({
  result,
  error,
  errorMessage,
}: {
  result?: DatabaseConnectionTestResult;
  error: Error | null;
  errorMessage?: (error: unknown) => string | undefined;
}) {
  if (!result && !error) return null;

  if (error) {
    const apiMessage = errorMessage?.(error);
    const message =
      apiMessage !== undefined
        ? t("settings.database.test.apiError", { message: apiMessage })
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
  const tone = result.status === "success" ? "success" : "danger";

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

function formFromSettings(
  settings: DatabaseSettingsData,
): DatabaseSettingsForm {
  return {
    user: settings.user,
    dsn: settings.dsn,
    connectionSecurity: settings.connection_security ?? "wallet_mtls",
    password: "",
    clearPassword: false,
    walletPassword: "",
    clearWalletPassword: false,
  };
}

function payloadFromForm(
  form: DatabaseSettingsForm,
  settings: DatabaseSettingsData,
): DatabaseSettingsUpdate {
  const payload: DatabaseSettingsUpdate = {
    user: form.user,
    dsn: form.dsn,
    connection_security: form.connectionSecurity,
    wallet_dir: settings.wallet_dir,
  };
  if (form.clearPassword) payload.clear_password = true;
  else if (form.password !== "") payload.password = form.password;
  if (form.connectionSecurity === "wallet_mtls") {
    if (form.clearWalletPassword) payload.clear_wallet_password = true;
    else if (form.walletPassword !== "")
      payload.wallet_password = form.walletPassword;
  }
  return payload;
}

function clearChangedErrors(
  errors: DatabaseSettingsFormErrors,
  update: Partial<DatabaseSettingsForm>,
): DatabaseSettingsFormErrors {
  const next = { ...errors };
  if ("user" in update) next.user = undefined;
  if ("dsn" in update) next.dsn = undefined;
  if ("password" in update || "clearPassword" in update)
    next.password = undefined;
  if ("walletPassword" in update || "clearWalletPassword" in update) {
    next.walletPassword = undefined;
  }
  return next;
}

function focusFirstInvalid(
  errors: DatabaseSettingsFormErrors,
  refs: {
    user: RefObject<HTMLInputElement | null>;
    password: RefObject<HTMLInputElement | null>;
    walletPassword: RefObject<HTMLInputElement | null>;
  },
) {
  if (errors.user) refs.user.current?.focus();
  else if (errors.password) refs.password.current?.focus();
  else if (errors.walletPassword) refs.walletPassword.current?.focus();
}

/** 保存済み secret の表示（NL2SQL の SavedSecretBadge。モデル設定と同じ見た目）。 */
function SavedSecretBadge({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center justify-center whitespace-nowrap rounded-full border border-success-border bg-success-subtle px-2 py-0.5 text-xs font-medium text-success-fg">
      {label}
    </span>
  );
}

const dateTimeFormat = new Intl.DateTimeFormat("ja-JP", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

/** ISO 文字列を「MM/DD HH:mm」へ。未設定・無効値はダッシュ（NL2SQL の formatDateTime）。 */
function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return dateTimeFormat.format(date);
}
