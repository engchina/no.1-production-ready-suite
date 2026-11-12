"use client";

import {
  PageBody,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Button,
  FormActionBar,
  FormStatus,
  TimedLoadingState,
  FormSkeleton,
  RoadmapBadge,
} from "@production-ready/ui";
import { useMemo, useState } from "react";
import {
  Plug,
  RotateCcw,
  Save,
  Settings,
} from "lucide-react";

import { ApiErrorState } from "@/components/StateViews";
import {
  ServiceProfileBadge,
  ServiceStatusBadge,
  type DisplayRuntimeStatus,
} from "@/components/settings/ServicesManagementClient";
import {
  ApiError,
  type ExternalParserBackendName,
  type ExternalParserConnectionData,
  type ParserAdapterBackend,
  type ParserAdapterBackendName,
  type ParserAdapterSettingsData,
  type ParserAdapterSettingsUpdate,
  type ParserServiceBackendData,
  type ParserServiceBackendName,
  type ServiceProfile,
} from "@/lib/api";
import { sortParserBackends } from "@/lib/parser-backend-order";
import { useLeaveGuard } from "@/lib/leave-guard";
import { t, type I18nKey } from "@/lib/i18n";
import {
  findParserCapability,
  formatSupportedExtensions,
  formatSupportedFormats,
} from "@/lib/parser-capabilities";
import {
  useParserAdapterSettings,
  useServiceStatusQueries,
  useUpdateParserAdapterSettings,
} from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  ConnectionConfiguredBadge,
  ExternalConnectionSheet,
  externalConnectionSheetId,
} from "./ExternalConnectionSheet";
import { PostParseSettingsCard } from "./PostParseSettingsCard";

type ParserAdapterForm = {
  adapter_backend: ParserAdapterBackend;
  docling_enabled: boolean;
  unstructured_enabled: boolean;
  dots_ocr_enabled: boolean;
  structured_records_jsonl_enabled: boolean;
  xml_tree_json_enabled: boolean;
};
type ParserAdapterFlagField = Exclude<keyof ParserAdapterForm, "adapter_backend">;

const EXTERNAL_BACKENDS: ExternalParserBackendName[] = ["dots_ocr"];

const ADAPTER_FLAG_FIELDS: Record<ParserAdapterBackendName, ParserAdapterFlagField> = {
  docling: "docling_enabled",
  unstructured: "unstructured_enabled",
  structured_records_jsonl: "structured_records_jsonl_enabled",
  xml_tree_json: "xml_tree_json_enabled",
  dots_ocr: "dots_ocr_enabled",
};

const SERVICE_BACKENDS: ParserServiceBackendName[] = ["oci_document_understanding"];

const PARSER_BACKEND_SERVICE_IDS: Partial<
  Record<ParserAdapterBackendName | ParserServiceBackendName, string>
> = {
  docling: "parser-docling",
  unstructured: "parser-unstructured",
  structured_records_jsonl: "parser-structured-records-jsonl",
  xml_tree_json: "parser-xml-tree-json",
  oci_document_understanding: "parser-oci-document-understanding",
};

function isAdapterBackend(backend: ParserAdapterBackend): backend is ParserAdapterBackendName {
  return backend in ADAPTER_FLAG_FIELDS;
}

function isServiceBackend(backend: ParserAdapterBackend): backend is ParserServiceBackendName {
  return (SERVICE_BACKENDS as ParserAdapterBackend[]).includes(backend);
}

/**
 * 準備中（ロードマップ）のエンジン（OCI Document Understanding。#3374）。
 * カードを灰色にして選ばせず、状態も問い合わせない。外部の接続の欄も出さず、保存にも送らない。
 */
