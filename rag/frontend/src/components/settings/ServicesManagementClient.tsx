"use client";

import { Fragment, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Container,
  MinusCircle,
  Play,
  RefreshCw,
  Server,
  Square,
  TerminalSquare,
  type LucideIcon,
} from "lucide-react";

import { ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { FormStatus } from "@/components/ui/form-status";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ApiError,
  type DeploymentMode,
  type ServiceCatalogItemData,
  type ServiceProfile,
  type ServiceRuntimeStatus,
} from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useControlService, useServiceCatalog, useServiceStatusQueries } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

type DisplayRuntimeStatus = ServiceRuntimeStatus | "loading" | "error";
type DisplayServiceData = ServiceCatalogItemData & {
  status: DisplayRuntimeStatus;
  blocked_by: string[];
  statusReady: boolean;
};

/** 前処理 / Parser マイクロサービスの稼働可視化・起動/停止を行う設定画面。 */
export function ServicesManagementClient() {
  const query = useServiceCatalog();
  const serviceIds = query.data?.services.map((service) => service.service_id) ?? [];
  const statusQueries = useServiceStatusQueries(serviceIds);
  const control = useControlService();
  const confirm = useConfirm();
  // クリックした行・操作だけにスピナーを出すための識別子(`${serviceId}:${action}`)。
  const [pending, setPending] = useState<string | null>(null);

  if (query.isPending) {
    return (
      <div className="space-y-4 p-8">
        <Skeleton className="h-40 w-full rounded-lg" />
        <Skeleton className="h-40 w-full rounded-lg" />
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
              : t("settings.services.loadError")
          }
          onRetry={() => void query.refetch()}
        />
      </div>
    );
  }

  const data = query.data;
  if (!data) return null;

  const displayServices = data.services.map<DisplayServiceData>((service, index) => {
    const statusQuery = statusQueries[index];
    const statusData = statusQuery?.data;
    if (statusData) {
      return {
        ...service,
        ...statusData,
        statusReady: true,
      };
    }
    return {
      ...service,
      status: statusQuery?.isError ? "error" : "loading",
      blocked_by: [],
      statusReady: false,
    };
  });
  const controlEnabled = data.control_enabled;
  const deploymentMode = data.deployment_mode;
  const serviceById = new Map(displayServices.map((service) => [service.service_id, service]));
  // サービス管理ページのセクションは検索・回答フロー順(サイドナビと一致)で表示する。
  // 各ステージは CPU/GPU/OCI のうち存在するプロファイルごとにグループを分けて表示する。
  const PIPELINE_STAGE_ORDER: { category: string; labelKey: I18nKey }[] = [
    { category: "preprocess", labelKey: "settings.services.stage.preprocess" },
    { category: "parser", labelKey: "settings.services.stage.parser" },
    { category: "chunking", labelKey: "settings.services.stage.chunking" },
    { category: "vector_index", labelKey: "settings.services.stage.vectorIndex" },
    { category: "retrieval", labelKey: "settings.services.stage.retrieval" },
    { category: "grounding", labelKey: "settings.services.stage.grounding" },
    { category: "generation", labelKey: "settings.services.stage.generation" },
    { category: "guardrail", labelKey: "settings.services.stage.guardrail" },
    { category: "evaluation", labelKey: "settings.services.stage.evaluation" },
    { category: "graphrag", labelKey: "settings.services.stage.graphrag" },
    { category: "agentic", labelKey: "settings.services.stage.agentic" },
  ];
  // プロファイル表示順と suffix/note。GPU/OCI は単独でも opt-in/要件を note で明示する。
  const PROFILE_ORDER: {
    profile: ServiceProfile;
    suffixKey: I18nKey;
    noteKey: I18nKey | null;
  }[] = [
    { profile: "cpu", suffixKey: "settings.services.cpuSuffix", noteKey: null },
    { profile: "gpu", suffixKey: "settings.services.gpuSuffix", noteKey: "settings.services.gpuNote" },
    { profile: "oci", suffixKey: "settings.services.ociSuffix", noteKey: "settings.services.ociNote" },
  ];
  const stageGroups = PIPELINE_STAGE_ORDER.map(({ category, labelKey }) => {
    const label = t(labelKey);
    const groups = PROFILE_ORDER.map((p) => ({
      ...p,
      services: displayServices.filter((s) => s.category === category && s.profile === p.profile),
    })).filter((g) => g.services.length > 0);
    return { category, label, groups };
  });

  async function act(service: DisplayServiceData, action: "start" | "stop") {
    if (action === "stop") {
      const ok = await confirm({
        title: t("settings.services.confirm.stop.title"),
        description: t("settings.services.confirm.stop.description", {
          service: serviceLabel(service),
        }),
        confirmLabel: t("settings.services.confirm.stop.confirm"),
        cancelLabel: t("settings.services.confirm.cancel"),
        tone: "danger",
      });
      if (!ok) return;
    }
    setPending(`${service.service_id}:${action}`);
    control.mutate(
      { serviceId: service.service_id, action },
      {
        onSuccess: () => {
          toast.success(
            t(
              action === "start"
                ? "settings.services.toast.started"
                : "settings.services.toast.stopped",
              { service: serviceLabel(service) }
            )
          );
        },
        onError: (error) => {
          toast.error(
            t("settings.services.toast.failed", { service: serviceLabel(service) }),
            {
              description:
                error instanceof ApiError ? error.message : undefined,
            }
          );
        },
        onSettled: () => setPending(null),
      }
    );
  }

  const latestStatusUpdatedAt = Math.max(
    0,
    ...statusQueries.map((statusQuery) => statusQuery.dataUpdatedAt)
  );
  const lastUpdated = Math.max(query.dataUpdatedAt, latestStatusUpdatedAt);
  const lastUpdatedText = lastUpdated
    ? new Date(lastUpdated).toLocaleTimeString("ja-JP")
    : null;
  const statusFetching = statusQueries.some((statusQuery) => statusQuery.isFetching);

  function refreshServices() {
    void query.refetch();
    for (const statusQuery of statusQueries) {
      void statusQuery.refetch();
    }
  }

  return (
    <div className="space-y-5 p-8">
      <Card>
        <CardHeader>
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div className="flex items-start gap-3">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-bg text-info">
                <Server size={20} aria-hidden />
              </div>
              <div>
                <CardTitle>{t("settings.services.overview.title")}</CardTitle>
                <CardDescription>
                  {t("settings.services.overview.description")}
                </CardDescription>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <ModeBadge mode={deploymentMode} />
              <ControlBadge enabled={controlEnabled} />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                loading={query.isFetching || statusFetching}
                onClick={refreshServices}
                aria-label={t("settings.services.refresh")}
              >
                <RefreshCw size={15} aria-hidden />
                {query.isFetching || statusFetching
                  ? t("settings.services.refreshing")
                  : t("settings.services.refresh")}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {controlEnabled ? (
            <FormStatus
              tone="info"
              message={t(
                deploymentMode === "dev"
                  ? "settings.services.mode.dev.hint"
                  : "settings.services.mode.prod.hint"
              )}
            />
          ) : (
            <FormStatus tone="info" message={t("settings.services.controlDisabled.hint")} />
          )}
          {lastUpdatedText ? (
            <p className="text-xs tabular-nums text-muted">
              {t("settings.services.lastUpdated", { time: lastUpdatedText })}
            </p>
          ) : null}
        </CardContent>
      </Card>

      {stageGroups.map((stage) => {
        // 単一プロファイルかつ CPU のときだけ suffix 無しのステージ名にする。
        // 複数プロファイル、または GPU/OCI は suffix(+note)を付けて区別・要件を明示する。
        const multi = stage.groups.length > 1;
        return (
          <Fragment key={stage.category}>
            {stage.groups.map((g) => (
              <ServiceGroup
                key={`${stage.category}-${g.profile}`}
                title={
                  multi || g.profile !== "cpu"
                    ? t(g.suffixKey, { stage: stage.label })
                    : stage.label
                }
                note={g.noteKey ? t(g.noteKey) : undefined}
                services={g.services}
                controlEnabled={controlEnabled}
                pending={pending}
                serviceById={serviceById}
                onAct={act}
              />
            ))}
          </Fragment>
        );
      })}
    </div>
  );
}

