"use client";

import {
  AlertCircle,
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  PlugZap,
  Power,
  PowerOff,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState, type DragEvent, type RefObject } from "react";

import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { FieldError } from "@/components/ui/field-error";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import {
  SETTINGS_DETAIL_GRID_CLASS,
  SettingsSupplementalPanels,
  formatSettingsEnvValue,
} from "@/components/settings/SettingsPreviewPanels";
import {
  ApiError,
  type AdbInfoData,
  type DatabaseConnectionTestResult,
  type DatabaseSettingsData,
  type DatabaseSettingsUpdate,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useAdbInfo,
  useDatabaseSettings,
  useStartAdb,
  useStopAdb,
  useTestDatabaseSettings,
  useUpdateAdbSettings,
  useUpdateDatabaseSettings,
  useUploadDatabaseWallet,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

interface DatabaseSettingsForm {
  user: string;
  dsn: string;
  password: string;
  clearPassword: boolean;
}

interface DatabaseSettingsFormErrors {
  user?: string;
  dsn?: string;
  password?: string;
  wallet?: string;
}

const EMPTY_FORM: DatabaseSettingsForm = {
  user: "",
  dsn: "",
  password: "",
  clearPassword: false,
};

/** Oracle 26ai の runtime 接続設定フォーム。 */
export function DatabaseSettingsClient() {
  const query = useDatabaseSettings();
  const save = useUpdateDatabaseSettings();
  const walletUpload = useUploadDatabaseWallet();
  const test = useTestDatabaseSettings();
  const resetTest = test.reset;

  const [form, setForm] = useState<DatabaseSettingsForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<DatabaseSettingsFormErrors>({});
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [saved, setSaved] = useState(false);
  const [uploadedWalletFileName, setUploadedWalletFileName] = useState<string | null>(null);
  const [optimisticSettings, setOptimisticSettings] = useState<DatabaseSettingsData | null>(null);

  const userRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const walletInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
      setErrors({});
      setSaved(false);
      setOptimisticSettings(null);
    }
  }, [query.data]);

  function updateForm(update: Partial<DatabaseSettingsForm>) {
    setForm((current) => ({ ...current, ...update }));
    setErrors((current) => clearChangedErrors(current, update));
    setSaved(false);
    resetTest();
  }

  function updatePasswordClear(clear: boolean) {
    updateForm({ clearPassword: clear, password: clear ? "" : form.password });
  }

  function submit(settings: DatabaseSettingsData) {
    if (!validateForm(settings, true)) return;
    save.mutate(payloadFromForm(form, settings), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimisticSettings(data);
        setErrors({});
        setSaved(true);
      },
    });
  }

  function runTest(settings: DatabaseSettingsData) {
    if (!validateForm(settings, false)) return;
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

    setSaved(false);
    setUploadedWalletFileName(null);
    setErrors((current) => ({ ...current, wallet: undefined }));
    resetTest();
    walletUpload.mutate(file, {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimisticSettings(data);
        setUploadedWalletFileName(file.name);
        setSaved(true);
      },
    });
  }

  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.database.saveError");
  const walletUploadError =
    walletUpload.error instanceof ApiError
      ? walletUpload.error.message
      : t("settings.database.walletUploadError");
  const testResult = test.data;

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-20 w-full rounded-lg" />
        <Skeleton className="h-[460px] w-full rounded-lg" />
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
  const envPreview = buildDatabaseEnvFile(form, settings);

  return (
    <div className="p-8">
      <div className={SETTINGS_DETAIL_GRID_CLASS}>
        <div className="space-y-6">
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
                  onToggleVisible={() => setPasswordVisible((current) => !current)}
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

              <WalletServiceField
                value={form.dsn}
                onChange={(value) => updateForm({ dsn: value })}
                services={settings.available_services}
                error={errors.dsn}
              />

              <WalletUploadField
                inputRef={walletInputRef}
                settings={settings}
                uploadPending={walletUpload.isPending}
                uploadedFileName={uploadedWalletFileName}
                uploadError={walletUpload.isError ? walletUploadError : null}
                validationError={errors.wallet}
                onUpload={uploadWallet}
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
                {saved ? (
                  <FormStatus tone="success" message={t("settings.database.actions.saved")} />
                ) : null}
                {save.isError ? <FormStatus tone="danger" message={saveError} /> : null}
              </div>

              {testResult ? <ConnectionTestResultPanel result={testResult} /> : null}

              <p className="text-xs leading-relaxed text-muted">{t("settings.database.hint")}</p>
            </CardContent>
          </Card>
        </form>

          <AdbManagementCard settings={settings} />
        </div>

        <SettingsSupplementalPanels
          status={<StatusPanel settings={settings} />}
          env={{
            description: t("settings.database.env.description"),
            value: envPreview,
          }}
          operation={{
            description: t("settings.database.ops.description"),
            notes: [
              t("settings.database.ops.nonBlockingSave"),
              t("settings.database.ops.env"),
              t("settings.database.ops.vector"),
              t("settings.database.ops.pool"),
            ],
          }}
        />
      </div>
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
function AdbManagementCard({ settings }: { settings: DatabaseSettingsData }) {
  const infoQuery = useAdbInfo();
  const saveSettings = useUpdateAdbSettings();
  const start = useStartAdb();
  const stop = useStopAdb();

  // ADB OCID は backend/.env を正本とする読み取り専用値。
  const ocid = settings.adb_ocid;
  const [region, setRegion] = useState(settings.region || ADB_DEFAULT_REGION);
  const [log, setLog] = useState<AdbOperationLogEntry[]>([]);

  useEffect(() => {
    setRegion(settings.region || ADB_DEFAULT_REGION);
  }, [settings.region]);

  const info = infoQuery.data;
  // 遷移中は useAdbInfo が背景ポーリングするため、その isFetching で操作ボタンを
  // 無効化しない(4 秒ごとのちらつき/無効化を避ける)。明示的な操作の最中だけ busy。
  const busy = saveSettings.isPending || start.isPending || stop.isPending;

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

  async function handleRefresh() {
    const result = await persist();
    if (result) appendLog(result);
  }

  async function handleStart() {
    if (!(await persist())) return;
    try {
      const result = await start.mutateAsync();
      appendLog(result);
    } catch {
      /* mutation error surface は下部の FormStatus が担う */
    }
  }

  async function handleStop() {
    if (!(await persist())) return;
    try {
      const result = await stop.mutateAsync();
      appendLog(result);
    } catch {
      /* mutation error surface は下部の FormStatus が担う */
    }
  }

  const actionError =
    saveSettings.error instanceof ApiError
      ? saveSettings.error.message
      : start.error instanceof ApiError
        ? start.error.message
        : stop.error instanceof ApiError
          ? stop.error.message
          : start.isError || stop.isError || saveSettings.isError
            ? t("settings.adb.notify.actionFailed")
            : null;

  return (
    <Card className="rounded-md">
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
            loading={saveSettings.isPending}
            disabled={busy}
            onClick={() => void handleRefresh()}
          >
            <RefreshCw size={15} aria-hidden />
            {saveSettings.isPending
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

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          <Button
            type="button"
            size="lg"
            loading={saveSettings.isPending}
            disabled={busy || !ocid.trim()}
            onClick={() => void handleRefresh()}
          >
            <Save size={16} aria-hidden />
            {saveSettings.isPending
              ? t("settings.database.actions.saving")
              : t("settings.database.actions.save")}
          </Button>
          <Button
            type="button"
            size="lg"
            variant="secondary"
            loading={start.isPending}
            disabled={busy || !ocid.trim()}
            onClick={() => void handleStart()}
          >
            <Power size={16} aria-hidden />
            {start.isPending
              ? t("settings.adb.action.starting")
              : t("settings.adb.action.start")}
          </Button>
          <Button
            type="button"
            size="lg"
            variant="secondary"
            loading={stop.isPending}
            disabled={busy || !ocid.trim()}
            onClick={() => void handleStop()}
          >
            <PowerOff size={16} aria-hidden />
            {stop.isPending ? t("settings.adb.action.stopping") : t("settings.adb.action.stop")}
          </Button>
          {actionError ? <FormStatus tone="danger" message={actionError} /> : null}
        </div>

        {info && info.lifecycle_state ? <AdbInfoPanel info={info} /> : null}

        {log.length > 0 ? <AdbOperationLog entries={log} /> : null}
      </CardContent>
    </Card>
  );
}

function AdbInfoPanel({ info }: { info: AdbInfoData }) {
  const known = info.status === "success" || info.status === "accepted";
  // ReadinessBadge と同様、自己完結したステータスバーを余分なパネルで囲まない。
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
        {t("settings.adb.field.status")}: {adbLifecycleLabel(state)}
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
  error,
  onChange,
}: {
  value: string;
  services: string[];
  error?: string;
  onChange: (value: string) => void;
}) {
  const serviceOptions = services.map((service) => ({
    value: service,
    label: service,
  })) satisfies SelectFieldOption<string>[];

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
        buttonClassName="h-11"
      />
    );
  }

  return (
    <TextField
      id="oracle-wallet-service"
      label={t("settings.database.field.serviceDsn")}
      required
      value={value}
      onChange={onChange}
      placeholder={t("settings.database.placeholder.serviceDsnManual")}
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
  error,
  required = false,
  inputRef,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  error?: string;
  required?: boolean;
  inputRef?: RefObject<HTMLInputElement | null>;
}) {
  const errorId = `${id}-error`;

  return (
    <div className="space-y-1.5">
      <RequiredLabel id={id} label={label} required={required} />
      <input
        ref={inputRef}
        id={id}
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? errorId : undefined}
        className={cn(
          "h-11 w-full rounded-md border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
          error ? "border-danger" : "border-border"
        )}
      />
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
  inputRef: RefObject<HTMLInputElement | null>;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;
  const describedBy = [hintId, error ? errorId : ""].filter(Boolean).join(" ");

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <RequiredLabel id={id} label={label} required={required} />
        {hasSavedSecret ? (
          <span className="rounded-full border border-success/30 bg-success-bg px-2 py-0.5 text-xs font-medium text-success">
            {t("settings.database.secrets.saved")}
          </span>
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
          disabled={disabled}
          aria-label={
            visible
              ? t("settings.database.secrets.hide")
              : t("settings.database.secrets.show")
          }
          className="absolute right-0 top-0 flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-md text-muted transition-colors hover:bg-background hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {hasSavedSecret
          ? t("settings.database.helper.passwordSavedCompact")
          : t("settings.database.helper.passwordRequired")}
      </p>
      <FieldError id={errorId} message={error} />
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
  return (
    <label htmlFor={id} className="text-sm font-medium text-foreground">
      {label}
      {required ? (
        <span aria-hidden="true" className="ml-0.5">
          *
        </span>
      ) : null}
    </label>
  );
}

function WalletUploadField({
  inputRef,
  settings,
  uploadPending,
  uploadedFileName,
  uploadError,
  validationError,
  onUpload,
}: {
  inputRef: RefObject<HTMLInputElement | null>;
  settings: DatabaseSettingsData;
  uploadPending: boolean;
  uploadedFileName: string | null;
  uploadError: string | null;
  validationError?: string;
  onUpload: (file: File) => void;
}) {
  const hintId = "oracle-wallet-upload-hint";

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) onUpload(file);
  }

  return (
    <div className="space-y-2">
      <span className="block text-sm font-medium text-foreground">
        {t("settings.database.wallet.title")}
      </span>
      <button
        type="button"
        disabled={uploadPending}
        onClick={() => inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
        aria-describedby={hintId}
        className="flex min-h-36 w-full cursor-pointer flex-col items-center justify-center rounded-md border border-dashed border-border bg-background px-4 py-6 text-center transition-colors hover:border-primary hover:bg-info-bg/30 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60"
      >
        <Upload size={24} className="text-muted" aria-hidden />
        <span className="mt-2 text-sm font-semibold text-foreground">
          {uploadPending
            ? t("settings.database.actions.uploadingWallet")
            : t("settings.database.wallet.uploadCta")}
        </span>
        <span id={hintId} className="mt-2 text-sm leading-relaxed text-muted">
          {t("settings.database.wallet.help")}
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        className="hidden"
        accept=".zip,application/zip,application/x-zip-compressed,application/octet-stream"
        aria-label={t("settings.database.walletInput.aria")}
        aria-describedby={hintId}
        disabled={uploadPending}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onUpload(file);
          event.target.value = "";
        }}
      />

      {uploadedFileName ? (
        <FormStatus
          tone="success"
          className="text-xs"
          message={t("settings.database.actions.walletUploaded", { fileName: uploadedFileName })}
        />
      ) : null}
      {validationError ? (
        <FormStatus tone="warning" className="text-xs" message={validationError} />
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
        <StatusLine
          label={t("settings.database.status.readiness")}
          value={readinessLabel(settings.readiness)}
          ok={settings.readiness === "ok"}
        />
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

function StatusPanel({ settings }: { settings: DatabaseSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <ShieldCheck size={18} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.database.status.title")}</CardTitle>
            <CardDescription>{t("settings.database.status.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <ReadinessBadge readiness={settings.readiness} />
        <MetadataRow
          label={t("settings.database.status.authMethod")}
          value={authMethodLabel(settings)}
        />
        <MetadataRow
          label={t("settings.database.status.wallet")}
          value={
            settings.wallet_uploaded
              ? t("settings.database.wallet.detected")
              : t("settings.database.wallet.notDetected")
          }
        />
      </CardContent>
    </Card>
  );
}

function ConnectionTestResultPanel({ result }: { result: DatabaseConnectionTestResult }) {
  const success = result.status === "success";
  const skipped = result.status === "skipped";
  const Icon = success ? CheckCircle2 : skipped ? AlertCircle : XCircle;

  return (
    <div
      role={success || skipped ? "status" : "alert"}
      className={cn(
        "flex flex-col gap-2 rounded-md border px-4 py-3 text-sm",
        success && "border-success/30 bg-success-bg/50 text-foreground",
        skipped && "border-warning/30 bg-warning-bg/60 text-foreground",
        !success && !skipped && "border-danger/30 bg-danger-bg/50 text-foreground"
      )}
    >
      <div className="flex items-start gap-2">
        <Icon
          size={18}
          className={cn(
            "mt-0.5 shrink-0",
            success && "text-success",
            skipped && "text-warning",
            !success && !skipped && "text-danger"
          )}
          aria-hidden
        />
        <div>
          <p className="font-medium">{result.message}</p>
          <p className="mt-1 text-xs text-muted">
            {t("settings.database.test.meta", {
              readiness: readinessLabel(result.readiness),
              elapsed: result.elapsed_ms,
              checkedAt: formatDateTime(result.checked_at),
            })}
            {result.error_type ? ` / ${result.error_type}` : ""}
          </p>
          {result.troubleshooting.length > 0 ? (
            <ul className="mt-2 space-y-1 text-xs leading-relaxed text-muted">
              {result.troubleshooting.map((tip) => (
                <li key={tip} className="flex gap-1.5">
                  <span aria-hidden="true">-</span>
                  <span>{tip}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
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
        {t("settings.database.status.readiness")}: {readinessLabel(readiness)}
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

function formFromSettings(settings: DatabaseSettingsData): DatabaseSettingsForm {
  return {
    user: settings.user,
    dsn: settings.dsn,
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
    wallet_dir: settings.wallet_dir,
  };
  if (form.clearPassword) payload.clear_password = true;
  else if (form.password !== "") payload.password = form.password;
  return payload;
}

function buildDatabaseEnvFile(
  form: DatabaseSettingsForm,
  settings: DatabaseSettingsData
): string {
  const entries: [string, string][] = [
    ["ORACLE_USER", form.user],
    [
      "ORACLE_PASSWORD",
      secretPreview(form.password, settings.has_password, form.clearPassword),
    ],
    ["ORACLE_DSN", form.dsn],
    ["ORACLE_CLIENT_LIB_DIR", oracleClientLibDir(settings.wallet_dir)],
    [
      "ORACLE_WALLET_PASSWORD",
      settings.has_wallet_password ? t("settings.preview.secret.saved") : "",
    ],
  ];
  return [
    "# Oracle 26ai",
    ...entries.map(([key, value]) => `${key}=${formatSettingsEnvValue(value)}`),
  ].join("\n");
}

function secretPreview(value: string, hasSavedSecret: boolean, clearSecret = false): string {
  if (clearSecret) return "";
  if (value.trim()) return t("settings.preview.secret.entered");
  return hasSavedSecret ? t("settings.preview.secret.saved") : "";
}

function oracleClientLibDir(walletDir: string): string {
  const suffix = "/network/admin";
  const normalized = walletDir.trim();
  if (normalized.endsWith(suffix)) return normalized.slice(0, -suffix.length);
  return normalized;
}

function authMethodLabel(settings: DatabaseSettingsData): string {
  if (settings.has_password && settings.wallet_uploaded) {
    return t("settings.database.authMethod.passwordAndWallet");
  }
  if (settings.has_password) {
    return t("settings.database.authMethod.password");
  }
  if (settings.wallet_uploaded) {
    return t("settings.database.authMethod.wallet");
  }
  return t("settings.database.secrets.notSet");
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

function readinessLabel(readiness: string): string {
  switch (readiness) {
    case "ok":
      return t("settings.database.readiness.ok");
    case "missing":
      return t("settings.database.readiness.missing");
    case "missing_credentials":
      return t("settings.database.readiness.missingCredentials");
    case "invalid":
      return t("settings.database.readiness.invalid");
    case "wallet_not_found":
      return t("settings.database.readiness.walletNotFound");
    case "error":
      return t("settings.database.readiness.error");
    default:
      return readiness || t("settings.database.readiness.unknown");
  }
}
