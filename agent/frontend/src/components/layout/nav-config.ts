import {
  BadgeCheck,
  Blocks,
  Bot,
  ClipboardList,
  Container,
  DatabaseBackup,
  FlaskConical,
  LockKeyhole,
  PlayCircle,
  PlugZap,
  Store,
  TableProperties,
  type LucideIcon,
} from "lucide-react";

import {
  SYSTEM_SETTINGS_NAV_ITEMS,
  USER_ROLE_NAV_ITEMS,
  type HasPermission,
  type SystemSettingsKey,
  type UserRoleKey,
} from "@engchina/production-ready-system-settings";

import { APP_ROUTES } from "@/lib/routes";
import type { I18nKey } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";

export interface NavItem {
  href: string;
  labelKey: I18nKey;
  sidebarLabelKey?: I18nKey;
  icon: LucideIcon;
  /** 画面を開くのに必要なメニュー権限（ナビの表示とルートの保護に使う。#215）。 */
  permission: string;
}

export interface NavSection {
  titleKey: I18nKey;
  items: NavItem[];
  collapsible?: boolean;
}

/** 共通5項目（共有パッケージ）に付ける Agent の menu 権限（NL2SQL / RAG と同じコード）。 */
const SYSTEM_SETTINGS_MENU_PERMISSIONS = {
  oci: MENU_PERMISSIONS.settingsOci,
  uploadStorage: MENU_PERMISSIONS.settingsUploadStorage,
  model: MENU_PERMISSIONS.settingsModel,
  database: MENU_PERMISSIONS.settingsDatabase,
  appearance: MENU_PERMISSIONS.settingsAppearance,
} satisfies Record<SystemSettingsKey, string>;

/** 共通2項目（ユーザー管理・ロール管理）に付ける Agent の menu 権限。 */
const USER_ROLE_MENU_PERMISSIONS = {
  users: MENU_PERMISSIONS.securityUsers,
  roles: MENU_PERMISSIONS.securityRoles,
} satisfies Record<UserRoleKey, string>;

/**
 * Agent コンソールのサイドナビ構成（共有 Sidebar が消費する）。並び方は NL2SQL / RAG と同じ
 * （製品のセクション → 改善・運用 → セキュリティ設定 → 共通のユーザーとロール → 運用設定 →
 * 共通のシステム設定。#658 / #776）。
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.controlPlane",
    items: [
      { href: APP_ROUTES.agents, labelKey: "nav.agents", icon: Bot, permission: MENU_PERMISSIONS.agents },
      {
        href: APP_ROUTES.skills,
        labelKey: "nav.skills",
        sidebarLabelKey: "nav.skills.sidebar",
        icon: Blocks,
        permission: MENU_PERMISSIONS.skills,
      },
      { href: APP_ROUTES.runtimes, labelKey: "nav.runtimes", icon: Container, permission: MENU_PERMISSIONS.runtimes },
      { href: APP_ROUTES.runs, labelKey: "nav.runs", icon: PlayCircle, permission: MENU_PERMISSIONS.runs },
      { href: APP_ROUTES.approvals, labelKey: "nav.approvals", icon: BadgeCheck, permission: MENU_PERMISSIONS.approvals },
      { href: APP_ROUTES.audit, labelKey: "nav.audit", icon: ClipboardList, permission: MENU_PERMISSIONS.audit },
      {
        href: APP_ROUTES.pluginMarketplaces,
        labelKey: "nav.pluginMarketplaces",
        icon: Store,
        permission: MENU_PERMISSIONS.pluginMarketplaces,
      },
    ],
  },
  {
    // 業務 Agent の品質を確かめて直す画面。RAG / NL2SQL の「改善・運用」（nav.section.improve）と
    // 同じ名前・同じ位置・同じアイコン（品質評価は FlaskConical）にする（#658 / #776）。
    titleKey: "nav.section.improve",
    items: [
      {
        href: APP_ROUTES.evaluation,
        labelKey: "nav.evaluation",
        icon: FlaskConical,
        permission: MENU_PERMISSIONS.evaluation,
      },
    ],
  },
  {
    // Agent 固有のセキュリティ（ロールへの機能権限とエージェント / 業務ビューの対象範囲の付与。#215）。
    titleKey: "nav.section.security",
    items: [
      {
        href: APP_ROUTES.securityPermissions,
        labelKey: "nav.securityPermissions",
        icon: LockKeyhole,
        permission: MENU_PERMISSIONS.securityPermissions,
      },
    ],
  },
  {
    // 3製品で共通のユーザー管理・ロール管理（画面は platform の共有パッケージ。#206）。
    titleKey: "nav.section.userRoles",
    // 共有パッケージの型は platform 側の @types/react で解決されるため、icon だけ Agent の型へそろえる
    // （実体は同じ lucide-react のコンポーネント）。
    items: USER_ROLE_NAV_ITEMS.map((item) => ({
      ...item,
      icon: item.icon as LucideIcon,
      permission: USER_ROLE_MENU_PERMISSIONS[item.key],
    })),
  },
  {
    // Agent 固有の運用設定（接続先と Control Plane のバックアップ。#87）。先頭はシステムテーブル
    // （RAG / NL2SQL と同じ並びとアイコン。#658 / #751）。
    titleKey: "nav.section.operations",
    items: [
      {
        href: APP_ROUTES.settingsSystemTables,
        labelKey: "nav.settingsSystemTables",
        sidebarLabelKey: "nav.settingsSystemTables.sidebar",
        icon: TableProperties,
        permission: MENU_PERMISSIONS.settingsSystemTables,
      },
      {
        href: APP_ROUTES.settingsMcpConnections,
        labelKey: "nav.settingsMcpConnections",
        icon: PlugZap,
        permission: MENU_PERMISSIONS.settingsExternalMcp,
      },
      {
        href: APP_ROUTES.settingsRuntimeSnapshot,
        labelKey: "nav.settingsRuntimeSnapshot",
        icon: DatabaseBackup,
        permission: MENU_PERMISSIONS.settingsRuntimeSnapshot,
      },
    ],
  },
  {
    // 3製品で共通のシステム設定（画面は platform の共有パッケージ。#70）。
    titleKey: "nav.section.settings",
    items: SYSTEM_SETTINGS_NAV_ITEMS.map((item) => ({
      ...item,
      icon: item.icon as LucideIcon,
      permission: SYSTEM_SETTINGS_MENU_PERMISSIONS[item.key],
    })),
  },
];

/** ナビの全項目（並び順どおり）。ルートの権限と既定の入口の判定に使う。 */
export const NAV_ITEMS: readonly NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);

/** 利用者に権限のある項目だけを残したナビ構成。項目が 0 件になったセクションは除く（#215）。 */
export function visibleNavSections(
  hasPermission: HasPermission,
  sections: readonly NavSection[] = NAV_SECTIONS,
): NavSection[] {
  return sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => hasPermission(item.permission)),
    }))
    .filter((section) => section.items.length > 0);
}
