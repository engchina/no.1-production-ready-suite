"use client";

import {
  AlertCircle,
  CheckCircle2,
  Database,
  Eye,
  EyeOff,
  FileArchive,
  KeyRound,
  PlugZap,
  RefreshCw,
  Save,
  ShieldCheck,
  Upload,
  XCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type DatabaseConnectionTestResult,
  type DatabaseSettingsData,
  type DatabaseSettingsUpdate,
} from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useDatabaseSettings,
  useTestDatabaseSettings,
  useUpdateDatabaseSettings,
  useUploadDatabaseWallet,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

interface DatabaseSettingsForm {
  user: string;
  dsn: string;
  walletDir: string;
  password: string;
  walletPassword: string;
}

const EMPTY_FORM: DatabaseSettingsForm = {
  user: "",
  dsn: "",
  walletDir: "",
  password: "",
  walletPassword: "",
};

/** Oracle 26ai の runtime 接続設定フォーム。 */
export function DatabaseSettingsClient() {
  const query = useDatabaseSettings();
  const save = useUpdateDatabaseSettings();
  const walletUpload = useUploadDatabaseWallet();
  const test = useTestDatabaseSettings();
  const resetTest = test.reset;

  const [form, setForm] = useState<DatabaseSettingsForm>(EMPTY_FORM);
  const [visible, setVisible] = useState({ password: false, walletPassword: false });
  const [saved, setSaved] = useState(false);
  const [uploadedWalletFileName, setUploadedWalletFileName] = useState<string | null>(null);
  const [optimisticSettings, setOptimisticSettings] = useState<DatabaseSettingsData | null>(null);

  useEffect(() => {
    if (query.data) {
      setForm(formFromSettings(query.data));
      setSaved(false);
      setOptimisticSettings(null);
    }
  }, [query.data]);

  function updateForm(update: Partial<DatabaseSettingsForm>) {
    setForm((current) => ({ ...current, ...update }));
    setSaved(false);
    resetTest();
  }

  function submit() {
    save.mutate(payloadFromForm(form), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setOptimisticSettings(data);
        setSaved(true);
      },
    });
  }

  function runTest() {
    test.mutate(payloadFromForm(form));
  }

  function uploadWallet(file: File) {
    setSaved(false);
    setUploadedWalletFileName(null);
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
        <Skeleton className="h-52 w-full rounded-lg" />
        <Skeleton className="h-80 w-full rounded-lg" />
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
    <div className="space-y-5 p-8">
      {settings.adapter === "local" ? (
        <div className="flex items-start gap-3 rounded-md border border-warning/30 bg-warning-bg/60 px-4 py-3 text-sm text-foreground">
          <AlertCircle size={18} className="mt-0.5 shrink-0 text-warning" aria-hidden />
          <p>{t("settings.database.localAdapterNotice")}</p>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
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
                  <Database size={20} aria-hidden />
                </div>
                <div>
                  <CardTitle>{t("settings.database.connection.title")}</CardTitle>
                  <CardDescription>
                    {t("settings.database.connection.description")}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <TextField
                  id="oracle-user"
                  label={t("settings.database.field.user")}
                  value={form.user}
                  onChange={(value) => updateForm({ user: value })}
                  helper={t("settings.database.helper.user")}
                  placeholder={t("settings.database.placeholder.user")}
                />
                <WalletDirectoryField
                  value={form.walletDir}
                  onUpload={uploadWallet}
                  uploadPending={walletUpload.isPending}
                  uploadedFileName={uploadedWalletFileName}
                  uploadError={walletUpload.isError ? walletUploadError : null}
                  warning={
                    settings.wallet_uploaded
                      ? null
                      : t("settings.database.walletDir.missing")
                  }
                />
              </div>
              <DsnField
                value={form.dsn}
                onChange={(value) => updateForm({ dsn: value })}
                services={settings.available_services}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <div className="flex items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
                  <KeyRound size={20} aria-hidden />
                </div>
                <div>
                  <CardTitle>{t("settings.database.secrets.title")}</CardTitle>
                  <CardDescription>{t("settings.database.secrets.description")}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <SecretField
                id="oracle-password"
                label={t("settings.database.field.password")}
                value={form.password}
                visible={visible.password}
                hasSavedSecret={settings.has_password}
                onToggleVisible={() =>
                  setVisible((current) => ({ ...current, password: !current.password }))
                }
                onChange={(value) => updateForm({ password: value })}
                helper={
                  settings.has_password
                    ? t("settings.database.helper.passwordSaved")
                    : t("settings.database.helper.passwordEmpty")
                }
              />

              <SecretField
                id="oracle-wallet-password"
                label={t("settings.database.field.walletPassword")}
                value={form.walletPassword}
                visible={visible.walletPassword}
                hasSavedSecret={settings.has_wallet_password}
                onToggleVisible={() =>
                  setVisible((current) => ({
                    ...current,
                    walletPassword: !current.walletPassword,
                  }))
                }
                onChange={(value) => updateForm({ walletPassword: value })}
                helper={
                  settings.has_wallet_password
                    ? t("settings.database.helper.walletPasswordSaved")
                    : t("settings.database.helper.walletPasswordEmpty")
                }
              />
            </CardContent>
          </Card>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="submit" loading={save.isPending}>
              <Save size={15} aria-hidden />
              {save.isPending ? t("settings.database.actions.saving") : t("settings.database.actions.save")}
            </Button>
            <Button type="button" variant="secondary" loading={test.isPending} onClick={runTest}>
              <PlugZap size={15} aria-hidden />
              {test.isPending
                ? t("settings.database.actions.testing")
                : t("settings.database.actions.test")}
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                void query.refetch();
                save.reset();
                resetTest();
                setSaved(false);
                setOptimisticSettings(null);
              }}
            >
              <RefreshCw size={15} aria-hidden />
              {t("settings.database.actions.reload")}
            </Button>
            {saved ? (
              <p className="text-sm font-medium text-success" role="status">
                {t("settings.database.actions.saved")}
              </p>
            ) : null}
            {save.isError ? (
              <p className="text-sm font-medium text-danger" role="alert">
                {saveError}
              </p>
            ) : null}
          </div>
        </form>

        <aside className="space-y-5">
          <StatusPanel settings={settings} />
          <OperationPanel />
        </aside>
      </div>

      {testResult ? <ConnectionTestResultPanel result={testResult} /> : null}
    </div>
  );
}

