import {
  Database,
  FileSearch,
  FileStack,
  FolderTree,
  LayoutDashboard,
  Settings,
  Table2,
  Upload,
  type LucideIcon,
} from "lucide-react";

import { APP_ROUTES } from "@/lib/routes";
import type { I18nKey } from "@/lib/i18n";

export interface NavItem {
  href: string;
  labelKey: I18nKey;
  icon: LucideIcon;
}

export interface NavSection {
  titleKey: I18nKey;
  items: NavItem[];
}

/**
 * サイドナビ構成。参照実装のセクション構造（伝票登録 / データ参照 / 設定）を踏襲。
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.denpyo",
    items: [
      { href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard },
      { href: APP_ROUTES.upload, labelKey: "nav.upload", icon: Upload },
      { href: APP_ROUTES.fileList, labelKey: "nav.fileList", icon: FileStack },
    ],
  },
  {
    titleKey: "nav.section.reference",
    items: [
      { href: APP_ROUTES.search, labelKey: "nav.search", icon: FileSearch },
      { href: APP_ROUTES.categoryManagement, labelKey: "nav.categoryManagement", icon: FolderTree },
      { href: APP_ROUTES.tableBrowser, labelKey: "nav.tableBrowser", icon: Table2 },
    ],
  },
  {
    titleKey: "nav.section.settings",
    items: [
      { href: APP_ROUTES.settingsOci, labelKey: "nav.settingsOci", icon: Settings },
      { href: APP_ROUTES.settingsModel, labelKey: "nav.settingsModel", icon: Settings },
      { href: APP_ROUTES.settingsDatabase, labelKey: "nav.settingsDatabase", icon: Database },
    ],
  },
];
