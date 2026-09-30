"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  FormStatus,
  ProcessingIndicator,
  TimedLoadingState,
  FormSkeleton,
  TextField,
} from "@engchina/production-ready-ui";
import { useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Plug,
  RotateCcw,
  Save,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import {
  SERVICE_PROFILE_ORDER,
  ServiceProfileBadge,
  ServiceStatusBadge,
  type DisplayRuntimeStatus,
} from "@/components/settings/ServicesManagementClient";
import {
  ApiError,
  type ExternalParserBackendName,
  type ExternalParserConnectionData,
  type ExternalParserConnectionStatus,
  type ParserAdapterBackend,
  type ParserAdapterBackendName,
  type ParserAdapterSettingsData,
  type ParserAdapterSettingsUpdate,
  type ParserServiceBackendData,
  type ParserServiceBackendName,
  type ServiceProfile,
} from "@/lib/api";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useValuesChanged } from "@/lib/render-sync";
import { t, type I18nKey } from "@/lib/i18n";
import {
  findParserCapability,
  formatSupportedExtensions,
  formatSupportedFormats,
} from "@/lib/parser-capabilities";
import {
  useParserAdapterSettings,
  useExternalParserStatus,
  useServiceStatusQueries,
  useUpdateParserAdapterSettings,
} from "@/lib/queries";
import { cn } from "@/lib/utils";
import { PostParseSettingsCard } from "./PostParseSettingsCard";

type ParserAdapterForm = {
  adapter_backend: ParserAdapterBackend;
  docling_enabled: boolean;
  unstructured_enabled: boolean;
  mineru_enabled: boolean;
  dots_ocr_enabled: boolean;
  connections: Record<ExternalParserBackendName, ExternalParserConnectionForm>;
};
type ExternalParserConnectionForm = {
  endpoint: string;
  model: string;
  api_key: string;
  clear_api_key: boolean;
};
type ParserAdapterFlagField = Exclude<
  keyof ParserAdapterForm,
  "adapter_backend" | "connections"
>;
type ConnectionFieldErrors = Record<string, string>;

const EXTERNAL_BACKENDS: ExternalParserBackendName[] = ["mineru", "dots_ocr"];

const ADAPTER_FLAG_FIELDS: Record<ParserAdapterBackendName, ParserAdapterFlagField> = {
  docling: "docling_enabled",
  unstructured: "unstructured_enabled",
  mineru: "mineru_enabled",
  dots_ocr: "dots_ocr_enabled",
};

const SERVICE_BACKENDS: ParserServiceBackendName[] = [
  "oci_genai_vision",
  "oci_document_understanding",
];

const PARSER_BACKEND_SERVICE_IDS: Partial<
  Record<ParserAdapterBackendName | ParserServiceBackendName, string>
> = {
  docling: "parser-docling",
  unstructured: "parser-unstructured",
  oci_genai_vision: "parser-oci-genai-vision",
  oci_document_understanding: "parser-oci-document-understanding",
};

function isAdapterBackend(backend: ParserAdapterBackend): backend is ParserAdapterBackendName {
  return backend in ADAPTER_FLAG_FIELDS;
}

function isServiceBackend(backend: ParserAdapterBackend): backend is ParserServiceBackendName {
  return (SERVICE_BACKENDS as ParserAdapterBackend[]).includes(backend);
}

function isExternalBackend(
  backend: ParserAdapterBackend
): backend is ExternalParserBackendName {
  return (EXTERNAL_BACKENDS as ParserAdapterBackend[]).includes(backend);
}

