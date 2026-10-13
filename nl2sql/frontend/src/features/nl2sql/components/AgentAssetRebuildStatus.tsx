import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";

import {
  ApiErrorBanner,
  Button,
  Skeleton,
  StatusBadge,
  TimedLoadingState,
} from "@production-ready/ui";

import { apiGet } from "@/lib/api";
import { t } from "@/lib/i18n";

import {
  presentAgentAssetRebuild,
  profileAgentAssetRebuildKey,
  profileAgentAssetRebuildUrl,
  type ProfileAgentAssetRebuildData,
} from "../agentAssetRebuild";

const TITLE_ID = "profile-agent-rebuild-title";

/**
 * 業務プロファイルの Select AI Agent アセットを最後に再構築した日時と結果（#1691）。
 *
 * 編集画面の「Select AI Agent アセット」の欄の、再構築のチェックの直下に出す。アセットの状態なので
 * 見出しの `StatusBadge`（messaging.md §10.2）で示し、日時・失敗の要約は文で添える。保存の直後の
 * 結果（`ProfileSaveProgress`）と違い、画面を開き直しても出る。保存の Oracle 反映のジョブが
 * 終わると、画面がこの取得を取り直す（`profileAgentAssetRebuildKey`）。
 */
export function AgentAssetRebuildStatus({ profileId }: { profileId: string }) {
  const query = useQuery({
    queryKey: profileAgentAssetRebuildKey(profileId),
    queryFn: ({ signal }) =>
      apiGet<ProfileAgentAssetRebuildData>(profileAgentAssetRebuildUrl(profileId), { signal }),
    enabled: Boolean(profileId),
    staleTime: 10_000,
    retry: false,
  });

  return (
    <div
      role="group"
      aria-labelledby={TITLE_ID}
      className="grid min-w-0 gap-2 border-t border-border pt-3"
      data-testid="profile-agent-rebuild-status"
    >
      {query.isPending ? (
        // 欄の中の小さな取得なので枠とスピナーは付けず、文言・経過時間と行の形の Skeleton だけを出す
        // （スピナーは画面の主な読み込みが持つ。messaging.md §3.6 / §3.7）。
        <TimedLoadingState
          label={t("profiles.oracle.assets.rebuild.loading")}
          operationKey={`profile-agent-rebuild-${profileId}`}
          framed={false}
          activityIcon="none"
          testId="profile-agent-rebuild-loading"
        >
          <span id={TITLE_ID} className="sr-only">
            {t("profiles.oracle.assets.rebuild.title")}
          </span>
          <div className="grid gap-2" aria-hidden>
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-5 w-full" />
          </div>
        </TimedLoadingState>
      ) : query.isError ? (
        <>
          <span id={TITLE_ID} className="sr-only">
            {t("profiles.oracle.assets.rebuild.title")}
          </span>
          <ApiErrorBanner
            error={query.error}
            fallback={t("profiles.oracle.assets.rebuild.loadError")}
            testId="profile-agent-rebuild-error"
            action={
              <Button
                type="button"
                variant="secondary"
                size="sm"
                icon={RefreshCw}
                loading={query.isFetching}
                onClick={() => void query.refetch()}
              >
                {t("common.action.retry")}
              </Button>
            }
          />
        </>
      ) : (
        <AgentAssetRebuildSummary data={query.data} />
      )}
    </div>
  );
}

function AgentAssetRebuildSummary({ data }: { data: ProfileAgentAssetRebuildData }) {
  const view = presentAgentAssetRebuild(data);
  return (
    <>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <h4 id={TITLE_ID} className="text-sm font-semibold text-fg">
          {t("profiles.oracle.assets.rebuild.title")}
        </h4>
        <span data-testid="profile-agent-rebuild-badge">
          <StatusBadge variant={view.variant} label={view.badge} />
        </span>
      </div>
      <p className="break-words text-sm text-fg-muted" data-testid="profile-agent-rebuild-summary">
        {data.rebuilt_at ? <time dateTime={data.rebuilt_at}>{view.summary}</time> : view.summary}
      </p>
      {view.failure ? (
        <p className="break-words text-sm text-fg" data-testid="profile-agent-rebuild-failure">
          {view.failure}
        </p>
      ) : null}
      {view.changedSince ? (
        <p className="break-words text-sm text-fg-muted" data-testid="profile-agent-rebuild-changed">
          {view.changedSince}
        </p>
      ) : null}
    </>
  );
}
