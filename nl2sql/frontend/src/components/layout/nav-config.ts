import {
  BookA,
  Boxes,
  BrainCircuit,
  BriefcaseBusiness,
  Eye,
  FileCode2,
  FileKey,
  FileSpreadsheet,
  FlaskConical,
  History,
  LockKeyhole,
  MessageSquareCode,
  MessageSquareText,
  MessagesSquare,
  Network,
  ScrollText,
  Shapes,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Table2,
  TableProperties,
  Tags,
  ThumbsUp,
  type LucideIcon,
} from "lucide-react";

import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import type { I18nKey } from "@/lib/i18n";
import {
  SYSTEM_SETTINGS_NAV_ITEMS,
  USER_ROLE_NAV_ITEMS,
  type SystemSettingsKey,
  type UserRoleKey,
} from "@engchina/production-ready-system-settings";

import { APP_ROUTES } from "@/lib/routes";

export interface NavItem {
  href: string;
  labelKey: I18nKey;
  sidebarLabelKey?: I18nKey;
  icon: LucideIcon;
  permission: string;
}

export interface NavSection {
  titleKey: I18nKey;
  items: NavItem[];
  collapsible?: boolean;
  /** 保存値がない場合に、このセクションを初期状態で折りたたむか。 */
  initiallyCollapsed?: boolean;
}

/** 共通5項目（共有パッケージ）に付ける NL2SQL の menu 権限。 */
const SYSTEM_SETTINGS_MENU_PERMISSIONS = {
  oci: MENU_PERMISSIONS.settingsOci,
  uploadStorage: MENU_PERMISSIONS.settingsUploadStorage,
  model: MENU_PERMISSIONS.settingsModel,
  database: MENU_PERMISSIONS.settingsDatabase,
  appearance: MENU_PERMISSIONS.settingsAppearance,
} satisfies Record<SystemSettingsKey, string>;

/** 共通2項目（ユーザー管理・ロール管理）に付ける NL2SQL の menu 権限（#206）。 */
const USER_ROLE_MENU_PERMISSIONS = {
  users: MENU_PERMISSIONS.securityUsers,
  roles: MENU_PERMISSIONS.securityRoles,
} satisfies Record<UserRoleKey, string>;