function DsnField({
  value,
  services,
  onChange,
}: {
  value: string;
  services: string[];
  onChange: (value: string) => void;
}) {
  const serviceOptions = services.map((service) => ({
    value: service,
    label: service,
  })) satisfies SelectFieldOption<string>[];
  const selectedService = services.includes(value) ? value : "";

  return (
    <div className="space-y-3">
      {serviceOptions.length > 0 ? (
        <SelectField
          id="oracle-dsn-service"
          label={t("settings.database.field.dsnService")}
          value={selectedService}
          options={serviceOptions}
          onValueChange={onChange}
          helper={t("settings.database.helper.dsnService")}
          placeholder={t("settings.database.placeholder.dsnService")}
          buttonClassName="min-h-11"
        />
      ) : null}
      <TextField
        id="oracle-dsn"
        label={t("settings.database.field.dsn")}
        value={value}
        onChange={onChange}
        helper={
          serviceOptions.length > 0
            ? t("settings.database.helper.dsnWithServices")
            : t("settings.database.helper.dsn")
        }
        placeholder={t("settings.database.placeholder.dsn")}
      />
    </div>
  );
}

function TextField({
  id,
  label,
  value,
  onChange,
  helper,
  placeholder,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  helper: string;
  placeholder: string;
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
        aria-describedby={hintId}
        className="h-11 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
      />
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
    </div>
  );
}

function WalletDirectoryField({
  value,
  onUpload,
  uploadPending,
  uploadedFileName,
  uploadError,
  warning,
}: {
  value: string;
  onUpload: (file: File) => void;
  uploadPending: boolean;
  uploadedFileName: string | null;
  uploadError: string | null;
  warning: string | null;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const id = "oracle-wallet-dir";
  const hintId = `${id}-hint`;
  const warningId = `${id}-warning`;
  const uploadHintId = "oracle-wallet-upload-hint";
  const describedBy = [hintId, warning ? warningId : ""].filter(Boolean).join(" ");

  return (
    <div className="space-y-2">
      <div className="space-y-1.5">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {t("settings.database.field.walletDir")}
        </label>
        <input
          id={id}
          type="text"
          value={value}
          readOnly
          placeholder={t("settings.database.placeholder.walletDir")}
          aria-describedby={describedBy}
          className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground outline-none placeholder:text-muted/70"
        />
        <p id={hintId} className="text-xs leading-relaxed text-muted">
          {t("settings.database.helper.walletDir")}
        </p>
        {warning ? (
          <p
            id={warningId}
            className="flex items-start gap-1.5 text-xs leading-relaxed text-warning"
            role="status"
          >
            <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden />
            <span>{warning}</span>
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-2 rounded-md border border-border bg-background px-3 py-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-2 text-sm">
          <FileArchive size={17} className="mt-0.5 shrink-0 text-primary" aria-hidden />
          <div className="min-w-0">
            <p className="font-medium text-foreground">{t("settings.database.field.walletZip")}</p>
            <p id={uploadHintId} className="mt-1 text-xs leading-relaxed text-muted">
              {t("settings.database.helper.walletZip")}
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          loading={uploadPending}
          onClick={() => inputRef.current?.click()}
          className="min-h-[44px] shrink-0"
        >
          {uploadPending ? null : <Upload size={15} aria-hidden />}
          {uploadPending
            ? t("settings.database.actions.uploadingWallet")
            : t("settings.database.actions.selectWallet")}
        </Button>
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".zip,application/zip,application/x-zip-compressed,application/octet-stream"
          aria-label={t("settings.database.walletInput.aria")}
          aria-describedby={uploadHintId}
          disabled={uploadPending}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) onUpload(file);
            event.target.value = "";
          }}
        />
      </div>

      {uploadedFileName ? (
        <p className="text-xs font-medium text-success" role="status">
          {t("settings.database.actions.walletUploaded", { fileName: uploadedFileName })}
        </p>
      ) : null}
      {uploadError ? (
        <p className="text-xs font-medium text-danger" role="alert">
          {uploadError}
        </p>
      ) : null}
    </div>
  );
}

