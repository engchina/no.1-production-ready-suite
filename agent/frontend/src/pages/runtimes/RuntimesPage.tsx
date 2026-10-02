import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { RefreshCw, Server, SlidersHorizontal } from "lucide-react";
import {
  Banner,
  buttonVariants,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Skeleton,
  PageHeader,
  StatusBadge,
  PageBody,
  useActionPending,
} from "@engchina/production-ready-ui";
import { agentApi } from "@/lib/api";
import { QueryState } from "@/components/ListViews";
import { t } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";
import { APP_ROUTES } from "@/lib/routes";
import { useAuth } from "@/components/security/AuthProvider";

/**
 * Runtime（組み込み Runtime の状態。#754）。業務 Agent は Control Plane の中の OpenAI Agents SDK で実行し、
 * モデルは「システム設定 > モデル」の OCI Enterprise AI を使う。外部 Runtime の登録・Docker の操作は無い。
 */
export function RuntimesPage() {
  const { hasPermission } = useAuth();
  const status = useQuery({ queryKey: ["runtime-status"], queryFn: agentApi.getRuntimeStatus });
  // 「表示を更新」は押した取り直しの間だけ回す（定期の取り直し・他の操作の後の invalidate・条件の切り替えでは回さない。#819）。
  const manualRefresh = useActionPending();
  const data = status.data;
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
            onClick: () => void manualRefresh.track(() => status.refetch()),
          },
        ]}
      />
      <PageBody wide>
        <QueryState query={status} loadingLabel={t("loading.runtimes")} skeleton={<RuntimeCardsSkeleton />}>
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
                        <Link
                          to={APP_ROUTES.settingsModel}
                          className={buttonVariants({ variant: "secondary", size: "sm" })}
                          data-testid="builtin-runtime-open-model-settings"
                        >
                          <SlidersHorizontal size={16} aria-hidden="true" />
                          <span>{t("runtime.builtin.openModelSettings")}</span>
                        </Link>
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
      </PageBody>
    </>
  );
}

/** 組み込み Runtime のカードの形。読み込み後のカードの高さを予約する。 */
function RuntimeCardsSkeleton() {
  return (
    <div aria-hidden="true">
      <Skeleton className="h-56" />
    </div>
  );
}