/**
 * NL2SQL コンソールのサイドナビ構成（共有 Sidebar が消費する）。下部の並びは 3 製品で同じ
 * （改善・運用 → セキュリティ設定 → 共通のユーザーとロール → 運用設定 → 共通のシステム設定。#658）。
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.use",
    initiallyCollapsed: false,
    items: [
      { href: APP_ROUTES.chat, labelKey: "nav.chat", icon: MessagesSquare, permission: MENU_PERMISSIONS.chat },
      { href: APP_ROUTES.query, labelKey: "nav.query", icon: Sparkles, permission: MENU_PERMISSIONS.query },
      {
        href: APP_ROUTES.directSql,
        labelKey: "nav.directSql",
        icon: FileCode2,
        permission: MENU_PERMISSIONS.directSql,
      },
      { href: APP_ROUTES.sqlToQuestion, labelKey: "nav.sqlToQuestion", icon: MessageSquareCode, permission: MENU_PERMISSIONS.sqlToQuestion },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History, permission: MENU_PERMISSIONS.history },
    ],
  },
  {
    titleKey: "nav.section.prepare",
    initiallyCollapsed: true,
    items: [
      {
        href: APP_ROUTES.adminSql,
        labelKey: "nav.adminSql",
        icon: SquareTerminal,
        permission: MENU_PERMISSIONS.adminSql,
      },
      { href: APP_ROUTES.tableManagement, labelKey: "nav.tableManagement", icon: Table2, permission: MENU_PERMISSIONS.tableManagement },
      { href: APP_ROUTES.viewManagement, labelKey: "nav.viewManagement", icon: Eye, permission: MENU_PERMISSIONS.viewManagement },
      { href: APP_ROUTES.dataManagement, labelKey: "nav.dataManagement", icon: FileSpreadsheet, permission: MENU_PERMISSIONS.dataManagement },
      { href: APP_ROUTES.commentManagement, labelKey: "nav.commentManagement", icon: MessageSquareText, permission: MENU_PERMISSIONS.commentManagement },
      { href: APP_ROUTES.annotationManagement, labelKey: "nav.annotationManagement", icon: Tags, permission: MENU_PERMISSIONS.annotationManagement },
      { href: APP_ROUTES.domainManagement, labelKey: "nav.domainManagement", icon: Shapes, permission: MENU_PERMISSIONS.domainManagement },
      { href: APP_ROUTES.glossaryRules, labelKey: "nav.glossaryRules", icon: BookA, permission: MENU_PERMISSIONS.glossaryRules },
      { href: APP_ROUTES.globalRules, labelKey: "nav.globalRules", icon: ScrollText, permission: MENU_PERMISSIONS.globalRules },
      { href: APP_ROUTES.sampleData, labelKey: "nav.sampleData", icon: Boxes, permission: MENU_PERMISSIONS.sampleData },
    ],
  },
  {
    titleKey: "nav.section.improve",
    initiallyCollapsed: true,
    items: [
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: BriefcaseBusiness, permission: MENU_PERMISSIONS.profiles },
      { href: APP_ROUTES.ontologyBuild, labelKey: "nav.ontologyBuild", icon: Network, permission: MENU_PERMISSIONS.ontologyBuild },
      { href: APP_ROUTES.feedbackManagement, labelKey: "nav.feedbackManagement", icon: ThumbsUp, permission: MENU_PERMISSIONS.feedbackManagement },
      { href: APP_ROUTES.questionClassifierModels, labelKey: "nav.questionClassifierModels", icon: BrainCircuit, permission: MENU_PERMISSIONS.questionClassifierModels },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical, permission: MENU_PERMISSIONS.evaluation },
    ],
  },
  {
    // NL2SQL 固有のセキュリティ（ロールへの権限付与と DeepSec。#206）。
    titleKey: "nav.section.security",
    initiallyCollapsed: true,
    items: [
      { href: APP_ROUTES.securityPermissions, labelKey: "nav.securityPermissions", icon: LockKeyhole, permission: MENU_PERMISSIONS.securityPermissions },
      { href: APP_ROUTES.securityDeepSec, labelKey: "nav.securityDeepSec", icon: ShieldCheck, permission: MENU_PERMISSIONS.securityDeepSec },
    ],
  },
  {
    // 3製品で共通のユーザー管理・ロール管理（画面は platform の共有パッケージ。#206）。
    titleKey: "nav.section.userRoles",
    initiallyCollapsed: true,
    items: USER_ROLE_NAV_ITEMS.map((item) => ({
      ...item,
      permission: USER_ROLE_MENU_PERMISSIONS[item.key],
    })),
  },
  {
    // NL2SQL 固有の運用設定（#81）。
    titleKey: "nav.section.operations",
    initiallyCollapsed: true,
    items: [
      {
        href: APP_ROUTES.settingsSystemTables,
        labelKey: "nav.settingsSystemTables",
        sidebarLabelKey: "nav.settingsSystemTables.sidebar",
        icon: TableProperties,
        permission: MENU_PERMISSIONS.settingsSystemTables,
      },
      // Select AI Credential はデータベース設定から分けた運用の操作（#658）。
      {
        href: APP_ROUTES.settingsSelectAiCredential,
        labelKey: "nav.settingsSelectAiCredential",
        sidebarLabelKey: "nav.settingsSelectAiCredential.sidebar",
        icon: FileKey,
        permission: MENU_PERMISSIONS.settingsSelectAiCredential,
      },
    ],
  },
  {
    // 3製品で共通のシステム設定（画面は platform の共有パッケージ。#70）。
    titleKey: "nav.section.settings",
    initiallyCollapsed: true,
    items: SYSTEM_SETTINGS_NAV_ITEMS.map((item) => ({
      ...item,
      permission: SYSTEM_SETTINGS_MENU_PERMISSIONS[item.key],
    })),
  },
];

/**
 * 保存済みの開閉状態をナビ構成の初期値へ重ねる。
 * 明示保存された false（展開）も維持し、新設セクションだけ構成値へ安全に追従させる。
 */
export function resolveCollapsedSections(
  savedSections: Record<string, boolean>
): Record<string, boolean> {
  const defaults = Object.fromEntries(
    NAV_SECTIONS.map((section) => [section.titleKey, section.initiallyCollapsed ?? false])
  );
  return { ...defaults, ...savedSections };
}
