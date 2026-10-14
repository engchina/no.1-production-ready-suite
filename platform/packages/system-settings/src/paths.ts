import {
  BrainCog,
  Cloud,
  Database,
  KeyRound,
  KeySquare,
  Palette,
  Shield,
  Users,
  type LucideIcon,
} from "lucide-react";

/**
 * 共有システム設定画面のパス。3製品で同じ URL にそろえる（#70）。
 * 各製品の lib/routes.ts はこの値を参照する。
 */
export const SYSTEM_SETTINGS_PATHS = {
  oci: "/settings/oci",
  uploadStorage: "/settings/upload-storage",
  model: "/settings/model",
  database: "/settings/database",
  appearance: "/settings/appearance",
} as const;

export type SystemSettingsKey = keyof typeof SYSTEM_SETTINGS_PATHS;

export interface SystemSettingsNavItem {
  key: SystemSettingsKey;
  href: string;
  /** 製品の i18n の key（3製品で同じ key を持つ。文言は製品側で上書きできる）。 */
  labelKey: string;
  sidebarLabelKey?: string;
  icon: LucideIcon;
}

/**
 * サイドナビ「システム設定」の共通5項目（#116）。項目・並び順・アイコンは共有パッケージが決め、
 * 各製品の nav config はこれをそのまま使う（NL2SQL は各項目に menu 権限を付ける）。
 */
export const SYSTEM_SETTINGS_NAV_ITEMS = [
  {
    key: "oci",
    href: SYSTEM_SETTINGS_PATHS.oci,
    labelKey: "nav.settingsOci",
    sidebarLabelKey: "nav.settingsOci.sidebar",
    icon: KeyRound,
  },
  {
    key: "uploadStorage",
    href: SYSTEM_SETTINGS_PATHS.uploadStorage,
    labelKey: "nav.settingsUploadStorage",
    icon: Cloud,
  },
  {
    key: "model",
    href: SYSTEM_SETTINGS_PATHS.model,
    labelKey: "nav.settingsModel",
    sidebarLabelKey: "nav.settingsModel.sidebar",
    icon: BrainCog,
  },
  {
    key: "database",
    href: SYSTEM_SETTINGS_PATHS.database,
    labelKey: "nav.settingsDatabase",
    sidebarLabelKey: "nav.settingsDatabase.sidebar",
    icon: Database,
  },
  {
    key: "appearance",
    href: SYSTEM_SETTINGS_PATHS.appearance,
    labelKey: "nav.settingsAppearance",
    icon: Palette,
  },
] as const satisfies readonly SystemSettingsNavItem[];

/**
 * 共有のユーザー管理・ロール管理画面のパス（#206）。NL2SQL の既存 URL をそのまま使う。
 * ロールに付ける権限の管理は製品固有の画面（NL2SQL は権限管理）で行う。
 */
export const USER_ROLE_PATHS = {
  users: "/settings/security/users",
  roles: "/settings/security/roles",
} as const;

export type UserRoleKey = keyof typeof USER_ROLE_PATHS;

export interface UserRoleNavItem {
  key: UserRoleKey;
  href: string;
  labelKey: string;
  icon: LucideIcon;
}

/** サイドナビ「ユーザーとロール」の共通2項目。製品の nav config はこれをそのまま使う。 */
export const USER_ROLE_NAV_ITEMS = [
  { key: "users", href: USER_ROLE_PATHS.users, labelKey: "nav.securityUsers", icon: Users },
  { key: "roles", href: USER_ROLE_PATHS.roles, labelKey: "nav.securityRoles", icon: Shield },
] as const satisfies readonly UserRoleNavItem[];

/**
 * API キー（外部の MCP クライアント向け。RAG / NL2SQL だけ。#1805）の画面のパス。
 * `mine` は自分のキー（アカウントのメニューから開く）、`admin` は全員のキー（「ユーザーとロール」）。
 */
export const API_KEY_PATHS = {
  mine: "/account/api-keys",
  admin: "/settings/security/api-keys",
} as const;

/**
 * サイドナビ「ユーザーとロール」の末尾に置く「API キー」（管理の画面。RAG / NL2SQL だけ。#1805）。
 * KeyRound は共通の OCI 認証が使うため、別のアイコンにする（#658）。
 */
export const API_KEY_ADMIN_NAV_ITEM = {
  key: "apiKeys",
  href: API_KEY_PATHS.admin,
  labelKey: "nav.securityApiKeys",
  icon: KeySquare,
} as const;
