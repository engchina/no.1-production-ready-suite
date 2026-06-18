"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  PackageCheck,
  PackageX,
  Plug,
  RefreshCw,
  RotateCcw,
  Route,
  Save,
  ShieldCheck,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  ApiError,
  type ParserAdapterBackend,
  type ParserAdapterBackendName,
  type ParserAdapterBackendSourceMatrixData,
  type ParserAdapterContractCaseData,
  type ParserAdapterContractData,
  type ParserAdapterContractStatus,
  type ParserAdapterScoreBackend,
  type ParserAdapterSettingsData,
  type ParserAdapterSourceRouteData,
  type ParserAdapterStatus,
  type ParserAdapterStatusData,
  type ParserServiceBackendData,
  type ParserServiceBackendName,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useParserAdapterContract,
  useParserAdapterSettings,
  useUpdateParserAdapterSettings,
} from "@/lib/queries";
import { cn } from "@/lib/utils";

type ParserAdapterForm = {
  adapter_backend: ParserAdapterBackend;
  docling_enabled: boolean;
  marker_enabled: boolean;
  unstructured_enabled: boolean;
};

const BACKEND_OPTIONS: ParserAdapterBackend[] = [
  "local",
  "auto",
  "docling",
  "marker",
  "unstructured",
  // service 系 backend(OCI クラウドサービス直呼び。package readiness 対象外)。
  "enterprise_ai_vlm",
  "oci_document_understanding",
];