function roadmapBackendsFromSettings(settings: ParserAdapterSettingsData): Set<ParserAdapterBackend> {
  return new Set<ParserAdapterBackend>([
    ...(settings.service_backends ?? [])
      .filter((service) => service.availability === "roadmap")
      .map((service) => service.backend),
    ...settings.adapters
      .filter((adapter) => adapter.availability === "roadmap")
      .map((adapter) => adapter.backend),
  ]);
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
  // 直前に取り込んだ解析エンジンの server 値。server 値が変わったレンダーだけ、未編集ならフォームをそろえる。
  // 「解析後の処理」の保存でも query.data は変わるため、解析エンジンの部分だけを比べる（#528）。
  // 保存の開始・終了（isPending）ではフォームを戻さない（失敗しても入力を残す。#976）。
  const [base, setBase] = useState<string | null>(null);
  const serverForm = query.data ? formFromSettings(query.data) : null;
  const serverKey = serverForm ? serializeForm(serverForm) : null;
  if (serverForm && serverKey !== base) {
    setBase(serverKey);
    if (form === null || serializeForm(form) === base) setForm(serverForm);
  }

  // 未保存の変更があるときは離脱を確認し、保存中は離脱を止める。
  useLeaveGuard(Boolean(serverKey && form && serializeForm(form) !== serverKey), save.isPending);

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
        <ApiErrorState
          error={query.error}
          fallback={t("settings.parserAdapters.loadError")}
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
    setForm((current) => (current ? { ...current, ...update } : current));
  }

  function selectBackend(adapterBackend: ParserAdapterBackend) {
    const enabledUpdate = externalBackendFlagUpdate(adapterBackend);
    updateForm({ adapter_backend: adapterBackend, ...enabledUpdate });
  }

  function resetForm() {
    save.reset();
    setForm(formFromSettings(settings));
  }

  function submit() {
    if (!form || save.isPending) return;
    save.mutate(parserSettingsUpdate(form), {
      onSuccess: (data) => {
        const next = formFromSettings(data);
        setBase(serializeForm(next));
        setForm(next);
        // 保存の成功は Toast、失敗は操作の行の FormStatus（messaging.md §10.2）。
        toast.success(t("settings.parserAdapters.actions.saved"));
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
        errorMessage={save.isError ? saveError : null}
        onBackendChange={selectBackend}
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
  errorMessage,
  onBackendChange,
  onReset,
  onSubmit,
}: {
  dirty: boolean;
  form: ParserAdapterForm;
  settings: ParserAdapterSettingsData;
  saving: boolean;
  errorMessage: string | null;
  onBackendChange: (backend: ParserAdapterBackend) => void;
  onReset: () => void;
  onSubmit: () => void;
}) {
  const backendOptions = useMemo(
    () => backendOptionsFromSettings(settings),
    [settings]
  );
  // 正本（PARSER_BACKEND_ORDER。#3359）の順。1 つの 2 列の grid に並べ、375px の 1 列でも同じ順にする
  // （Docling・Unstructured / 構造化レコード JSONL・XML ツリー JSON /
  // Dots.OCR・OCI Document Understanding）。処理レシピの選択肢も同じ順。
  const orderedBackends = useMemo(() => sortParserBackends(backendOptions), [backendOptions]);
  // 準備中（ロードマップ）のエンジン（#3374）。カードを灰色にして選ばせず、状態も問い合わせない。
  const roadmapBackends = useMemo(() => roadmapBackendsFromSettings(settings), [settings]);
  const serviceIds = useMemo(
    () => [
      ...new Set(
        backendOptions
          .filter((backend) => !roadmapBackends.has(backend))
          .map(serviceIdForBackend)
          .filter((serviceId): serviceId is string => Boolean(serviceId))
      ),
    ],
    [backendOptions, roadmapBackends]
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
  // 接続の設定を開いているエンジン（#4740）。開くだけで使用エンジンの選択は変えない。
  const [connectionSheetBackend, setConnectionSheetBackend] = useState<ExternalParserBackendName | null>(null);
  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div data-surface-tint="info" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
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
            {/* 1 行 2 枚で横幅いっぱいに並べる。 */}
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {orderedBackends.map((backend) => {
                  const roadmap = roadmapBackends.has(backend);
                  const selected = !roadmap && form.adapter_backend === backend;
                  const roadmapReasonId = `settings-parser-backend-${backend}-roadmap-reason`;
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
                  // 外部 GPU のエンジンは、カードの「設定」から接続を開く（準備中は出さない）。
                  const connectionBackend = isExternalBackend(backend) && !roadmap ? backend : null;
                  return (
                    <div key={backend} className="relative min-w-0">
                      <input
                        id={`settings-parser-backend-${backend}`}
                        className="peer absolute inset-0 z-10 cursor-pointer opacity-0 disabled:cursor-not-allowed"
                        type="radio"
                        name="settings-parser-backend"
                        value={backend}
                        checked={selected}
                        // 準備中（ロードマップ）のエンジンは選べない（#3374）。理由は下の文で読み上げる。
                        disabled={saving || roadmap}
                        aria-describedby={roadmap ? roadmapReasonId : undefined}
                        data-availability={roadmap ? "roadmap" : undefined}
                        onChange={() => onBackendChange(backend)}
                      />
                      <label
                        data-surface-tint={selected ? "accent" : undefined}
                        htmlFor={`settings-parser-backend-${backend}`}
                        className={cn(
                          connectionBackend ? "flex flex-col" : "block",
                          "h-full cursor-pointer min-h-[5.43rem] rounded-md border px-3 py-2 text-left transition-colors peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-focus-ring peer-disabled:cursor-not-allowed peer-disabled:opacity-50",
                          roadmap
                            ? // 準備中は無効の地と灰色の名前にし、理由は読める濃さのまま残す（全体を薄くしない。#3374）。
                              "border-border bg-surface-disabled text-fg-disabled peer-disabled:opacity-100"
                            : selected
                              ? "border-accent-emphasis bg-accent-subtle text-fg"
                              : "border-border bg-surface text-fg peer-hover:bg-surface-hover"
                        )}
                      >
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="text-sm font-semibold">{backendLabel(backend)}</span>
                          {runtimeProfile ? <ServiceProfileBadge profile={runtimeProfile} /> : null}
                          {roadmap ? <RoadmapBadge label={t("settings.parserAdapters.roadmap.badge")} /> : null}
                        </span>
                        {roadmap ? (
                          <span
                            id={roadmapReasonId}
                            className="mt-1 block text-xs font-medium leading-relaxed text-fg-muted"
                          >
                            {t("settings.parserAdapters.roadmap.reason")}
                          </span>
                        ) : null}
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
                        <span
                          className={cn(
                            "flex flex-wrap items-center gap-1.5",
                            // 「設定」のボタンと同じ行に、重ならないよう右を空けて並べる。
                            connectionBackend ? "mt-auto min-h-[var(--control-height-touch)] pt-2 pr-24" : "mt-2"
                          )}
                        >
                          {runtimeStatus ? <ServiceStatusBadge status={runtimeStatus} /> : null}
                          {service && !roadmap && !service.configured ? (
                            <ConnectionConfiguredBadge configured={false} />
                          ) : null}
                          {externalConnection && !roadmap ? (
                            <ConnectionConfiguredBadge configured={externalConnection.configured} />
                          ) : null}
                        </span>
                      </label>
                      {/* ラベルの外（ラジオの上の層）に置き、押してもエンジンを選ばない（#4740）。 */}
                      {connectionBackend ? (
                        <div className="absolute bottom-2 right-3 z-20 flex h-[var(--control-height-touch)] items-center">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            icon={Settings}
                            disabled={saving}
                            aria-label={t("settings.parserAdapters.connection.settingsLabel", {
                              backend: backendLabel(connectionBackend),
                            })}
                            aria-haspopup="dialog"
                            aria-expanded={connectionSheetBackend === connectionBackend}
                            aria-controls={externalConnectionSheetId(connectionBackend)}
                            onClick={() => setConnectionSheetBackend(connectionBackend)}
                          >
                            {t("settings.parserAdapters.connection.settings")}
                          </Button>
                        </div>
                      ) : null}
                    </div>
                  );
                })}
            </div>
          </div>
          {form.adapter_backend === "local" ? (
            <p className="text-xs leading-relaxed text-warning-fg">
              {t("settings.parserAdapters.legacyBackendNotice")}
            </p>
          ) : null}
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
        <FormActionBar
          ariaLabel={t("settings.parserAdapters.actions.label")}
          primaryActions={[
            {
              id: "save",
              label: t("settings.parserAdapters.actions.save"),
              icon: Save,
              loading: saving,
              disabled: !dirty,
              onClick: onSubmit,
            },
          ]}
          secondaryActions={[
            {
              id: "reset",
              label: t("settings.parserAdapters.actions.reset"),
              icon: RotateCcw,
              disabled: !dirty || saving,
              onClick: onReset,
            },
          ]}
          status={
            errorMessage ? (
              <FormStatus tone="danger" message={errorMessage} />
            ) : dirty ? (
              <FormStatus tone="warning" message={t("settings.parserAdapters.actions.unsaved")} />
            ) : null
          }
        />
        {EXTERNAL_BACKENDS.filter((backend) => !roadmapBackends.has(backend)).map((backend) => (
          <ExternalConnectionSheet
            key={backend}
            backend={backend}
            backendLabel={backendLabel(backend)}
            connection={connectionByBackend.get(backend)}
            open={connectionSheetBackend === backend}
            onClose={() => setConnectionSheetBackend(null)}
          />
        ))}
      </CardContent>
    </Card>
  );
}

function RuntimeFact({ label, value }: { label: string; value: string }) {
  return (
    <div data-surface-tint="neutral" className="rounded-md border border-border bg-surface-hover p-3">
      <dt className="text-xs font-medium text-fg-muted">{label}</dt>
      <dd className="mt-1 break-words text-sm font-semibold text-fg">{value}</dd>
    </div>
  );
}

function adapterLabel(adapter: ParserAdapterBackendName) {
  if (adapter === "docling") return "Docling";
  if (adapter === "unstructured") return "Unstructured";
  if (adapter === "structured_records_jsonl")
    return t("settings.parserAdapters.backend.structured_records_jsonl");
  if (adapter === "xml_tree_json") return t("settings.parserAdapters.backend.xml_tree_json");
  return "Dots.OCR";
}

function backendLabel(backend: ParserAdapterBackend) {
  if (backend === "local") return t("settings.parserAdapters.backend.local");
  if (backend === "dots_ocr") return "Dots.OCR";
  if (backend === "oci_document_understanding")
    return t("settings.parserAdapters.backend.oci_document_understanding");
  return adapterLabel(backend);
}

function backendOptionsFromSettings(settings: ParserAdapterSettingsData): ParserAdapterBackend[] {
  const ordered: ParserAdapterBackend[] = [
    ...settings.adapters.map((adapter) => adapter.backend),
    ...(settings.service_backends ?? []).map((service) => service.backend),
  ];
  const selected = settings.adapter_backend;
  if (selected !== "local" && !ordered.includes(selected)) ordered.push(selected);
  return [...new Set(ordered)];
}

function serviceIdForBackend(backend: ParserAdapterBackend): string | null {
  if (backend === "local" || isExternalBackend(backend)) return null;
  if (isAdapterBackend(backend) || isServiceBackend(backend)) {
    return PARSER_BACKEND_SERVICE_IDS[backend] ?? null;
  }
  return null;
}

function serviceProfileForBackend(backend: ParserAdapterBackend): ServiceProfile | null {
  if (backend === "dots_ocr") return "gpu";
  if (isServiceBackend(backend)) return "oci";
  if (isAdapterBackend(backend)) return "cpu";
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
    adapter_backend: settings.adapter_backend,
    docling_enabled: enabledByBackend.get("docling") ?? false,
    unstructured_enabled: enabledByBackend.get("unstructured") ?? false,
    dots_ocr_enabled: enabledByBackend.get("dots_ocr") ?? false,
    structured_records_jsonl_enabled: enabledByBackend.get("structured_records_jsonl") ?? false,
    xml_tree_json_enabled: enabledByBackend.get("xml_tree_json") ?? false,
  };
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
    dots_ocr_enabled: form.dots_ocr_enabled,
    structured_records_jsonl_enabled: form.structured_records_jsonl_enabled,
    xml_tree_json_enabled: form.xml_tree_json_enabled,
  });
}

function parserSettingsUpdate(form: ParserAdapterForm): ParserAdapterSettingsUpdate {
  return {
    adapter_backend: form.adapter_backend,
    docling_enabled: form.docling_enabled,
    unstructured_enabled: form.unstructured_enabled,
    dots_ocr_enabled: form.dots_ocr_enabled,
    structured_records_jsonl_enabled: form.structured_records_jsonl_enabled,
    xml_tree_json_enabled: form.xml_tree_json_enabled,
  };
}
