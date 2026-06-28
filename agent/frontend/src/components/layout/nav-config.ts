import {
  BadgeCheck,
  Bot,
  Boxes,
  ClipboardList,
  DatabaseBackup,
  Cloud,
  KeyRound,
  LayoutDashboard,
  PlayCircle,
  Server,
  Settings,
  Store,
  type LucideIcon,
} from "lucide-react";

import { APP_ROUTES } from "@/lib/routes";
import type { I18nKey } from "@/lib/i18n";

export interface NavItem {
  href: string;
  labelKey: I18nKey;
  sidebarLabelKey?: I18nKey;
  icon: LucideIcon;
}

export interface NavSection {
  titleKey: I18nKey;
  items: NavItem[];
  collapsible?: boolean;
}

/** Agent コンソールのサイドナビ構成（共有 Sidebar が消費する）。 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.overview",
    items: [{ href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard }],
  },
  {
    titleKey: "nav.section.controlPlane",
    items: [
      { href: APP_ROUTES.agents, labelKey: "nav.agents", icon: Bot },
      {
        href: APP_ROUTES.skills,
        labelKey: "nav.skills",
        sidebarLabelKey: "nav.skills.sidebar",
        icon: Boxes,
      },
      { href: APP_ROUTES.runtimes, labelKey: "nav.runtimes", icon: Server },
      { href: APP_ROUTES.runs, labelKey: "nav.runs", icon: PlayCircle },
      { href: APP_ROUTES.approvals, labelKey: "nav.approvals", icon: BadgeCheck },
      { href: APP_ROUTES.audit, labelKey: "nav.audit", icon: ClipboardList },
      {
        href: APP_ROUTES.pluginMarketplaces,
        labelKey: "nav.pluginMarketplaces",
        sidebarLabelKey: "nav.pluginMarketplaces.sidebar",
        icon: Store,
      },
    ],
  },
  {
    titleKey: "nav.section.settings",
    items: [
      { href: APP_ROUTES.settingsConnection, labelKey: "nav.settingsConnection", icon: KeyRound },
      {
        href: APP_ROUTES.settingsOci,
        labelKey: "nav.settingsOci",
        sidebarLabelKey: "nav.settingsOci.sidebar",
        icon: KeyRound,
      },
      { href: APP_ROUTES.settingsUploadStorage, labelKey: "nav.settingsUploadStorage", icon: Cloud },
      {
        href: APP_ROUTES.settingsModel,
        labelKey: "nav.settingsModel",
        sidebarLabelKey: "nav.settingsModel.sidebar",
        icon: Settings,
      },
      {
        href: APP_ROUTES.settingsDatabase,
        labelKey: "nav.settingsDatabase",
        sidebarLabelKey: "nav.settingsDatabase.sidebar",
        icon: DatabaseBackup,
      },
      { href: APP_ROUTES.settingsExternalRag, labelKey: "nav.settingsExternalRag", icon: Settings },
      { href: APP_ROUTES.settingsExternalNl2Sql, labelKey: "nav.settingsExternalNl2Sql", icon: Settings },
      { href: APP_ROUTES.settingsExternalMcp, labelKey: "nav.settingsExternalMcp", icon: Settings },
      { href: APP_ROUTES.settingsRuntimeSnapshot, labelKey: "nav.settingsRuntimeSnapshot", icon: DatabaseBackup },
    ],
  },
];
