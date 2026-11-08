import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Database, HardDrive, RefreshCw, Server, SlidersHorizontal, type LucideIcon } from "lucide-react";
import {
  Banner,
  ButtonLink,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  PageHeader,
  StatusBadge,
  PageBody,
  TimedLoadingState,
  useActionPending,
} from "@production-ready/ui";
import { agentApi, type RuntimeStorageStatus } from "@/lib/api";
import { QueryState } from "@/components/ListViews";
import { storageFixKey, storageReasonKey, useRuntimeStorage } from "@/components/system/StorageNotice";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { canOpenRoute } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";
import { useAuth } from "@/components/security/AuthProvider";

/** 保存先をデータベースにする設定（agent/backend/.env。既定の auto なら要らない。#764 / #839）。 */
const PERSISTENT_STORAGE_SETTING = "AGENT_RUNTIME_REPOSITORY_BACKEND=oracle_checkpoint";

/**
 * Runtime（組み込み Runtime の状態。#754）。業務 Agent は Control Plane の中の OpenAI Agents SDK で実行し、
 * モデルは「システム設定 > モデル」の OCI Enterprise AI を使う。外部 Runtime の登録・Docker の操作は無い。
 */
export function RuntimesPage() {
  const { hasPermission } = useAuth();
  const status = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  const storage = useRuntimeStorage();
  // 「最新情報を取得」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();
  const data = status.data;
  // 実行環境と保存先は同じ時に読み込む。経過時間は 1 か所だけにし、2 つのカードの形で寸法を予約する（#4649）。
  const initialLoading = status.isLoading || storage.isLoading;
  return (
    <>
      <PageHeader
        wide
        title={t("nav.runtimes")}
        subtitle={t("page.runtimes.subtitle")}
        actions={[
          {
            // 外部の状態（モデルの設定）の再確認。ページツールなので utility（buttons.md §5）。文言は製品のまま。
            id: "refresh",
            kind: "utility",
            label: t("runtime.refresh"),
            icon: RefreshCw,
            loading: manualRefresh.pending,
            onClick: () => void manualRefresh.track(() => Promise.all([status.refetch(), storage.refetch()])),
          },
        ]}
      />
      <PageBody wide>
        {initialLoading ? (
          <TimedLoadingState label={t("loading.runtimes")} testId="runtimes-loading">
            <div className="space-y-6">
              <RuntimeCardSkeleton
                icon={Server}
                title={t("runtime.builtin.title")}
                description={t("runtime.builtin.description")}
                terms={[
                  t("runtime.builtin.sdk"),
                  t("runtime.builtin.provider"),
                  t("runtime.builtin.defaultModel"),
                  t("runtime.builtin.models"),
                ]}
                testId="builtin-runtime-card-skeleton"
              />
              <RuntimeCardSkeleton
                icon={HardDrive}
                title={t("storage.card.title")}
                description={t("storage.card.description")}
                terms={[t("storage.card.backend"), t("storage.card.database")]}
                testId="runtime-storage-card-skeleton"
              />
            </div>
          </TimedLoadingState>
        ) : (
          <>
            <QueryState query={status} loadingLabel={t("loading.runtimes")} skeleton={null}>
              {data ? (
                <Card className="min-w-0" data-testid="builtin-runtime-card">
                  <CardHeader className="flex-row items-start justify-between gap-4">
                    <div className="min-w-0">
                      <CardTitle className="flex items-center gap-2">
                        <Server size={20} aria-hidden />
                        {t("runtime.builtin.title")}
                      </CardTitle>
                      <CardDescription>{t("runtime.builtin.description")}</CardDescription>
                    </div>
                    <StatusBadge
                      variant={data.ready ? "success" : "warning"}
                      label={data.ready ? t("runtime.builtin.ready") : t("runtime.builtin.notReady")}
                    />
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
                      <dt className="text-fg-muted">{t("runtime.builtin.sdk")}</dt>
                      <dd className="break-words">
                        <code>{data.sdk}</code> {data.sdk_version}
                      </dd>
                      <dt className="text-fg-muted">{t("runtime.builtin.provider")}</dt>
                      <dd>{data.model_provider}</dd>
                      <dt className="text-fg-muted">{t("runtime.builtin.defaultModel")}</dt>
                      <dd className="break-words">{data.model_id || t("runtime.builtin.modelUnset")}</dd>
                      <dt className="text-fg-muted">{t("runtime.builtin.models")}</dt>
                      <dd className="break-words">
                        {data.models.length
                          ? data.models.map((model) => model.display_name).join("、")
                          : t("runtime.builtin.modelsEmpty")}
                      </dd>
                    </dl>
                    {!data.ready ? (
                      // 実行できない理由と、直す場所を出す（モデルの設定はシステム設定の権限がある利用者だけが開ける）。
                      <Banner severity="warning" title={t("runtime.builtin.notReadyTitle")}>
                        <div className="space-y-3">
                          <p>{data.message ?? t("runtime.builtin.notReadyDefault")}</p>
                          {hasPermission(MENU_PERMISSIONS.settingsModel) ? (
                            <ButtonLink
                              to={APP_ROUTES.settingsModel}
                              linkComponent={Link}
                              size="sm"
                              icon={SlidersHorizontal}
                              testId="builtin-runtime-open-model-settings"
                            >
                              {t("runtime.builtin.openModelSettings")}
                            </ButtonLink>
                          ) : (
                            <p className="text-fg-muted">{t("runtime.builtin.askAdmin")}</p>
                          )}
                        </div>
                      </Banner>
                    ) : null}
                  </CardContent>
                </Card>
              ) : null}
            </QueryState>
            <QueryState query={storage} loadingLabel={t("loading.runtimes")} skeleton={null}>
              {storage.data ? <StorageCard status={storage.data} /> : null}
            </QueryState>
          </>
        )}
      </PageBody>
    </>
  );
}

