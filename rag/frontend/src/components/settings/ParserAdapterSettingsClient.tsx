"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  PackageX,
  Plug,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  SERVICE_PROFILE_ORDER,
  ServiceProfileBadge,
  ServiceStatusBadge,
  type DisplayRuntimeStatus,
} from "@/components/settings/ServicesManagementClient";
import {
  ApiError,
  type ParserAdapterBackend,
  type ParserAdapterBackendName,
  type ParserAdapterContractCaseData,
  type ParserAdapterContractData,
  type ParserAdapterContractStatus,
  type ParserAdapterSettingsData,
  type ParserServiceBackendData,
  type ParserServiceBackendName,
  type ServiceProfile,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import {
  findParserCapability,
  formatSupportedExtensions,
  formatSupportedFormats,
} from "@/lib/parser-capabilities";
import {
  useParserAdapterContract,
  useParserAdapterSettings,
  useServiceStatusQueries,
  useUpdateParserAdapterSettings,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

type ParserAdapterForm = {
  adapter_backend: ParserAdapterBackend;
  docling_enabled: boolean;
  marker_enabled: boolean;
  unstructured_enabled: boolean;
  unlimited_ocr_enabled: boolean;
  mineru_enabled: boolean;
  dots_ocr_enabled: boolean;
  glm_ocr_enabled: boolean;
};
type ParserAdapterFlagField = Exclude<keyof ParserAdapterForm, "adapter_backend">;

const ADAPTER_FLAG_FIELDS: Record<ParserAdapterBackendName, ParserAdapterFlagField> = {
  docling: "docling_enabled",
  marker: "marker_enabled",
  unstructured: "unstructured_enabled",
  unlimited_ocr: "unlimited_ocr_enabled",
  mineru: "mineru_enabled",
  dots_ocr: "dots_ocr_enabled",
  glm_ocr: "glm_ocr_enabled",
};

const SERVICE_BACKENDS: ParserServiceBackendName[] = [
  "oci_genai_vision",
  "oci_document_understanding",
];

const PARSER_BACKEND_SERVICE_IDS: Record<
  ParserAdapterBackendName | ParserServiceBackendName,
  string
> = {
  docling: "parser-docling",
  marker: "parser-marker",
  unstructured: "parser-unstructured",
  unlimited_ocr: "parser-unlimited-ocr",
  mineru: "parser-mineru",
  dots_ocr: "parser-dots-ocr",
  glm_ocr: "parser-glm-ocr",
  oci_genai_vision: "parser-oci-genai-vision",
  oci_document_understanding: "parser-oci-document-understanding",
};

function isAdapterBackend(backend: ParserAdapterBackend): backend is ParserAdapterBackendName {
  return backend in ADAPTER_FLAG_FIELDS;
}

function isServiceBackend(backend: ParserAdapterBackend): backend is ParserServiceBackendName {
  return (SERVICE_BACKENDS as ParserAdapterBackend[]).includes(backend);
}

/** Optional parser adapter の runtime 設定と readiness を管理する設定画面。 */
export function ParserAdapterSettingsClient() {
  const query = useParserAdapterSettings();
  const contractQuery = useParserAdapterContract();
  const save = useUpdateParserAdapterSettings();
  const [form, setForm] = useState<ParserAdapterForm | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  useEffect(() => {
    if (query.data && !save.isPending) {
      setForm(formFromSettings(query.data));
    }
  }, [query.data, save.isPending]);

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-40 w-full rounded-lg" />
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
              : t("settings.parserAdapters.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const settings = query.data;
  if (!settings || !form) return null;

  const dirty = serializeForm(form) !== serializeForm(formFromSettings(settings));
  const saveError =
    save.error instanceof ApiError ? save.error.message : t("settings.parserAdapters.saveError");
  const contractError =
    contractQuery.error instanceof ApiError
      ? contractQuery.error.message
      : t("settings.parserAdapters.contract.loadError");

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
    setSuccessMessage(null);
    setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form) return;
    save.mutate(form, {
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
    <div className="space-y-5 p-8">
      <OverviewCard
        dirty={dirty}
        form={form}
        settings={settings}
        saving={save.isPending}
        successMessage={successMessage}
        errorMessage={save.isError ? saveError : null}
        onBackendChange={selectBackend}
        onReset={resetForm}
        onSubmit={submit}
      />
      <details className="border-t border-border pt-4">
        <summary className="cursor-pointer text-sm font-semibold text-foreground">
          {t("settings.parserAdapters.diagnostics.title")}
        </summary>
        <div className="mt-4 space-y-5">
          <ParserAdapterContractCard
            data={contractQuery.data}
            checking={contractQuery.isFetching}
            errorMessage={contractQuery.isError ? contractError : null}
            hasFetched={contractQuery.isFetched}
            onRun={() => void contractQuery.refetch()}
          />
        </div>
      </details>
    </div>
  );
}

function OverviewCard({
  dirty,
  form,
  settings,
  saving,
  successMessage,
  errorMessage,
  onBackendChange,
  onReset,
  onSubmit,
}: {
  dirty: boolean;
  form: ParserAdapterForm;
  settings: ParserAdapterSettingsData;
  saving: boolean;
  successMessage: string | null;
  errorMessage: string | null;
  onBackendChange: (backend: ParserAdapterBackend) => void;
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
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
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
          <div className="text-sm font-medium text-foreground">
            {t("settings.parserAdapters.backend")}
          </div>
          <div
            role="radiogroup"
            aria-label={t("settings.parserAdapters.backend")}
            className="space-y-2"
          >
            {backendProfileGroups.map((group) => (
              <div
                key={group.profile}
                className="grid grid-cols-1 gap-2 md:grid-cols-3 lg:grid-cols-4"
              >
                {group.backends.map((backend) => {
                  const selected = form.adapter_backend === backend;
                  const service = isServiceBackend(backend)
                    ? serviceByName.get(backend)
                    : undefined;
                  const serviceId = serviceIdForBackend(backend);
                  const runtime = serviceId ? runtimeByServiceId.get(serviceId) : null;
                  const runtimeStatus = runtime?.status ?? null;
                  const runtimeProfile = runtime?.profile ?? serviceProfileForBackend(backend);
                  const capability = findParserCapability(settings.capabilities, backend);
                  const supportedFormats = formatSupportedFormats(capability);
                  const supportedExtensions = formatSupportedExtensions(capability);
                  return (
                    <button
                      key={backend}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      disabled={saving}
                      onClick={() => onBackendChange(backend)}
                      className={cn(
                        "min-h-[76px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                        selected
                          ? "border-primary bg-primary/10 text-foreground"
                          : "border-border bg-card text-foreground hover:bg-background"
                      )}
                    >
                      <span className="flex items-center gap-1.5">
                        <span className="text-sm font-semibold">{backendLabel(backend)}</span>
                        {runtimeProfile ? <ServiceProfileBadge profile={runtimeProfile} /> : null}
                      </span>
                      <span className="mt-1 block text-xs leading-relaxed text-muted">
                        {t(backendDescriptionKey(backend))}
                      </span>
                      {supportedFormats ? (
                        <span className="mt-1 block text-xs text-muted">
                          {t("settings.parserAdapters.capabilities")}: {supportedFormats}
                        </span>
                      ) : null}
                      {supportedExtensions ? (
                        <span className="mt-0.5 block break-words text-[11px] leading-4 text-muted">
                          {supportedExtensions}
                        </span>
                      ) : null}
                      <span className="mt-2 flex flex-wrap items-center gap-1.5">
                        {runtimeStatus ? <ServiceStatusBadge status={runtimeStatus} /> : null}
                        {service && !service.configured ? (
                          <span className="inline-flex items-center gap-1 rounded-sm bg-warning-bg px-1.5 py-0.5 text-[11px] font-medium text-warning whitespace-nowrap">
                            <AlertTriangle size={12} aria-hidden />
                            {t("settings.parserAdapters.serviceBackend.unconfigured")}
                          </span>
                        ) : null}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))}
          </div>
          {form.adapter_backend === "local" ? (
            <p className="text-xs leading-relaxed text-warning">
              {t("settings.parserAdapters.legacyBackendNotice")}
            </p>
          ) : null}
          <p className="text-xs leading-relaxed text-muted">
            {t("settings.parserAdapters.serviceBackend.note")}
          </p>
        </div>
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
              variant="secondary"
              onClick={onReset}
              disabled={!dirty || saving}
              aria-label={t("settings.parserAdapters.actions.reset")}
            >
              <RotateCcw size={15} aria-hidden />
              {t("settings.parserAdapters.actions.reset")}
            </Button>
            <Button
              type="button"
              loading={saving}
              disabled={!dirty}
              onClick={onSubmit}
              aria-label={t("settings.parserAdapters.actions.save")}
            >
              <Save size={15} aria-hidden />
              {saving
                ? t("settings.parserAdapters.actions.saving")
                : t("settings.parserAdapters.actions.save")}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function ParserAdapterContractCard({
  data,
  checking,
  errorMessage,
  hasFetched,
  onRun,
}: {
  data: ParserAdapterContractData | undefined;
  checking: boolean;
  errorMessage: string | null;
  hasFetched: boolean;
  onRun: () => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="flex items-start gap-3">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
              <ShieldCheck size={20} aria-hidden />
            </div>
            <div>
              <CardTitle>{t("settings.parserAdapters.contract.title")}</CardTitle>
              <CardDescription>
                {t("settings.parserAdapters.contract.description")}
              </CardDescription>
            </div>
          </div>
          <Button
            type="button"
            variant="secondary"
            loading={checking}
            onClick={onRun}
            aria-label={t("settings.parserAdapters.contract.run")}
            className="w-full md:w-auto"
          >
            <RefreshCw size={15} aria-hidden />
            {checking
              ? t("settings.parserAdapters.contract.running")
              : t("settings.parserAdapters.contract.run")}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {errorMessage ? <FormStatus tone="danger" message={errorMessage} /> : null}
        {!hasFetched && !checking ? (
          <FormStatus tone="info" message={t("settings.parserAdapters.contract.notRun")} />
        ) : null}
        {checking && !data ? (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
          </div>
        ) : null}
        {data ? <ParserAdapterContractResult data={data} /> : null}
      </CardContent>
    </Card>
  );
}

function ParserAdapterContractResult({ data }: { data: ParserAdapterContractData }) {
  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <div className="rounded-md border border-border bg-muted/20 p-3">
          <dt className="text-xs font-medium text-muted">
            {t("settings.parserAdapters.contract.judgement")}
          </dt>
          <dd className="mt-2">
            <ContractVerdict passed={data.passed} />
          </dd>
        </div>
        <RuntimeFact
          label={t("settings.parserAdapters.contract.caseCount")}
          value={String(data.case_count)}
        />
        <RuntimeFact
          label={t("settings.parserAdapters.contract.blockingFailureCount")}
          value={String(data.blocking_failure_count)}
        />
        <RuntimeFact
          label={t("settings.parserAdapters.contract.passedSources")}
          value={
            data.summary.passed_source_kinds.length
              ? data.summary.passed_source_kinds.map(sourceKindLabel).join(", ")
            : t("settings.parserAdapters.routes.noMissing")
          }
        />
      </dl>
      <ContractCodeSummary data={data} />
      <ContractBackendMatrix data={data} />
      <ContractCaseTable cases={data.cases} />
    </div>
  );
}

function ContractVerdict({ passed }: { passed: boolean }) {
  const Icon = passed ? CheckCircle2 : PackageX;
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center gap-1.5 rounded-md px-2 text-xs font-semibold",
        passed ? "bg-success-bg text-success" : "bg-danger-bg text-danger"
      )}
    >
      <Icon size={14} aria-hidden />
      {passed
        ? t("settings.parserAdapters.contract.passed")
        : t("settings.parserAdapters.contract.failed")}
    </span>
  );
}

function ContractCodeSummary({ data }: { data: ParserAdapterContractData }) {
  return (
    <div
      className="grid grid-cols-1 gap-3 md:grid-cols-3"
      aria-label={t("settings.parserAdapters.contract.codeSummary")}
    >
      <CodeCountPanel
        title={t("settings.parserAdapters.contract.blockingReasons")}
        counts={data.summary.blocking_failure_reason_counts}
        labelForCode={contractReasonLabel}
      />
      <CodeCountPanel
        title={t("settings.parserAdapters.contract.warningCodes")}
        counts={data.summary.warning_code_counts}
        labelForCode={routeWarningLabel}
      />
      <CodeCountPanel
        title={t("settings.parserAdapters.contract.reasonCodes")}
        counts={data.summary.reason_code_counts}
        labelForCode={contractReasonLabel}
      />
    </div>
  );
}

function CodeCountPanel({
  title,
  counts,
  labelForCode,
}: {
  title: string;
  counts: Record<string, number>;
  labelForCode: (code: string) => string;
}) {
  const entries = Object.entries(counts)
    .filter(([, count]) => count > 0)
    .sort(([leftCode, leftCount], [rightCode, rightCount]) => {
      if (rightCount !== leftCount) return rightCount - leftCount;
      return labelForCode(leftCode).localeCompare(labelForCode(rightCode), "ja");
    });
  return (
    <section className="rounded-md border border-border bg-muted/20 p-3">
      <h3 className="text-xs font-medium text-muted">{title}</h3>
      {entries.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {entries.map(([code, count]) => (
            <span
              key={code}
              className="inline-flex min-h-6 items-center rounded-md bg-card px-2 text-xs font-medium text-foreground ring-1 ring-border"
              title={code}
            >
              {labelForCode(code)}
              <span className="ml-1 font-semibold text-muted"> {count}</span>
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-sm text-foreground">
          {t("settings.parserAdapters.contract.noCodes")}
        </p>
      )}
    </section>
  );
}

function ContractBackendMatrix({ data }: { data: ParserAdapterContractData }) {
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
      {data.backends.map((backend) => {
        const sourceStatus = data.summary.backend_source_status[backend] ?? {};
        const statusCounts = data.summary.backend_status_counts[backend] ?? {};
        return (
          <div key={backend} className="rounded-md border border-border bg-muted/20 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="text-sm font-semibold text-foreground">
                {adapterLabel(backend)}
              </div>
              <div className="text-xs text-muted">
                {formatStatusCounts(statusCounts)}
              </div>
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {Object.entries(sourceStatus).map(([sourceKind, status]) => (
                <span
                  key={`${backend}-${sourceKind}`}
                  className={cn(
                    "inline-flex min-h-6 items-center gap-1 rounded-md px-2 text-xs font-medium",
                    contractStatusToneClass(status as ParserAdapterContractStatus)
                  )}
                >
                  {sourceKindLabel(sourceKind)}
                  <span className="text-[11px] opacity-80">
                    {contractStatusLabel(status)}
                  </span>
                </span>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ContractCaseTable({ cases }: { cases: ParserAdapterContractCaseData[] }) {
  if (!cases.length) {
    return <FormStatus tone="warning" message={t("settings.parserAdapters.contract.empty")} />;
  }
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="hidden border-b border-border bg-muted/20 text-xs font-medium text-muted md:grid md:grid-cols-[0.85fr_0.65fr_0.8fr_1.25fr_1.05fr_1.25fr]">
        <div className="px-3 py-2">{t("settings.parserAdapters.adapter")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.routes.sourceKind")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.status")}</div>
        <div className="px-3 py-2">
          {t("settings.parserAdapters.contract.runtimeEvidence")}
        </div>
        <div className="px-3 py-2">{t("settings.parserAdapters.contract.schemaCounts")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.warning")}</div>
      </div>
      <ul className="divide-y divide-border">
        {cases.map((contractCase) => (
          <ContractCaseRow
            key={`${contractCase.backend}-${contractCase.source_kind}`}
            contractCase={contractCase}
          />
        ))}
      </ul>
    </div>
  );
}

function ContractCaseRow({
  contractCase,
}: {
  contractCase: ParserAdapterContractCaseData;
}) {
  return (
    <li className="grid grid-cols-1 gap-3 px-0 py-4 md:grid-cols-[0.85fr_0.65fr_0.8fr_1.25fr_1.05fr_1.25fr] md:gap-0 md:py-0">
      <RowCell label={t("settings.parserAdapters.adapter")}>
        <div className="text-sm font-medium text-foreground">
          {adapterLabel(contractCase.backend)}
        </div>
        <div className="break-words text-xs text-muted">
          {contractCase.parser_backend ?? contractCase.fixture_name}
        </div>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.routes.sourceKind")}>
        <span className="inline-flex min-h-6 items-center rounded-md bg-muted px-2 text-xs font-semibold text-foreground">
          {sourceKindLabel(contractCase.source_kind)}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.status")}>
        <ContractStatusPill status={contractCase.status} blocking={contractCase.blocking} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.contract.runtimeEvidence")}>
        <ContractRuntimeEvidence contractCase={contractCase} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.contract.schemaCounts")}>
        <span className="break-words text-sm text-foreground">
          {formatContractCounts(contractCase)}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.warning")}>
        <ContractCodeList contractCase={contractCase} />
      </RowCell>
    </li>
  );
}

function ContractRuntimeEvidence({
  contractCase,
}: {
  contractCase: ParserAdapterContractCaseData;
}) {
  return (
    <div className="space-y-1 text-xs text-muted">
      <EvidenceLine
        label={t("settings.parserAdapters.contract.packageEvidence")}
        value={formatPackageEvidence(contractCase)}
      />
      <EvidenceLine
        label={t("settings.parserAdapters.contract.parserEvidence")}
        value={formatParserEvidence(contractCase)}
      />
      <EvidenceLine
        label={t("settings.parserAdapters.contract.fixtureEvidence")}
        value={contractCase.fixture_name}
      />
    </div>
  );
}

function EvidenceLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[4.8rem_minmax(0,1fr)] gap-2">
      <span className="text-muted">{label}</span>
      <span className="break-words font-medium text-foreground">{value}</span>
    </div>
  );
}

function ContractStatusPill({
  status,
  blocking,
}: {
  status: ParserAdapterContractStatus;
  blocking: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center rounded-md px-2 text-xs font-semibold",
        contractStatusToneClass(status)
      )}
    >
      {contractStatusLabel(status)}
      {blocking ? ` / ${t("settings.parserAdapters.contract.blocking")}` : ""}
    </span>
  );
}

function ContractCodeList({
  contractCase,
}: {
  contractCase: ParserAdapterContractCaseData;
}) {
  const labels = [
    ...contractCase.warning_codes.map(routeWarningLabel),
    ...contractCase.reason_codes.map(contractReasonLabel),
  ].filter(Boolean);
  if (!labels.length) {
    return <span className="text-sm text-foreground">{t("settings.parserAdapters.noWarning")}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {labels.map((label, index) => (
        <span
          key={`${contractCase.backend}-${contractCase.source_kind}-${label}-${index}`}
          className="inline-flex min-h-6 items-center rounded-md bg-muted px-2 text-xs font-medium text-foreground"
        >
          {label}
        </span>
      ))}
    </div>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <dt className="text-xs font-medium text-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function RowCell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 px-3 md:py-3">
      <div className="mb-1 text-xs font-medium text-muted md:hidden">{label}</div>
      {children}
    </div>
  );
}

function adapterLabel(adapter: ParserAdapterBackendName) {
  if (adapter === "docling") return "Docling";
  if (adapter === "marker") return "Marker";
  if (adapter === "unstructured") return "Unstructured";
  if (adapter === "unlimited_ocr") return "Unlimited-OCR";
  if (adapter === "mineru") return "MinerU";
  if (adapter === "dots_ocr") return "Dots.OCR";
  return "GLM-OCR";
}

function backendLabel(backend: ParserAdapterBackend) {
  if (backend === "local") return t("settings.parserAdapters.backend.local");
  if (backend === "unlimited_ocr") return "Unlimited-OCR";
  if (backend === "mineru") return "MinerU";
  if (backend === "dots_ocr") return "Dots.OCR";
  if (backend === "glm_ocr") return "GLM-OCR";
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
  if (normalized === "local") return null;
  if (isAdapterBackend(normalized) || isServiceBackend(normalized)) {
    return PARSER_BACKEND_SERVICE_IDS[normalized];
  }
  return null;
}

function serviceProfileForBackend(backend: ParserAdapterBackend): ServiceProfile | null {
  const normalized = normalizeBackend(backend);
  if (
    normalized === "unlimited_ocr" ||
    normalized === "mineru" ||
    normalized === "dots_ocr" ||
    normalized === "glm_ocr"
  ) {
    return "gpu";
  }
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

function sourceKindLabel(sourceKind: string) {
  if (isKnownSourceKind(sourceKind)) {
    return t(`settings.parserAdapters.sourceKind.${sourceKind}` as I18nKey);
  }
  return sourceKind;
}

function isKnownSourceKind(sourceKind: string) {
  return ["pdf", "image", "office", "html", "email", "audio", "text", "unknown"].includes(
    sourceKind
  );
}

function routeWarningLabel(code: string) {
  if (!code) return "";
  if (code === "adapter_package_missing") {
    return t("settings.parserAdapters.warning.adapter_package_missing");
  }
  if (code === "adapter_feature_flag_disabled") {
    return t("settings.parserAdapters.warning.adapter_feature_flag_disabled");
  }
  if (code === "adapter_flag_ignored_by_backend") {
    return t("settings.parserAdapters.warning.adapter_flag_ignored_by_backend");
  }
  if (code === "unsupported_audio") {
    return t("settings.parserAdapters.warning.unsupported_audio");
  }
  if (code === "audio_transcription_not_configured") {
    return t("settings.parserAdapters.warning.audio_transcription_not_configured");
  }
  if (code.endsWith("_adapter_source_unsupported")) {
    return t("settings.parserAdapters.warning.adapter_source_unsupported");
  }
  if (code.endsWith("_adapter_feature_flag_disabled")) {
    return t("settings.parserAdapters.warning.adapter_feature_flag_disabled");
  }
  if (code.endsWith("_adapter_package_missing")) {
    return t("settings.parserAdapters.warning.adapter_package_missing");
  }
  if (code.endsWith("_adapter_flag_ignored_by_backend")) {
    return t("settings.parserAdapters.warning.adapter_flag_ignored_by_backend");
  }
  return code;
}

function routeReasonLabel(code: string) {
  if (!code) return "";
  const knownReasonKeys = [
    "local_parser_preferred_for_source",
    "audio_transcription_not_configured",
    "local_backend_selected",
    "selected_adapter_supported_for_source",
    "selected_adapter_unsupported_for_source",
    "active_adapter_available_for_source",
    "adapter_attempt_requires_fallback",
  ];
  if (knownReasonKeys.includes(code)) {
    return t(`settings.parserAdapters.reason.${code}` as I18nKey);
  }
  return code;
}

function contractReasonLabel(code: string) {
  if (!code) return "";
  const knownReasonKeys = [
    "adapter_active",
    "adapter_available",
    "adapter_disabled",
    "adapter_failed",
    "adapter_fallback_used",
    "adapter_ignored",
    "adapter_import_name_missing",
    "adapter_distribution_name_missing",
    "adapter_missing",
    "adapter_not_routed_for_source",
    "adapter_package_version_missing",
    "fixture_missing",
    "schema_remap_contract_ok",
    "schema_remap_empty",
  ];
  if (knownReasonKeys.includes(code)) {
    return t(`settings.parserAdapters.contract.reason.${code}` as I18nKey);
  }
  return routeReasonLabel(code) || code;
}

function contractStatusLabel(status: string) {
  if (isKnownContractStatus(status)) {
    return t(`settings.parserAdapters.contract.status.${status}` as I18nKey);
  }
  return status;
}

function isKnownContractStatus(status: string): status is ParserAdapterContractStatus {
  return [
    "passed",
    "failed",
    "fallback",
    "available",
    "ignored",
    "disabled",
    "missing",
    "unsupported",
    "fixture_missing",
  ].includes(status);
}

function contractStatusToneClass(status: ParserAdapterContractStatus | string) {
  if (status === "passed") return "bg-success-bg text-success";
  if (status === "failed" || status === "fallback" || status === "missing") {
    return "bg-danger-bg text-danger";
  }
  if (status === "disabled" || status === "fixture_missing") {
    return "bg-warning-bg text-warning";
  }
  if (status === "ignored" || status === "unsupported") {
    return "bg-warning-bg text-warning";
  }
  if (status === "available") return "bg-info-bg text-info";
  return "bg-muted text-foreground";
}

function formatContractCounts(contractCase: ParserAdapterContractCaseData) {
  return [
    t("settings.parserAdapters.contract.count.elements", {
      count: contractCase.element_count,
    }),
    t("settings.parserAdapters.contract.count.pages", {
      count: contractCase.page_count,
    }),
    t("settings.parserAdapters.contract.count.tables", {
      count: contractCase.table_count,
    }),
    t("settings.parserAdapters.contract.count.cells", {
      count: contractCase.table_cell_count,
    }),
    t("settings.parserAdapters.contract.count.assets", {
      count: contractCase.asset_count,
    }),
    t("settings.parserAdapters.contract.count.bbox", {
      count: contractCase.bbox_count,
    }),
  ].join(" / ");
}

function formatPackageEvidence(contractCase: ParserAdapterContractCaseData) {
  if (contractCase.adapter_distribution_name && contractCase.adapter_package_version) {
    return `${contractCase.adapter_distribution_name} ${contractCase.adapter_package_version}`;
  }
  if (contractCase.adapter_import_name && contractCase.adapter_package_version) {
    return `${contractCase.adapter_import_name} ${contractCase.adapter_package_version}`;
  }
  if (contractCase.adapter_import_name) {
    return contractCase.adapter_import_name;
  }
  return t("settings.parserAdapters.contract.noPackageEvidence");
}

function formatParserEvidence(contractCase: ParserAdapterContractCaseData) {
  if (contractCase.parser_backend && contractCase.parser_version) {
    return `${contractCase.parser_backend} ${contractCase.parser_version}`;
  }
  return contractCase.parser_backend ?? t("settings.parserAdapters.contract.noParserEvidence");
}

function formatStatusCounts(statusCounts: Partial<Record<string, number>>) {
  const labels = Object.entries(statusCounts)
    .filter(([, count]) => Boolean(count))
    .map(([status, count]) => `${contractStatusLabel(status)} ${count}`);
  return labels.length ? labels.join(" / ") : t("settings.parserAdapters.noWarning");
}

function formFromSettings(settings: ParserAdapterSettingsData): ParserAdapterForm {
  const enabledByBackend = new Map(
    settings.adapters.map((adapter) => [adapter.backend, adapter.enabled])
  );
  return {
    adapter_backend: normalizeBackend(settings.adapter_backend),
    docling_enabled: enabledByBackend.get("docling") ?? false,
    marker_enabled: enabledByBackend.get("marker") ?? false,
    unstructured_enabled: enabledByBackend.get("unstructured") ?? false,
    unlimited_ocr_enabled: enabledByBackend.get("unlimited_ocr") ?? false,
    mineru_enabled: enabledByBackend.get("mineru") ?? false,
    dots_ocr_enabled: enabledByBackend.get("dots_ocr") ?? false,
    glm_ocr_enabled: enabledByBackend.get("glm_ocr") ?? false,
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
    marker_enabled: form.marker_enabled,
    unstructured_enabled: form.unstructured_enabled,
    unlimited_ocr_enabled: form.unlimited_ocr_enabled,
    mineru_enabled: form.mineru_enabled,
    dots_ocr_enabled: form.dots_ocr_enabled,
    glm_ocr_enabled: form.glm_ocr_enabled,
  });
}
