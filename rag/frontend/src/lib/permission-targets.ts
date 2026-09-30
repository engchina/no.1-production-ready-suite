import {
  arrangePermissionsByNav,
  permissionNavSections,
  type PermissionDefinition,
  type RolePermissionTargetItem,
  type RolePermissionTargetSection,
  type RolePermissionsApi,
} from "@engchina/production-ready-system-settings";

import { NAV_SECTIONS } from "@/components/layout/nav-config";
import type { AccessTarget, AccessTargetsData, SecurityRole } from "./api";
import { t } from "./i18n";
import { CAPABILITY_PERMISSIONS } from "./permissions";
import { securityApi } from "./security-api";

/**
 * 権限管理画面（共通の RolePermissionsPage）へ渡す RAG の保存 API と「利用できる対象」（#214）。
 * 業務ビューは `rag.business_views.manage`、KB は `rag.knowledge_bases.manage` を持つロールで全件が対象。
 */

/** 対象の key。要素 ID とテスト ID は `security-roles-<key>-*` になる。 */
export const BUSINESS_VIEW_ACCESS_KEY = "business-view-access";
export const KNOWLEDGE_BASE_ACCESS_KEY = "knowledge-base-access";

/**
 * 権限管理の機能の一覧は、左のナビ（nav-config の NAV_SECTIONS）を正本にして、グループ・並び順・名前を
 * そろえる（#567）。ナビに無い権限（画面の中の操作を許可する capability の「管理権限」）は、ナビの後ろに
 * backend のカタログのまま置く。
 */
export function arrangeRagPermissions(catalog: readonly PermissionDefinition[]): PermissionDefinition[] {
  return arrangePermissionsByNav(catalog, permissionNavSections(NAV_SECTIONS, t));
}

export const PERMISSIONS_API: RolePermissionsApi<SecurityRole> = {
  roles: (includeArchived, options) => securityApi.roles(includeArchived, options),
  permissions: (options) => securityApi.permissions(options).then(arrangeRagPermissions),
  // 機能権限と業務ビュー / KB の対象範囲を RAG の保存 API（PUT /access）へ送る。
  // 全件が対象のとき（SYSTEM_ADMIN・rag.*.manage）は共通画面が空の一覧を渡す。
  save: (role, draft) =>
    securityApi.updateRoleAccess({
      role_id: role.role_id,
      version: role.version,
      permissions: draft.permissions,
      business_view_ids: draft.targets[BUSINESS_VIEW_ACCESS_KEY] ?? [],
      knowledge_base_ids: draft.targets[KNOWLEDGE_BASE_ACCESS_KEY] ?? [],
    }),
};

function toTargetItem(item: AccessTarget): RolePermissionTargetItem {
  return {
    id: item.id,
    name: item.name,
    description: item.description ?? undefined,
    // 有効な対象には状態を出さず、アーカイブ済みだけを示す。
    status: item.status === "ARCHIVED" ? t("security.permissions.targetArchived") : undefined,
  };
}

/**
 * 業務ビューと KB の候補は `/security/access-targets` の 1 回の応答から作る。共通画面は 1 回の読み込みで
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

/** 業務ビュー / KB の対象範囲（RolePermissionsPage の targets）。 */
export function ragPermissionTargets(
  loadTargets: (signal: AbortSignal) => Promise<AccessTargetsData>,
): RolePermissionTargetSection<SecurityRole>[] {
  return [
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
      load: ({ signal }) => loadTargets(signal).then((data) => data.business_views.map(toTargetItem)),
      selectedIds: (role) => role.business_view_ids ?? [],
      grantsAll: (effective) => effective.has(CAPABILITY_PERMISSIONS.businessViewsManage),
    },
    {
      key: KNOWLEDGE_BASE_ACCESS_KEY,
      messages: {
        title: t("security.permissions.knowledgeBases"),
        hint: t("security.permissions.knowledgeBasesHint"),
        all: t("security.permissions.knowledgeBasesAll"),
        searchLabel: t("security.permissions.knowledgeBasesSearch"),
        searchPlaceholder: t("security.permissions.knowledgeBasesSearchPlaceholder"),
        empty: t("security.permissions.knowledgeBasesEmpty"),
        noResults: t("security.permissions.knowledgeBasesNoResults"),
        loadWarning: t("security.permissions.knowledgeBasesLoadWarning"),
        grantsAllByPermission: t("security.permissions.knowledgeBasesManagedAll"),
        grantsAllSystemAdmin: t("security.permissions.knowledgeBasesSystemAdmin"),
      },
      load: ({ signal }) => loadTargets(signal).then((data) => data.knowledge_bases.map(toTargetItem)),
      selectedIds: (role) => role.knowledge_base_ids ?? [],
      grantsAll: (effective) => effective.has(CAPABILITY_PERMISSIONS.knowledgeBasesManage),
    },
  ];
}
