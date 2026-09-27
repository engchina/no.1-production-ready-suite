import type {
  RolePermissionTargetItem,
  RolePermissionTargetSection,
  RolePermissionsApi,
} from "@engchina/production-ready-system-settings";

import type { AccessTargetsData, AgentAccessTarget, SecurityRole } from "./api";
import { t } from "./i18n";
import { CAPABILITY_PERMISSIONS } from "./permissions";
import { securityApi } from "./security-api";

/**
 * 権限管理画面（共通の RolePermissionsPage）へ渡す Agent の保存 API と「利用できる対象」（#215）。
 * エージェント・業務ビューとも、`agent.admin` を持つロールで全件が対象（backend も空に正規化する）。
 */

/** 対象の key。要素 ID とテスト ID は `security-roles-<key>-*` になる。 */
export const AGENT_ACCESS_KEY = "agent-access";
export const BUSINESS_VIEW_ACCESS_KEY = "business-view-access";

/** 業務ビュー ID の形式（backend の `PUT /security/roles/{id}/access` の検証と同じ）。 */
export const BUSINESS_VIEW_ID_PATTERN = /^[A-Za-z0-9._:-]{1,64}$/;

export const PERMISSIONS_API: RolePermissionsApi<SecurityRole> = {
  roles: (includeArchived, options) => securityApi.roles(includeArchived, options),
  permissions: (options) => securityApi.permissions(options),
  // 機能権限とエージェント / 業務ビューの対象範囲を Agent の保存 API（PUT /access）へ送る。
  // 全件が対象のとき（SYSTEM_ADMIN・agent.admin）は共通画面が空の一覧を渡す。
  save: (role, draft) =>
    securityApi.updateRoleAccess({
      role_id: role.role_id,
      version: role.version,
      permissions: draft.permissions,
      agent_ids: draft.targets[AGENT_ACCESS_KEY] ?? [],
      business_view_ids: draft.targets[BUSINESS_VIEW_ACCESS_KEY] ?? [],
    }),
};

function agentTargetItem(item: AgentAccessTarget): RolePermissionTargetItem {
  return {
    id: item.id,
    name: item.name,
    description: item.description ?? undefined,
    // 有効なエージェントには状態を出さず、無効だけを示す。
    status: item.status === "disabled" ? t("security.permissions.agentDisabled") : undefined,
  };
}

/**
 * エージェントと業務ビューの候補は `/security/access-targets` の 1 回の応答から作る。共通画面は 1 回の読み込みで
 * 対象ごとに同じ signal を渡すため、signal ごとに応答を共有する（表示の更新では signal が変わり取り直す）。
 */
export function accessTargetsLoader(fetchTargets: (signal: AbortSignal) => Promise<AccessTargetsData>) {
  const inFlight = new WeakMap<AbortSignal, Promise<AccessTargetsData>>();
  return (signal: AbortSignal) => {
    const cached = inFlight.get(signal);
    if (cached) return cached;
    const request = fetchTargets(signal);
    inFlight.set(signal, request);
    return request;
  };
}

const grantsAllByAdmin = (effective: ReadonlySet<string>) => effective.has(CAPABILITY_PERMISSIONS.admin);

/** エージェント / 業務ビューの対象範囲（RolePermissionsPage の targets）。 */
export function agentPermissionTargets(
  loadTargets: (signal: AbortSignal) => Promise<AccessTargetsData>,
): RolePermissionTargetSection<SecurityRole>[] {
  return [
    {
      key: AGENT_ACCESS_KEY,
      messages: {
        title: t("security.permissions.agents"),
        hint: t("security.permissions.agentsHint"),
        all: t("security.permissions.agentsAll"),
        searchLabel: t("security.permissions.agentsSearch"),
        searchPlaceholder: t("security.permissions.agentsSearchPlaceholder"),
        empty: t("security.permissions.agentsEmpty"),
        noResults: t("security.permissions.agentsNoResults"),
        loadWarning: t("security.permissions.agentsLoadWarning"),
        grantsAllByPermission: t("security.permissions.agentsManagedAll"),
        grantsAllSystemAdmin: t("security.permissions.agentsSystemAdmin"),
      },
      load: ({ signal }) => loadTargets(signal).then((data) => data.agents.map(agentTargetItem)),
      selectedIds: (role) => role.agent_ids ?? [],
      grantsAll: grantsAllByAdmin,
    },
    {
      key: BUSINESS_VIEW_ACCESS_KEY,
      messages: {
        title: t("security.permissions.businessViews"),
        hint: t("security.permissions.businessViewsHint"),
        all: t("security.permissions.businessViewsAll"),
        searchLabel: t("security.permissions.businessViewsSearch"),
        searchPlaceholder: t("security.permissions.businessViewsSearchPlaceholder"),
        empty: t("security.permissions.businessViewsEmpty"),
        noResults: t("security.permissions.businessViewsNoResults"),
        loadWarning: t("security.permissions.businessViewsLoadWarning"),
        grantsAllByPermission: t("security.permissions.businessViewsManagedAll"),
        grantsAllSystemAdmin: t("security.permissions.businessViewsSystemAdmin"),
      },
      // Agent に業務ビューのマスタはなく、backend は名前 = ID で返す。
      load:({ signal }) =>
        loadTargets(signal).then((data) => data.business_views.map((item) => ({ id: item.id, name: item.name }))),
      selectedIds: (role) => role.business_view_ids ?? [],
      grantsAll: grantsAllByAdmin,
      // 一覧（Run に現れた ID とロールに割り当て済みの ID）にない業務ビューも ID を入力して足せる。
      allowCustomIds: {
        label: t("security.permissions.businessViewsCustomLabel"),
        placeholder: t("security.permissions.businessViewsCustomPlaceholder"),
        hint: t("security.permissions.businessViewsCustomHint"),
        addLabel: t("security.permissions.businessViewsCustomAdd"),
        pattern: BUSINESS_VIEW_ID_PATTERN,
        invalidMessage: t("security.permissions.businessViewsCustomInvalid"),
        customStatus: t("security.permissions.businessViewsCustomStatus"),
      },
    },
  ];
}