function SecretField({
  id,
  label,
  value,
  visible,
  hasSavedSecret,
  helper,
  onChange,
  onToggleVisible,
}: {
  id: string;
  label: string;
  value: string;
  visible: boolean;
  hasSavedSecret: boolean;
  helper: string;
  onChange: (value: string) => void;
  onToggleVisible: () => void;
}) {
  const hintId = `${id}-hint`;

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id} className="text-sm font-medium text-foreground">
          {label}
        </label>
        <span className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-muted">
          {hasSavedSecret
            ? t("settings.database.secrets.saved")
            : t("settings.database.secrets.notSet")}
        </span>
      </div>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={t("settings.database.placeholder.secret")}
          aria-describedby={hintId}
          className="h-11 w-full rounded-md border border-border bg-card px-3 pr-12 text-sm text-foreground outline-none transition-colors placeholder:text-muted/70 focus-visible:border-primary"
        />
        <button
          type="button"
          onClick={onToggleVisible}
          aria-label={
            visible
              ? t("settings.database.secrets.hide")
              : t("settings.database.secrets.show")
          }
          className="absolute right-0 top-0 flex h-11 w-11 cursor-pointer items-center justify-center rounded-r-md text-muted transition-colors hover:bg-background hover:text-foreground"
        >
          {visible ? <EyeOff size={16} aria-hidden /> : <Eye size={16} aria-hidden />}
        </button>
      </div>
      <p id={hintId} className="text-xs leading-relaxed text-muted">
        {helper}
      </p>
    </div>
  );
}

function StatusPanel({ settings }: { settings: DatabaseSettingsData }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
            <ShieldCheck size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.database.status.title")}</CardTitle>
            <CardDescription>{t("settings.database.status.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <ReadinessBadge readiness={settings.readiness} />
        <MetadataRow label={t("settings.database.status.adapter")} value={adapterLabel(settings.adapter)} />
        <MetadataRow
          label={t("settings.database.status.source")}
          value={t("settings.database.source.runtime")}
        />
        <MetadataRow
          label={t("settings.database.status.password")}
          value={
            settings.has_password
              ? t("settings.database.secrets.saved")
              : t("settings.database.secrets.notSet")
          }
        />
        <MetadataRow
          label={t("settings.database.status.walletPassword")}
          value={
            settings.has_wallet_password
              ? t("settings.database.secrets.saved")
              : t("settings.database.secrets.notSet")
          }
        />
        <MetadataRow
          label={t("settings.database.status.wallet")}
          value={
            settings.wallet_uploaded
              ? t("settings.database.wallet.detected")
              : t("settings.database.wallet.notDetected")
          }
        />
        <MetadataRow
          label={t("settings.database.status.services")}
          value={
            settings.available_services.length > 0
              ? settings.available_services.join(", ")
              : t("settings.database.services.empty")
          }
        />
        <MetadataRow
          label={t("settings.database.status.embeddingDim")}
          value={String(settings.embedding_dimension)}
        />
        <MetadataRow label={t("settings.database.status.vectorColumn")} value={settings.vector_column} />
      </CardContent>
    </Card>
  );
}

function OperationPanel() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{t("settings.database.ops.title")}</CardTitle>
        <CardDescription>{t("settings.database.ops.description")}</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2 text-sm leading-relaxed text-muted">
          <li>{t("settings.database.ops.env")}</li>
          <li>{t("settings.database.ops.vector")}</li>
          <li>{t("settings.database.ops.pool")}</li>
        </ul>
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
              checkedAt: formatDateTime(result.checked_at),
            })}
            {result.error_type ? ` / ${result.error_type}` : ""}
          </p>
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
    walletDir: settings.wallet_dir,
    password: "",
    walletPassword: "",
  };
}

function payloadFromForm(form: DatabaseSettingsForm): DatabaseSettingsUpdate {
  const payload: DatabaseSettingsUpdate = {
    user: form.user,
    dsn: form.dsn,
    wallet_dir: form.walletDir,
  };
  if (form.password !== "") payload.password = form.password;
  if (form.walletPassword !== "") payload.wallet_password = form.walletPassword;
  return payload;
}

function adapterLabel(adapter: string): string {
  return adapter === "oci"
    ? t("settings.database.adapter.oci")
    : t("settings.database.adapter.local");
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