/** Optional parser adapter の runtime 設定と readiness を管理する設定画面。 */
export function ParserAdapterSettingsClient() {
  const query = useParserAdapterSettings();
  const save = useUpdateParserAdapterSettings();
  const [form, setForm] = useState<ParserAdapterForm | null>(null);
  const [connectionErrors, setConnectionErrors] = useState<ConnectionFieldErrors>({});
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  // 解析エンジンの server 値か保存中フラグが変わったレンダーで、フォームを server 値に戻す。
  // 「解析後の処理」の保存でも query.data は変わるため、解析エンジンの部分だけを比べる（#528）。
  const serverForm = query.data ? serializeForm(formFromSettings(query.data)) : null;
  const serverChanged = useValuesChanged([serverForm, save.isPending]);
  if (serverChanged && query.data && !save.isPending) {
    setForm(formFromSettings(query.data));
  }

  // 未保存の選択があるときだけ、サイドナビ・内部リンク・再読込での離脱を確認する。
  useLeaveGuard(Boolean(query.data && form && serializeForm(form) !== serializeForm(formFromSettings(query.data))));

  if (query.isPending) {
    return (
      <PageBody wide>
        <TimedLoadingState
          label={t("settings.loading")}
          operationKey="settings-parser-adapters-load"
          placement="page"
          testId="settings-parser-adapters-loading"
        >
          <FormSkeleton fields={2} />
          <FormSkeleton fields={5} />
        </TimedLoadingState>
      </PageBody>
    );
  }

  if (query.isError) {
    return (
      <PageBody wide>
        <ErrorState
          message={
            query.error instanceof ApiError
              ? query.error.message
              : t("settings.parserAdapters.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </PageBody>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = serializeForm(form) !== serializeForm(formFromSettings(settings));
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.parserAdapters.saveError");

  function updateForm(update: Partial<ParserAdapterForm>) {
    save.reset();
    setSuccessMessage(null);
    setForm((current) => (current ? { ...current, ...update } : current));
  }

  function selectBackend(adapterBackend: ParserAdapterBackend) {
    const enabledUpdate = externalBackendFlagUpdate(adapterBackend);
    updateForm({ adapter_backend: adapterBackend, ...enabledUpdate });
  }

  function resetForm() {
    save.reset();
    setConnectionErrors({});
    setSuccessMessage(null);
    setForm(formFromSettings(settings));
  }

  function updateConnection(
    backend: ExternalParserBackendName,
    update: Partial<ExternalParserConnectionForm>
  ) {
    save.reset();
    setSuccessMessage(null);
    setConnectionErrors((current) => {
      const next = { ...current };
      for (const field of Object.keys(update)) delete next[`${backend}.${field}`];
      return next;
    });
    setForm((current) =>
      current
        ? {
            ...current,
            connections: {
              ...current.connections,
              [backend]: { ...current.connections[backend], ...update },
            },
          }
        : current
    );
  }

  function submit() {
    if (!form) return;
    const errors = validateConnections(form);
    setConnectionErrors(errors);
    const firstError = Object.keys(errors)[0];
    if (firstError) {
      requestAnimationFrame(() => focusConnectionError(firstError));
      return;
    }
    save.mutate(parserSettingsUpdate(form), {
      onSuccess: (data) => {
        setForm(formFromSettings(data));
        setSuccessMessage(t("settings.parserAdapters.actions.saved"));
      },
      onError: () => {
        setSuccessMessage(null);
      },
    });
  }

  return (
    <PageBody wide>
      <OverviewCard
        dirty={dirty}
        form={form}
        settings={settings}
        saving={save.isPending}
        successMessage={successMessage}
        errorMessage={save.isError ? saveError : null}
        connectionErrors={connectionErrors}
        onBackendChange={selectBackend}
        onConnectionChange={updateConnection}
        onReset={resetForm}
        onSubmit={submit}
      />
      {/* 解析の後の Vision・項目抽出・章節木の全体の既定（#528）。Vision の読み取りの指示もこの中で編集する。 */}
      <PostParseSettingsCard settings={settings} />
    </PageBody>
  );
}

function OverviewCard({
  dirty,
  form,
  settings,
  saving,
  successMessage,
  errorMessage,
  connectionErrors,
  onBackendChange,
  onConnectionChange,
  onReset,
  onSubmit,
}: {
  dirty: boolean;
  form: ParserAdapterForm;
  settings: ParserAdapterSettingsData;
  saving: boolean;
  successMessage: string | null;
  errorMessage: string | null;
  connectionErrors: ConnectionFieldErrors;
  onBackendChange: (backend: ParserAdapterBackend) => void;
  onConnectionChange: (
    backend: ExternalParserBackendName,
    update: Partial<ExternalParserConnectionForm>
  ) => void;
  onReset: () => void;
  onSubmit: () => void;
}) {
  const backendOptions = useMemo(
    () => backendOptionsFromSettings(settings),
    [settings]
  );
  const backendProfileGroups = useMemo(
    () =>
      SERVICE_PROFILE_ORDER.map((profile) => ({
        profile,
        backends: backendOptions.filter(
          (backend) => serviceProfileForBackend(backend) === profile
        ),
      })).filter((group) => group.backends.length > 0),
    [backendOptions]
  );
  const serviceIds = useMemo(
    () => [
      ...new Set(
        backendOptions
          .map(serviceIdForBackend)
          .filter((serviceId): serviceId is string => Boolean(serviceId))
      ),
    ],
    [backendOptions]
  );
  const serviceStatusQueries = useServiceStatusQueries(serviceIds);
  const runtimeByServiceId = useMemo(() => {
    const services = new Map<
      string,
      { status: DisplayRuntimeStatus; profile: ServiceProfile | null }
    >();
    serviceIds.forEach((serviceId, index) => {
      const statusQuery = serviceStatusQueries[index];
      services.set(serviceId, {
        status: statusQuery?.data?.status ?? (statusQuery?.isError ? "error" : "loading"),
        profile: statusQuery?.data?.profile ?? null,
      });
    });
    return services;
  }, [serviceIds, serviceStatusQueries]);
  const serviceByName = useMemo(
    () =>
      new Map<ParserServiceBackendName, ParserServiceBackendData>(
        (settings.service_backends ?? []).map((item) => [item.backend, item])
      ),
    [settings.service_backends]
  );
  const connectionByBackend = useMemo(
    () =>
      new Map<ExternalParserBackendName, ExternalParserConnectionData>(
        (settings.connections ?? []).map((connection) => [connection.backend, connection])
      ),
    [settings.connections]
  );
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
            <Plug size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.overview.title")}</CardTitle>
            <CardDescription>
              {t("settings.parserAdapters.overview.description")}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <div className="text-sm font-medium text-fg">
            {t("settings.parserAdapters.backend")}
          </div>
          <div
            role="radiogroup"
            aria-label={t("settings.parserAdapters.backend")}
            className="space-y-2"
          >
            {/* CPU / GPU / OCI の各グループは 2 エンジンなので、1 行 2 枚で横幅いっぱいに並べる
                （下の外部 GPU 接続カードと同じ 2 列）。 */}
            {backendProfileGroups.map((group) => (
              <div key={group.profile} className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {group.backends.map((backend) => {
                  const selected = form.adapter_backend === backend;
                  const service = isServiceBackend(backend)
                    ? serviceByName.get(backend)
                    : undefined;
                  const externalConnection = isExternalBackend(backend)
                    ? connectionByBackend.get(backend)
                    : undefined;
                  const serviceId = serviceIdForBackend(backend);
                  const runtime = serviceId ? runtimeByServiceId.get(serviceId) : null;
                  const runtimeStatus = runtime?.status ?? null;
                  const runtimeProfile = runtime?.profile ?? serviceProfileForBackend(backend);
                  const capability = findParserCapability(settings.capabilities, backend);
                  const supportedFormats = formatSupportedFormats(capability);
                  const supportedExtensions = formatSupportedExtensions(capability);
                  return (
                    <div key={backend} className="relative min-w-0">
                      <input
                        id={`settings-parser-backend-${backend}`}
                        className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                        type="radio"
                        name="settings-parser-backend"
                        value={backend}
                        checked={selected}
                        disabled={saving}
                        onChange={() => onBackendChange(backend)}
                      />
                      <label
                        htmlFor={`settings-parser-backend-${backend}`}
                        className={cn(
                          "block h-full cursor-pointer min-h-[5.43rem] rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                          selected
                            ? "border-accent-emphasis bg-accent-subtle text-fg"
                            : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
                        )}
                      >
                        <span className="flex items-center gap-1.5">
                          <span className="text-sm font-semibold">{backendLabel(backend)}</span>
                          {runtimeProfile ? <ServiceProfileBadge profile={runtimeProfile} /> : null}
                        </span>
                        <span className="mt-1 block text-xs leading-relaxed text-fg-muted">
                          {t(backendDescriptionKey(backend))}
                        </span>
                        {supportedFormats ? (
                          <span className="mt-1 block text-xs text-fg-muted">
                            {t("settings.parserAdapters.capabilities")}: {supportedFormats}
                          </span>
                        ) : null}
                        {supportedExtensions ? (
                          <span className="mt-0.5 block break-words text-xs leading-4 text-fg-muted">
                            {supportedExtensions}
                          </span>
                        ) : null}
                        <span className="mt-2 flex flex-wrap items-center gap-1.5">
                          {runtimeStatus ? <ServiceStatusBadge status={runtimeStatus} /> : null}
                          {service && !service.configured ? (
                            <span className="inline-flex items-center gap-1 rounded-sm bg-warning-subtle px-1.5 py-0.5 text-xs font-medium text-warning-fg whitespace-nowrap">
                              <AlertTriangle size={14} aria-hidden />
                              {t("settings.parserAdapters.serviceBackend.unconfigured")}
                            </span>
                          ) : null}
                          {externalConnection ? (
                            <span
                              className={cn(
                                "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium whitespace-nowrap",
                                externalConnection.configured
                                  ? "bg-success-subtle text-success-fg"
                                  : "bg-warning-subtle text-warning-fg"
                              )}
                            >
                              {externalConnection.configured ? (
                                <CheckCircle2 size={14} aria-hidden />
                              ) : (
                                <AlertTriangle size={14} aria-hidden />
                              )}
                              {externalConnection.configured
                                ? t("settings.parserAdapters.connection.configured")
                                : t("settings.parserAdapters.serviceBackend.unconfigured")}
                            </span>
                          ) : null}
                        </span>
                      </label>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          {form.adapter_backend === "local" ? (
            <p className="text-xs leading-relaxed text-warning-fg">
              {t("settings.parserAdapters.legacyBackendNotice")}
            </p>
          ) : null}
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("settings.parserAdapters.serviceBackend.note")}
          </p>
        </div>
        <section className="space-y-3 border-t border-border pt-5">
          <div>
            <h3 className="text-sm font-semibold text-fg">
              {t("settings.parserAdapters.connection.title")}
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-fg-muted">
              {t("settings.parserAdapters.connection.description")}
            </p>
          </div>
          <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-2">
            {EXTERNAL_BACKENDS.map((backend) => (
              <ExternalConnectionCard
                key={backend}
                backend={backend}
                connection={connectionByBackend.get(backend)}
                value={form.connections[backend]}
                errors={connectionErrors}
                saving={saving}
                onChange={(update) => onConnectionChange(backend, update)}
              />
            ))}
          </div>
        </section>
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RuntimeFact
            label={t("settings.parserAdapters.backend")}
            value={backendLabel(settings.adapter_backend)}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.effectiveOrder")}
            value={formatEffectiveOrder(settings.effective_order)}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.source")}
            value={configSourceLabel(settings.config_source)}
          />
        </dl>
        <div className="flex flex-col gap-3 border-t border-border pt-4 md:flex-row md:items-center md:justify-between">
          <div className="min-h-6">
            {dirty ? (
              <FormStatus tone="warning" message={t("settings.parserAdapters.actions.unsaved")} />
            ) : null}
            {successMessage ? <FormStatus tone="success" message={successMessage} /> : null}
            {errorMessage ? <FormStatus tone="danger" message={errorMessage} /> : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              loading={saving}
              disabled={!dirty}
              onClick={onSubmit}
              aria-label={t("settings.parserAdapters.actions.save")} icon={Save}>
              {t("settings.parserAdapters.actions.save")}
            </Button>
            <Button
              type="button"
              variant="secondary"
              onClick={onReset}
              disabled={!dirty || saving}
              aria-label={t("settings.parserAdapters.actions.reset")} icon={RotateCcw}>
              {t("settings.parserAdapters.actions.reset")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ExternalConnectionCard({
  backend,
  connection,
  value,
  errors,
  saving,
  onChange,
}: {
  backend: ExternalParserBackendName;
  connection: ExternalParserConnectionData | undefined;
  value: ExternalParserConnectionForm;
  errors: ConnectionFieldErrors;
  saving: boolean;
  onChange: (update: Partial<ExternalParserConnectionForm>) => void;
}) {
  const statusQuery = useExternalParserStatus(backend);
  const modelSupported = backend !== "mineru";
  const dirty =
    value.endpoint.trim() !== (connection?.endpoint ?? "") ||
    (modelSupported && value.model.trim() !== (connection?.model ?? "")) ||
    Boolean(value.api_key) ||
    value.clear_api_key;
  const endpointError = errors[`${backend}.endpoint`];
  const modelError = errors[`${backend}.model`];
  const endpointId = `external-parser-${backend}-endpoint`;
  const modelId = `external-parser-${backend}-model`;
  const apiKeyId = `external-parser-${backend}-api-key`;
  const clearApiKeyId = `external-parser-${backend}-clear-api-key`;

  return (
    <div className="min-w-0 rounded-md border border-border bg-surface-hover p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-fg">{backendLabel(backend)}</h4>
          <p className="mt-0.5 break-all text-xs text-fg-muted">
            {connectionProtocolLabel(connection?.protocol ?? externalProtocol(backend))}
          </p>
        </div>
        <span
          className={cn(
            "inline-flex min-h-6 items-center rounded-md px-2 text-xs font-medium",
            connection?.configured
              ? "bg-success-subtle text-success-fg"
              : "bg-warning-subtle text-warning-fg"
          )}
        >
          {connection?.configured
            ? t("settings.parserAdapters.connection.configured")
            : t("settings.parserAdapters.serviceBackend.unconfigured")}
        </span>
      </div>

      <div className="mt-4 space-y-3">
        <ConnectionTextField
          id={endpointId}
          label={t("settings.parserAdapters.connection.endpoint")}
          type="url"
          value={value.endpoint}
          placeholder={externalEndpointPlaceholder(backend)}
          error={endpointError}
          disabled={saving}
          onChange={(endpoint) => onChange({ endpoint })}
        />
        <ConnectionTextField
          id={modelId}
          label={t("settings.parserAdapters.connection.model")}
          value={modelSupported ? value.model : t("settings.parserAdapters.connection.nativeModel")}
          placeholder={modelSupported ? "model-id" : undefined}
          // Endpoint を入力したときだけ Model が要る（validateConnections と同じ条件。#531）
          required={modelSupported && Boolean(value.endpoint.trim())}
          error={modelError}
          disabled={saving || !modelSupported}
          onChange={(model) => onChange({ model })}
        />
        <ConnectionTextField
          id={apiKeyId}
          label={t("settings.parserAdapters.connection.apiKey")}
          type="password"
          value={value.api_key}
          // 任意の欄は placeholder で「任意」と示さない（#531）。保存済みのときだけ保持の説明を出す。
          placeholder={
            connection?.api_key_configured ? t("settings.parserAdapters.connection.apiKeyRetained") : undefined
          }
          disabled={saving || value.clear_api_key}
          onChange={(api_key) => onChange({ api_key, clear_api_key: false })}
        />
        <label
          htmlFor={clearApiKeyId}
          className="flex min-h-[var(--control-height-touch)] cursor-pointer items-center gap-2 rounded-md text-xs text-fg"
        >
          <input
            id={clearApiKeyId}
            type="checkbox"
            checked={value.clear_api_key}
            disabled={saving || !connection?.api_key_configured}
            onChange={(event) =>
              onChange({ clear_api_key: event.target.checked, api_key: "" })
            }
            className="h-4 w-4 rounded border-border accent-accent-emphasis"
          />
          {t("settings.parserAdapters.connection.clearApiKey")}
        </label>
      </div>

      <div className="mt-4 space-y-2 border-t border-border pt-3">
        <Button
          type="button"
          variant="secondary"
          className="w-full sm:w-auto"
          loading={statusQuery.isFetching}
          disabled={saving || dirty || !connection?.configured}
          onClick={() => void statusQuery.refetch()} icon={Plug}>
          {t("settings.parserAdapters.connection.test")}
        </Button>
        {statusQuery.isFetching ? (
          // 外部の GPU parser へ実際に接続するため、起動直後や高負荷時は数秒以上かかる。
          <ProcessingIndicator
            active
            label={t("settings.parserAdapters.connection.testing", { backend: backendLabel(backend) })}
            operationKey={`external-parser-status-${backend}`}
            placement="action"
            activityIcon="none"
            testId={`external-parser-status-processing-${backend}`}
          />
        ) : null}
        {dirty ? (
          <p className="text-xs leading-relaxed text-fg-muted">
            {t("settings.parserAdapters.connection.saveBeforeTest")}
          </p>
        ) : null}
        {!dirty && statusQuery.data ? (
          <ConnectionTestStatus status={statusQuery.data.status} />
        ) : null}
        {!dirty && statusQuery.isError ? (
          <FormStatus
            tone="danger"
            message={
              statusQuery.error instanceof ApiError
                ? statusQuery.error.message
                : t("settings.parserAdapters.connection.testFailed")
            }
          />
        ) : null}
      </div>
    </div>
  );
}

function ConnectionTextField({
  id,
  label,
  value,
  type = "text",
  placeholder,
  required = false,
  error,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  type?: "text" | "url" | "password";
  placeholder?: string;
  required?: boolean;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <TextField
      id={id}
      className="min-w-0"
      label={label}
      type={type}
      value={value}
      placeholder={placeholder}
      required={required}
      disabled={disabled}
      autoComplete="off"
      error={error}
      onValueChange={onChange}
    />
  );
}

function ConnectionTestStatus({ status }: { status: ExternalParserConnectionStatus }) {
  const success = status === "available";
  return (
    <FormStatus
      tone={success ? "success" : status === "unconfigured" ? "warning" : "danger"}
      message={t(`settings.parserAdapters.connection.status.${status}` as I18nKey)}
    />
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-hover p-3">
      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-fg">{value}</dd>
    </div>
  );
}

function adapterLabel(adapter: ParserAdapterBackendName) {
  if (adapter === "docling") return "Docling";
  if (adapter === "unstructured") return "Unstructured";
  if (adapter === "mineru") return "MinerU";
  return "Dots.OCR";
}

function backendLabel(backend: ParserAdapterBackend) {
  if (backend === "local") return t("settings.parserAdapters.backend.local");
  if (backend === "mineru") return "MinerU";
  if (backend === "dots_ocr") return "Dots.OCR";
  if (backend === "oci_genai_vision")
    return t("settings.parserAdapters.backend.oci_genai_vision");
  // enterprise_ai_vlm は oci_genai_vision の後方互換エイリアス(legacy 表示用)。
  if (backend === "enterprise_ai_vlm")
    return t("settings.parserAdapters.backend.oci_genai_vision");
  if (backend === "oci_document_understanding")
    return t("settings.parserAdapters.backend.oci_document_understanding");
  return adapterLabel(backend);
}

function backendOptionsFromSettings(settings: ParserAdapterSettingsData): ParserAdapterBackend[] {
  const ordered: ParserAdapterBackend[] = [
    ...settings.adapters.map((adapter) => adapter.backend),
    ...(settings.service_backends ?? []).map((service) => service.backend),
  ];
  const selected = normalizeBackend(settings.adapter_backend);
  if (selected !== "local" && !ordered.includes(selected)) ordered.push(selected);
  return [...new Set(ordered.map(normalizeBackend))];
}

function serviceIdForBackend(backend: ParserAdapterBackend): string | null {
  const normalized = normalizeBackend(backend);
  if (normalized === "local" || isExternalBackend(normalized)) return null;
  if (isAdapterBackend(normalized) || isServiceBackend(normalized)) {
    return PARSER_BACKEND_SERVICE_IDS[normalized] ?? null;
  }
  return null;
}

function serviceProfileForBackend(backend: ParserAdapterBackend): ServiceProfile | null {
  const normalized = normalizeBackend(backend);
  if (normalized === "mineru" || normalized === "dots_ocr") return "gpu";
  if (normalized === "oci_genai_vision" || normalized === "oci_document_understanding") {
    return "oci";
  }
  if (isAdapterBackend(normalized)) return "cpu";
  return null;
}

function configSourceLabel(source: string) {
  return source === "runtime" ? t("settings.common.currentConfig") : source;
}

function backendDescriptionKey(backend: ParserAdapterBackend): I18nKey {
  return `settings.parserAdapters.backend.${backend}.description` as I18nKey;
}

function formatEffectiveOrder(order: ParserAdapterBackendName[]) {
  if (!order.length) return t("settings.parserAdapters.noEffectiveOrder");
  return order.map(adapterLabel).join(" -> ");
}

function formFromSettings(settings: ParserAdapterSettingsData): ParserAdapterForm {
  const enabledByBackend = new Map(
    settings.adapters.map((adapter) => [adapter.backend, adapter.enabled])
  );
  return {
    adapter_backend: normalizeBackend(settings.adapter_backend),
    docling_enabled: enabledByBackend.get("docling") ?? false,
    unstructured_enabled: enabledByBackend.get("unstructured") ?? false,
    mineru_enabled: enabledByBackend.get("mineru") ?? false,
    dots_ocr_enabled: enabledByBackend.get("dots_ocr") ?? false,
    connections: Object.fromEntries(
      EXTERNAL_BACKENDS.map((backend) => {
        const connection = (settings.connections ?? []).find(
          (candidate) => candidate.backend === backend
        );
        return [
          backend,
          {
            endpoint: connection?.endpoint ?? "",
            model: connection?.model ?? defaultExternalModel(backend),
            api_key: "",
            clear_api_key: false,
          },
        ];
      })
    ) as Record<ExternalParserBackendName, ExternalParserConnectionForm>,
  };
}

/** 旧称 enterprise_ai_vlm を canonical な oci_genai_vision に正規化する(選択カードの一致用)。 */
function normalizeBackend(backend: ParserAdapterBackend): ParserAdapterBackend {
  return backend === "enterprise_ai_vlm" ? "oci_genai_vision" : backend;
}

function adapterFlagField(
  adapter: ParserAdapterBackendName
): ParserAdapterFlagField {
  return ADAPTER_FLAG_FIELDS[adapter];
}

function externalBackendFlagUpdate(
  backend: ParserAdapterBackend
): Partial<ParserAdapterForm> {
  if (isAdapterBackend(backend)) {
    return { [adapterFlagField(backend)]: true } as Partial<ParserAdapterForm>;
  }
  return {};
}

function serializeForm(form: ParserAdapterForm) {
  return JSON.stringify({
    adapter_backend: form.adapter_backend,
    docling_enabled: form.docling_enabled,
    unstructured_enabled: form.unstructured_enabled,
    mineru_enabled: form.mineru_enabled,
    dots_ocr_enabled: form.dots_ocr_enabled,
    connections: form.connections,
  });
}

function parserSettingsUpdate(form: ParserAdapterForm): ParserAdapterSettingsUpdate {
  return {
    adapter_backend: form.adapter_backend,
    docling_enabled: form.docling_enabled,
    unstructured_enabled: form.unstructured_enabled,
    mineru_enabled: form.mineru_enabled,
    dots_ocr_enabled: form.dots_ocr_enabled,
    connections: EXTERNAL_BACKENDS.map((backend) => {
      const connection = form.connections[backend];
      return {
        backend,
        endpoint: connection.endpoint.trim(),
        ...(backend === "mineru" ? {} : { model: connection.model.trim() }),
        ...(connection.api_key ? { api_key: connection.api_key } : {}),
        ...(connection.clear_api_key ? { clear_api_key: true } : {}),
      };
    }),
  };
}

function validateConnections(form: ParserAdapterForm): ConnectionFieldErrors {
  const errors: ConnectionFieldErrors = {};
  for (const backend of EXTERNAL_BACKENDS) {
    const connection = form.connections[backend];
    const endpoint = connection.endpoint.trim();
    if (endpoint) {
      try {
        const parsed = new URL(endpoint);
        if (
          !["http:", "https:"].includes(parsed.protocol) ||
          parsed.username ||
          parsed.password ||
          parsed.search ||
          parsed.hash
        ) {
          errors[`${backend}.endpoint`] = t("settings.parserAdapters.connection.invalidEndpoint");
        }
      } catch {
        errors[`${backend}.endpoint`] = t("settings.parserAdapters.connection.invalidEndpoint");
      }
    }
    if (endpoint && backend !== "mineru" && !connection.model.trim()) {
      errors[`${backend}.model`] = t("settings.parserAdapters.connection.modelRequired");
    }
  }
  return errors;
}

function focusConnectionError(key: string) {
  const [backend, field] = key.split(".");
  document.getElementById(`external-parser-${backend}-${field}`)?.focus();
}

function externalProtocol(backend: ExternalParserBackendName) {
  return backend === "mineru" ? "mineru_file_parse" : "openai_chat_completions";
}

function connectionProtocolLabel(protocol: string) {
  return protocol === "mineru_file_parse" ? "MinerU /file_parse" : "OpenAI /v1/chat/completions";
}

function externalEndpointPlaceholder(backend: ExternalParserBackendName) {
  return backend === "mineru" ? "https://parser.example.com" : "https://api.example.com/v1";
}

function defaultExternalModel(backend: ExternalParserBackendName) {
  if (backend === "dots_ocr") return "rednote-hilab/dots.mocr";
  return "";
}
