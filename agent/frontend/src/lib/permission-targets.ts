import {
  arrangePermissionsByNav,
  permissionNavSections,
  type PermissionDefinition,
  type RolePermissionTargetItem,
  type RolePermissionTargetPage,
  type RolePermissionTargetQuery,
  type RolePermissionTargetSection,
  type RolePermissionsApi,
} from "@engchina/production-ready-system-settings";

import { NAV_SECTIONS } from "@/components/layout/nav-config";
import type { AccessTargetPage, AgentAccessTarget, BusinessViewAccessTarget, SecurityRole } from "./api";
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

/**
 * 権限管理の機能の一覧は、左のナビ（nav-config の NAV_SECTIONS）を正本にして、グループ・並び順・名前を
 * そろえる（#567）。ナビに無い権限（画面の中の操作を許可する capability の「実行・承認・管理の権限」）は、
 * ナビの後ろに backend のカタログのまま置く。
 */
export function arrangeAgentPermissions(catalog: readonly PermissionDefinition[]): PermissionDefinition[] {
  return arrangePermissionsByNav(catalog, permissionNavSections(NAV_SECTIONS, t));
}

export const PERMISSIONS_API: RolePermissionsApi<SecurityRole> = {
  roles: (includeArchived, options) => securityApi.roles(includeArchived, options),
  permissions: (options) => securityApi.permissions(options).then(arrangeAgentPermissions),
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

/** 候補を読む関数（`GET /api/security/access-targets/{agents,business-views}`。検索とページング。#608）。 */
export interface AccessTargetFetchers {
  agents: (query: RolePermissionTargetQuery, signal: AbortSignal) => Promise<AccessTargetPage<AgentAccessTarget>>;
  businessViews: (
    query: RolePermissionTargetQuery,
    signal: AbortSignal,
  ) => Promise<AccessTargetPage<BusinessViewAccessTarget>>;
}

const grantsAllByAdmin = (effective: ReadonlySet<string>) => effective.has(CAPABILITY_PERMISSIONS.admin);

/** エージェント / 業務ビューの対象範囲（RolePermissionsPage の targets）。候補はサーバー側で検索し、50 件ずつ読む。 */
export function agentPermissionTargets(fetchers: AccessTargetFetchers): RolePermissionTargetSection<SecurityRole>[] {
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
      query: (query, { signal }): Promise<RolePermissionTargetPage> =>
        fetchers.agents(query, signal).then((page) => ({ items: page.items.map(agentTargetItem), total: page.total })),
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
      // RAG の業務ビューを読めなかったときは、読めた候補を出したまま理由を警告で表示する（#240）。
      query: (query, { signal }): Promise<RolePermissionTargetPage> =>
        fetchers.businessViews(query, signal).then((page) => ({
          items: page.items.map((item) => ({ id: item.id, name: item.name })),
          total: page.total,
          warning: (page.warnings ?? []).join(" "),
        })),
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