/**
 * 保存先（#839）。業務 Agent・スキル・MCP 接続・実行などが再起動の後も残るかを StatusBadge で出し
 * （messaging.md §10.2）、残らないときは理由と直し方を warning の Banner で出す（§3.4）。
 * 他の画面の案内（`NonPersistentStorageNotice`）はここへ案内する。
 */
function StorageCard({ status }: { status: RuntimeStorageStatus }) {
  const { hasPermission } = useAuth();
  const canOpenDatabase = canOpenRoute(APP_ROUTES.settingsDatabase, hasPermission);
  // 起動時の読み込みの結果（#853）。古いバックエンドは返さないので 0 とみなす。
  const repairedRuns = status.repaired_runs ?? 0;
  const skippedRuns = status.skipped_runs ?? 0;
  const skippedAgents = status.skipped_agents ?? 0;
  return (
    <Card className="min-w-0" data-testid="runtime-storage-card">
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <HardDrive size={20} aria-hidden />
            {t("storage.card.title")}
          </CardTitle>
          <CardDescription>{t("storage.card.description")}</CardDescription>
        </div>
        <StatusBadge
          variant={status.persistent ? "success" : "warning"}
          label={status.persistent ? t("storage.card.persistent") : t("storage.card.notPersistent")}
        />
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
          <dt className="text-fg-muted">{t("storage.card.backend")}</dt>
          <dd data-testid="runtime-storage-backend">{t(`storage.backend.${status.backend}`)}</dd>
          <dt className="text-fg-muted">{t("storage.card.database")}</dt>
          <dd>
            {status.database_configured
              ? t("storage.card.databaseConfigured")
              : t("storage.card.databaseNotConfigured")}
          </dd>
          {repairedRuns > 0 ? (
            <>
              <dt className="text-fg-muted">{t("storage.card.repairedRuns")}</dt>
              <dd data-testid="runtime-storage-repaired">
                <p>{t("storage.card.count", { count: repairedRuns })}</p>
                <p className="text-fg-muted">{t("storage.card.repairedRunsHint")}</p>
              </dd>
            </>
          ) : null}
        </dl>
        {skippedRuns + skippedAgents > 0 ? (
          <Banner
            severity="warning"
            title={t("storage.skipped.title")}
          >
            <div className="space-y-2" data-testid="runtime-storage-skipped">
              <p>{t("storage.skipped.body", { runs: skippedRuns, agents: skippedAgents })}</p>
              <p>{t("storage.skipped.fix")}</p>
            </div>
          </Banner>
        ) : null}
        {!status.persistent ? (
          <Banner
            severity="warning"
            title={t("storage.notice.title")}
            action={
              status.reason === "database_not_configured" && canOpenDatabase ? (
                <ButtonLink
                  to={APP_ROUTES.settingsDatabase}
                  linkComponent={Link}
                  size="sm"
                  icon={Database}
                  testId="runtime-storage-open-database-settings"
                >
                  {t("storage.fix.openDatabaseSettings")}
                </ButtonLink>
              ) : undefined
            }
          >
            <div className="space-y-2" data-testid="runtime-storage-fix">
              <p>{t(storageReasonKey(status))}</p>
              <p>{t(storageFixKey(status))}</p>
              {/* 既定（auto）なら設定は要らない。メモリなどを明示しているときだけ、直す値を出す。 */}
              {status.configured_backend !== "auto" && status.reason !== "restart_required" ? (
                <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span>{t("storage.fix.setting")}</span>
                  <code className="break-all font-mono">{PERSISTENT_STORAGE_SETTING}</code>
                </p>
              ) : null}
            </div>
          </Banner>
        ) : null}
      </CardContent>
    </Card>
  );
}

/**
 * 実行環境・保存先のカードの形（#4649）。見出し（題名・説明）と項目名は決まった文なのでそのまま出し、
 * 読み込んで決まる右の状態と値だけを Skeleton にする（文字の行の高さ 1lh で、読み込み後のカードと同じ高さ）。
 */
function RuntimeCardSkeleton({
  icon: Icon,
  title,
  description,
  terms,
  testId,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  /** 項目名（`dt`）の文。 */
  terms: string[];
  testId?: string;
}) {
  return (
    <Card className="min-w-0" aria-hidden="true" data-skeleton="card" data-testid={testId}>
      <CardHeader className="flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Icon size={20} aria-hidden />
            {title}
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <Skeleton className="h-[1lh] w-24 shrink-0 text-sm" />
      </CardHeader>
      <CardContent>
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[max-content_1fr]">
          {terms.map((term, row) => (
            <div key={term} className="contents">
              <dt className="text-fg-muted">{term}</dt>
              <dd>
                <Skeleton className={row % 2 === 0 ? "h-[1lh] w-1/2" : "h-[1lh] w-2/3"} />
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