const SERVICE_BACKENDS: ParserServiceBackendName[] = [
  "enterprise_ai_vlm",
  "oci_document_understanding",
];

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

  function updateAdapterFlag(adapter: ParserAdapterBackendName, enabled: boolean) {
    updateForm({ [adapterFlagField(adapter)]: enabled });
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
      <AdapterReadinessCard
        adapters={settings.adapters}
        form={form}
        saving={save.isPending}
        onFlagChange={updateAdapterFlag}
      />
      <ParserAdapterContractCard
        data={contractQuery.data}
        checking={contractQuery.isFetching}
        errorMessage={contractQuery.isError ? contractError : null}
        hasFetched={contractQuery.isFetched}
        onRun={() => void contractQuery.refetch()}
      />
      <SourceRoutingCard settings={settings} />
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
    () =>
      BACKEND_OPTIONS.map((backend) => ({
        backend,
        label: backendLabel(backend),
        description: t(backendDescriptionKey(backend)),
      })),
    []
  );
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
            className="grid grid-cols-1 gap-2 md:grid-cols-3 lg:grid-cols-4"
          >
            {backendOptions.map((option) => {
              const selected = form.adapter_backend === option.backend;
              const service = isServiceBackend(option.backend)
                ? serviceByName.get(option.backend)
                : undefined;
              return (
                <button
                  key={option.backend}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  disabled={saving}
                  onClick={() => onBackendChange(option.backend)}
                  className={cn(
                    "min-h-[76px] rounded-md border px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
                    selected
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border bg-card text-foreground hover:bg-background"
                  )}
                >
                  <span className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold">{option.label}</span>
                    {service ? (
                      <span
                        className="inline-flex items-center gap-1 rounded-sm bg-info-bg px-1.5 py-0.5 text-[10px] font-medium text-info"
                        title={t("settings.parserAdapters.serviceBackend.tag")}
                      >
                        <Cloud size={11} aria-hidden />
                        {t("settings.parserAdapters.serviceBackend.tag")}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-1 block text-xs leading-relaxed text-muted">
                    {option.description}
                  </span>
                  {service && !service.configured ? (
                    <span className="mt-1.5 inline-flex items-center gap-1 rounded-sm bg-warning-bg px-1.5 py-0.5 text-[11px] font-medium text-warning">
                      <AlertTriangle size={12} aria-hidden />
                      {t("settings.parserAdapters.serviceBackend.unconfigured")}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
          <p className="text-xs leading-relaxed text-muted">
            {t("settings.parserAdapters.serviceBackend.note")}
          </p>
        </div>
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RuntimeFact
            label={t("settings.parserAdapters.backend")}
            value={settings.adapter_backend}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.effectiveOrder")}
            value={formatEffectiveOrder(settings.effective_order)}
          />
          <RuntimeFact label={t("settings.parserAdapters.source")} value={settings.config_source} />
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

function AdapterReadinessCard({
  adapters,
  form,
  saving,
  onFlagChange,
}: {
  adapters: ParserAdapterStatusData[];
  form: ParserAdapterForm;
  saving: boolean;
  onFlagChange: (adapter: ParserAdapterBackendName, enabled: boolean) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-success-bg text-success">
            <Route size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.adapters.title")}</CardTitle>
            <CardDescription>
              {t("settings.parserAdapters.adapters.description")}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="hidden border-y border-border text-xs font-medium text-muted md:grid md:grid-cols-[1.1fr_0.8fr_1fr_0.8fr_0.8fr_1fr]">
          <div className="px-3 py-2">{t("settings.parserAdapters.adapter")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.flag")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.package")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.role")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.status")}</div>
          <div className="px-3 py-2">{t("settings.parserAdapters.warning")}</div>
        </div>
        <ul className="divide-y divide-border">
          {adapters.map((adapter) => (
            <AdapterRow
              key={adapter.backend}
              adapter={adapter}
              checked={adapterFlagValue(form, adapter.backend)}
              disabled={saving}
              onFlagChange={onFlagChange}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function AdapterRow({
  adapter,
  checked,
  disabled,
  onFlagChange,
}: {
  adapter: ParserAdapterStatusData;
  checked: boolean;
  disabled: boolean;
  onFlagChange: (adapter: ParserAdapterBackendName, enabled: boolean) => void;
}) {
  return (
    <li className="grid grid-cols-1 gap-3 py-4 md:grid-cols-[1.1fr_0.8fr_1fr_0.8fr_0.8fr_1fr] md:gap-0 md:py-0">
      <RowCell label={t("settings.parserAdapters.adapter")}>
        <div className="font-medium text-foreground">{adapterLabel(adapter.backend)}</div>
        <div className="text-xs text-muted">{adapter.package_name}</div>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.flag")}>
        <div className="flex items-center gap-2">
          <Switch
            checked={checked}
            disabled={disabled}
            onCheckedChange={(enabled) => onFlagChange(adapter.backend, enabled)}
            aria-label={t("settings.parserAdapters.flagToggleAria", {
              adapter: adapterLabel(adapter.backend),
            })}
          />
          <span className="text-sm text-foreground">
            {checked
              ? t("settings.parserAdapters.enabled")
              : t("settings.parserAdapters.disabled")}
          </span>
        </div>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.package")}>
        <PackageState adapter={adapter} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.role")}>
        <span className="text-sm text-foreground">
          {adapter.selected
            ? t("settings.parserAdapters.selected")
            : t("settings.parserAdapters.notSelected")}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.status")}>
        <StatusPill status={adapter.status} />
      </RowCell>
      <RowCell label={t("settings.parserAdapters.warning")}>
        <span className="break-words text-sm text-foreground">
          {warningLabel(adapter.warning_code)}
        </span>
      </RowCell>
    </li>
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

function SourceRoutingCard({ settings }: { settings: ParserAdapterSettingsData }) {
  const matrix = settings.backend_source_kind_matrix;
  const routes = settings.source_routes.length
    ? settings.source_routes
    : matrix.route_evidence;
  const missingSourceKinds = matrix.missing_source_kinds.map(sourceKindLabel);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
            <Route size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.routes.title")}</CardTitle>
            <CardDescription>
              {t("settings.parserAdapters.routes.description")}
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <RuntimeFact
            label={t("settings.parserAdapters.routes.required")}
            value={String(matrix.required_source_kinds.length)}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.routes.covered")}
            value={String(matrix.covered_source_kinds.length)}
          />
          <RuntimeFact
            label={t("settings.parserAdapters.routes.missing")}
            value={
              missingSourceKinds.length
                ? missingSourceKinds.join(", ")
                : t("settings.parserAdapters.routes.noMissing")
            }
          />
        </dl>
        <BackendSourceSummary matrix={matrix} />
        {routes.length ? (
          <SourceRouteTable routes={routes} />
        ) : (
          <FormStatus tone="warning" message={t("settings.parserAdapters.routes.empty")} />
        )}
      </CardContent>
    </Card>
  );
}

function BackendSourceSummary({
  matrix,
}: {
  matrix: ParserAdapterBackendSourceMatrixData;
}) {
  const entries = Object.entries(matrix.backend_source_kinds).filter(
    (entry): entry is [ParserAdapterScoreBackend, string[]] => Boolean(entry[1]?.length)
  );
  if (!entries.length) return null;
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
      {entries.map(([backend, sourceKinds]) => (
        <div key={backend} className="rounded-md border border-border bg-muted/20 p-3">
          <div className="text-xs font-medium text-muted">{backendLabel(backend)}</div>
          <div className="mt-1 break-words text-sm font-semibold text-foreground">
            {sourceKinds.map(sourceKindLabel).join(", ")}
          </div>
        </div>
      ))}
    </div>
  );
}

function SourceRouteTable({ routes }: { routes: ParserAdapterSourceRouteData[] }) {
  return (
    <div className="overflow-hidden rounded-md border border-border">
      <div className="hidden border-b border-border bg-muted/20 text-xs font-medium text-muted md:grid md:grid-cols-[0.7fr_1.15fr_1.15fr_1fr_1.4fr]">
        <div className="px-3 py-2">{t("settings.parserAdapters.routes.sourceKind")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.routes.candidateOrder")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.routes.attemptedOrder")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.routes.selectedBackend")}</div>
        <div className="px-3 py-2">{t("settings.parserAdapters.warning")}</div>
      </div>
      <ul className="divide-y divide-border">
        {routes.map((route) => (
          <SourceRouteRow key={route.source_kind} route={route} />
        ))}
      </ul>
    </div>
  );
}

function SourceRouteRow({ route }: { route: ParserAdapterSourceRouteData }) {
  return (
    <li className="grid grid-cols-1 gap-3 px-0 py-4 md:grid-cols-[0.7fr_1.15fr_1.15fr_1fr_1.4fr] md:gap-0 md:py-0">
      <RowCell label={t("settings.parserAdapters.routes.sourceKind")}>
        <span className="inline-flex min-h-6 items-center rounded-md bg-muted px-2 text-xs font-semibold text-foreground">
          {sourceKindLabel(route.source_kind)}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.routes.candidateOrder")}>
        <span className="break-words text-sm text-foreground">
          {formatBackendOrder(route.candidate_order)}
        </span>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.routes.attemptedOrder")}>
        <span className="break-words text-sm text-foreground">
          {formatBackendOrder(route.attempted_order)}
        </span>
        {route.active_order.length ? (
          <div className="mt-1 break-words text-xs text-muted">
            {t("settings.parserAdapters.routes.activeOrder")}:{" "}
            {formatBackendOrder(route.active_order)}
          </div>
        ) : null}
      </RowCell>
      <RowCell label={t("settings.parserAdapters.routes.selectedBackend")}>
        <StatusPillLike>{backendLabel(route.selected_backend)}</StatusPillLike>
      </RowCell>
      <RowCell label={t("settings.parserAdapters.warning")}>
        <RouteCodeList route={route} />
      </RowCell>
    </li>
  );
}

function RouteCodeList({ route }: { route: ParserAdapterSourceRouteData }) {
  const warnings = route.warning_codes.map(routeWarningLabel);
  const reasons = route.reason_codes.map(routeReasonLabel);
  const labels = [...warnings, ...reasons].filter(Boolean);
  if (!labels.length) {
    return <span className="text-sm text-foreground">{t("settings.parserAdapters.noWarning")}</span>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {labels.map((label, index) => (
        <span
          key={`${label}-${index}`}
          className="inline-flex min-h-6 items-center rounded-md bg-muted px-2 text-xs font-medium text-foreground"
        >
          {label}
        </span>
      ))}
    </div>
  );
}

function StatusPillLike({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex min-h-6 items-center rounded-md bg-info-bg px-2 text-xs font-semibold text-info">
      {children}
    </span>
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

function PackageState({ adapter }: { adapter: ParserAdapterStatusData }) {
  const Icon = adapter.installed ? PackageCheck : PackageX;
  return (
    <div className="space-y-1">
      <span
        className={cn(
          "inline-flex min-h-6 items-center gap-1.5 rounded-md px-2 text-xs font-medium",
          adapter.installed ? "bg-success-bg text-success" : "bg-warning-bg text-warning"
        )}
      >
        <Icon size={14} aria-hidden />
        {adapter.installed
          ? t("settings.parserAdapters.installed")
          : t("settings.parserAdapters.notInstalled")}
      </span>
      {adapter.version ? (
        <div className="text-xs text-muted">
          {adapter.distribution_name ? `${adapter.distribution_name} ` : ""}
          {t("settings.parserAdapters.version", { version: adapter.version })}
        </div>
      ) : null}
      <div className="break-words text-xs text-muted">
        {t("settings.parserAdapters.importName", { name: adapter.import_name })}
      </div>
      {!adapter.installed ? (
        <div className="break-words text-xs text-muted">
          {t("settings.parserAdapters.installHint", { package: adapter.install_package })}
        </div>
      ) : null}
    </div>
  );
}

function StatusPill({ status }: { status: ParserAdapterStatus }) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center rounded-md px-2 text-xs font-semibold",
        statusToneClass(status)
      )}
    >
      {t(statusLabelKey(status))}
    </span>
  );
}

function adapterLabel(adapter: ParserAdapterBackendName) {
  if (adapter === "docling") return "Docling";
  if (adapter === "marker") return "Marker";
  return "Unstructured";
}

function backendLabel(backend: ParserAdapterBackend) {
  if (backend === "local") return t("settings.parserAdapters.backend.local");
  if (backend === "auto") return t("settings.parserAdapters.backend.auto");
  if (backend === "enterprise_ai_vlm")
    return t("settings.parserAdapters.backend.enterprise_ai_vlm");
  if (backend === "oci_document_understanding")
    return t("settings.parserAdapters.backend.oci_document_understanding");
  return adapterLabel(backend);
}

function backendDescriptionKey(backend: ParserAdapterBackend): I18nKey {
  return `settings.parserAdapters.backend.${backend}.description` as I18nKey;
}

function formatEffectiveOrder(order: ParserAdapterBackendName[]) {
  if (!order.length) return t("settings.parserAdapters.noEffectiveOrder");
  return order.map(adapterLabel).join(" -> ");
}

function formatBackendOrder(order: ParserAdapterScoreBackend[]) {
  if (!order.length) return t("settings.parserAdapters.routes.noAdapter");
  return order.map(backendLabel).join(" -> ");
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
    "source_aware_auto_order",
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

function statusLabelKey(status: ParserAdapterStatus): I18nKey {
  return `settings.parserAdapters.status.${status}` as I18nKey;
}

function statusToneClass(status: ParserAdapterStatus) {
  if (status === "active") return "bg-success-bg text-success";
  if (status === "missing") return "bg-danger-bg text-danger";
  if (status === "ignored") return "bg-warning-bg text-warning";
  if (status === "available") return "bg-info-bg text-info";
  return "bg-muted text-foreground";
}

function warningLabel(code: string | null) {
  if (!code) return t("settings.parserAdapters.noWarning");
  if (code === "adapter_feature_flag_disabled") {
    return t("settings.parserAdapters.warning.adapter_feature_flag_disabled");
  }
  if (code === "adapter_package_missing") {
    return t("settings.parserAdapters.warning.adapter_package_missing");
  }
  if (code === "adapter_flag_ignored_by_backend") {
    return t("settings.parserAdapters.warning.adapter_flag_ignored_by_backend");
  }
  return code;
}

function formFromSettings(settings: ParserAdapterSettingsData): ParserAdapterForm {
  const enabledByBackend = new Map(
    settings.adapters.map((adapter) => [adapter.backend, adapter.enabled])
  );
  return {
    adapter_backend: settings.adapter_backend,
    docling_enabled: enabledByBackend.get("docling") ?? false,
    marker_enabled: enabledByBackend.get("marker") ?? false,
    unstructured_enabled: enabledByBackend.get("unstructured") ?? false,
  };
}

function adapterFlagField(
  adapter: ParserAdapterBackendName
): "docling_enabled" | "marker_enabled" | "unstructured_enabled" {
  if (adapter === "docling") return "docling_enabled";
  if (adapter === "marker") return "marker_enabled";
  return "unstructured_enabled";
}

function adapterFlagValue(form: ParserAdapterForm, adapter: ParserAdapterBackendName) {
  return form[adapterFlagField(adapter)];
}

function externalBackendFlagUpdate(
  backend: ParserAdapterBackend
): Partial<ParserAdapterForm> {
  if (backend === "docling") return { docling_enabled: true };
  if (backend === "marker") return { marker_enabled: true };
  if (backend === "unstructured") return { unstructured_enabled: true };
  return {};
}

function serializeForm(form: ParserAdapterForm) {
  return JSON.stringify({
    adapter_backend: form.adapter_backend,
    docling_enabled: form.docling_enabled,
    marker_enabled: form.marker_enabled,
    unstructured_enabled: form.unstructured_enabled,
  });
}
