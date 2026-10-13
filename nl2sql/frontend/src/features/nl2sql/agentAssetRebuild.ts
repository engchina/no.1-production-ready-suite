/**
 * 業務プロファイルの Select AI Agent アセットを最後に再構築した日時と結果（#1691）。
 *
 * 再構築は業務プロファイルの保存の Oracle 反映のジョブ（profile_sync）の工程で行う。backend の
 * `GET /api/nl2sql/profiles/{profile_id}/agent-asset-rebuild` が、ジョブの記録から最後の再構築を
 * 業務プロファイルの版（ETag）と別に返す（業務プロファイルに書くと編集中の保存と競合するため）。
 */
import { formatDateTime } from "../../lib/format";
import { t } from "../../lib/i18n";

export type ProfileAgentAssetRebuildStatus = "never" | "succeeded" | "failed";

export interface ProfileAgentAssetRebuildData {
  profile_id: string;
  status: ProfileAgentAssetRebuildStatus;
  /** 再構築が終わった（失敗した）日時（ISO 8601）。まだ再構築していなければ空。 */
  rebuilt_at: string;
  job_id: string;
  /** 再構築した業務プロファイルの版。 */
  profile_etag: string;
  /** 最後の再構築の後に業務プロファイルが保存された（アセットに入っていない変更がある）。 */
  profile_changed_since: boolean;
  team_name: string;
  error_code: string;
  /** 失敗の要約（利用者向けの日本語。ORA・PL/SQL の stack は含まない）。 */
  error_message_ja: string;
}

export function profileAgentAssetRebuildUrl(profileId: string): string {
  return `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/agent-asset-rebuild`;
}

export function profileAgentAssetRebuildKey(profileId: string) {
  return ["nl2sql", "profile-agent-asset-rebuild", profileId] as const;
}

export type AgentAssetRebuildBadgeVariant = "neutral" | "success" | "danger";

export interface AgentAssetRebuildPresentation {
  variant: AgentAssetRebuildBadgeVariant;
  /** 見出しの `StatusBadge` の文言（状態は色だけでなく文字とアイコンで示す）。 */
  badge: string;
  /** 日時（「最後の再構築: MM/DD HH:mm」）か、まだ再構築していないことの案内。 */
  summary: string;
  /** 失敗の要約。成功・未実行では空。 */
  failure: string;
  /** 最後の再構築の後に業務プロファイルを保存したときの案内。無ければ空。 */
  changedSince: string;
}

const STATUS_VARIANT: Record<ProfileAgentAssetRebuildStatus, AgentAssetRebuildBadgeVariant> = {
  never: "neutral",
  succeeded: "success",
  failed: "danger",
};

export function presentAgentAssetRebuild(
  data: ProfileAgentAssetRebuildData,
): AgentAssetRebuildPresentation {
  // 想定外の値は「まだ再構築していません」と同じに扱い、画面を落とさない。
  const status: ProfileAgentAssetRebuildStatus =
    data.status === "succeeded" || data.status === "failed" ? data.status : "never";
  if (status === "never") {
    return {
      variant: STATUS_VARIANT.never,
      badge: t("profiles.oracle.assets.rebuild.status.never"),
      summary: t("profiles.oracle.assets.rebuild.never"),
      failure: "",
      changedSince: "",
    };
  }
  return {
    variant: STATUS_VARIANT[status],
    badge: t(`profiles.oracle.assets.rebuild.status.${status}`),
    summary: t("profiles.oracle.assets.rebuild.at", { time: formatDateTime(data.rebuilt_at) }),
    failure:
      status === "failed"
        ? data.error_message_ja.trim() || t("profiles.oracle.assets.rebuild.failedFallback")
        : "",
    changedSince: data.profile_changed_since ? t("profiles.oracle.assets.rebuild.changedSince") : "",
  };
}