function ServiceGroup({
  title,
  note,
  services,
  controlEnabled,
  pending,
  serviceById,
  onAct,
}: {
  title: string;
  note?: string;
  services: DisplayServiceData[];
  controlEnabled: boolean;
  pending: string | null;
  serviceById: Map<string, DisplayServiceData>;
  onAct: (service: DisplayServiceData, action: "start" | "stop") => void;
}) {
  if (services.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {note ? <CardDescription>{note}</CardDescription> : null}
      </CardHeader>
      <CardContent className="space-y-2">
        <ul className="divide-y divide-border">
          {services.map((service) => (
            <ServiceRow
              key={service.service_id}
              service={service}
              controlEnabled={controlEnabled}
              pending={pending}
              serviceById={serviceById}
              onAct={onAct}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function ServiceRow({
  service,
  controlEnabled,
  pending,
  serviceById,
  onAct,
}: {
  service: DisplayServiceData;
  controlEnabled: boolean;
  pending: string | null;
  serviceById: Map<string, DisplayServiceData>;
  onAct: (service: DisplayServiceData, action: "start" | "stop") => void;
}) {
  const running = service.status === "running";
  const stopped = service.status === "stopped";
  const dependencyStopped = service.status === "dependency_stopped";
  const statusLoading = service.status === "loading";
  const statusError = service.status === "error";
  const startPending = pending === `${service.service_id}:start`;
  const stopPending = pending === `${service.service_id}:stop`;
  const anyPending = pending !== null;
  const inferenceServerSummary = service.depends_on.map((id) => {
    const inferenceServer = serviceById.get(id);
    const label = inferenceServer ? serviceLabel(inferenceServer) : id;
    const status = inferenceServer
      ? t(`settings.services.status.${inferenceServer.status}` as I18nKey)
      : t("settings.services.status.unconfigured");
    return `${label}(${status})`;
  });
  const blockedInferenceServers = service.blocked_by.map((id) => {
    const inferenceServer = serviceById.get(id);
    return inferenceServer ? serviceLabel(inferenceServer) : id;
  });
  let controlHint: string | undefined;
  if (!controlEnabled) {
    controlHint = t("settings.services.controlDisabled.hint");
  } else if (statusLoading) {
    controlHint = t("settings.services.statusLoadingHint");
  } else if (statusError) {
    controlHint = t("settings.services.statusLoadErrorHint");
  } else if (dependencyStopped) {
    controlHint = t("settings.services.inferenceServerRequired", {
      service: serviceLabel(service),
      servers: blockedInferenceServers.join(", "),
    });
  }

  return (
    <li className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{serviceLabel(service)}</p>
        <p className="font-mono text-xs text-muted">{service.service_id}</p>
        {inferenceServerSummary.length > 0 ? (
          <p className="mt-1 text-xs text-muted">
            {t("settings.services.inferenceServers")}:{" "}
            {inferenceServerSummary.join(", ")}
          </p>
        ) : null}
        {blockedInferenceServers.length > 0 ? (
          <p className="mt-1 text-xs font-medium text-amber-700">
            {t("settings.services.inferenceServerRequired", {
              service: serviceLabel(service),
              servers: blockedInferenceServers.join(", "),
            })}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <ServiceStatusBadge status={service.status} />
        <div className="flex gap-2" title={controlHint}>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            loading={startPending}
            disabled={
              !controlEnabled ||
              !service.statusReady ||
              running ||
              dependencyStopped ||
              (anyPending && !startPending)
            }
            onClick={() => onAct(service, "start")}
            aria-label={`${serviceLabel(service)} ${t("settings.services.action.start")}`}
          >
            <Play size={14} aria-hidden />
            {startPending
              ? t("settings.services.action.starting")
              : t("settings.services.action.start")}
          </Button>
          <Button
            type="button"
            variant="danger"
            size="sm"
            loading={stopPending}
            disabled={!controlEnabled || !service.statusReady || stopped || (anyPending && !stopPending)}
            onClick={() => onAct(service, "stop")}
            aria-label={`${serviceLabel(service)} ${t("settings.services.action.stop")}`}
          >
            <Square size={14} aria-hidden />
            {stopPending
              ? t("settings.services.action.stopping")
              : t("settings.services.action.stop")}
          </Button>
        </div>
      </div>
    </li>
  );
}

/** 配備モード(dev=uv プロセス / prod=docker)を示すバッジ(色だけに頼らずアイコン+ラベル併記)。 */
function ModeBadge({ mode }: { mode: DeploymentMode }) {
  const Icon = mode === "dev" ? TerminalSquare : Container;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        mode === "dev" ? "bg-sky-100 text-sky-700" : "bg-violet-100 text-violet-700"
      )}
    >
      <Icon size={13} aria-hidden />
      {t(mode === "dev" ? "settings.services.mode.dev" : "settings.services.mode.prod")}
    </span>
  );
}

/** 起動/停止が全体で有効か無効かを示すバッジ。 */
function ControlBadge({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        enabled ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-600"
      )}
    >
      {t("settings.services.controlEnabled")}:{" "}
      {enabled
        ? t("settings.services.controlEnabled.on")
        : t("settings.services.controlEnabled.off")}
    </span>
  );
}

const STATUS_META: Record<
  DisplayRuntimeStatus,
  { className: string; icon: LucideIcon; spin?: boolean }
> = {
  running: { className: "bg-emerald-100 text-emerald-700", icon: CheckCircle2 },
  degraded: { className: "bg-amber-100 text-amber-700", icon: AlertTriangle },
  stopped: { className: "bg-slate-100 text-slate-600", icon: CircleSlash },
  dependency_stopped: { className: "bg-amber-100 text-amber-700", icon: AlertTriangle },
  unconfigured: { className: "bg-slate-100 text-slate-500", icon: MinusCircle },
  loading: { className: "bg-slate-100 text-slate-600", icon: RefreshCw, spin: true },
  error: { className: "bg-rose-100 text-rose-700", icon: AlertTriangle },
};

/** 稼働状態バッジ(色だけに頼らずアイコン+日本語ラベル併記)。 */
function ServiceStatusBadge({ status }: { status: DisplayRuntimeStatus }) {
  const meta = STATUS_META[status];
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium",
        meta.className
      )}
    >
      <Icon size={13} className={meta.spin ? "animate-spin" : undefined} aria-hidden />
      {t(`settings.services.status.${status}` as I18nKey)}
    </span>
  );
}

function serviceLabel(service: ServiceCatalogItemData): string {
  return t(service.label_key as I18nKey);
}

export default ServicesManagementClient;
