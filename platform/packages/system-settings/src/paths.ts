import { BrainCog, Cloud, Database, KeyRound, Palette, type LucideIcon } from "lucide-react";

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
