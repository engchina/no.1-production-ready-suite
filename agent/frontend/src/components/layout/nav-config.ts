import {
  BadgeCheck,
  Bot,
  Boxes,
  ClipboardList,
  DatabaseBackup,
  Cloud,
  KeyRound,
  LayoutDashboard,
  Search,
  PlayCircle,
  ShieldCheck,
  SlidersHorizontal,
  Settings,
  Terminal,
  Wrench,
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
    items: [
      { href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard },
      { href: APP_ROUTES.agents, labelKey: "nav.agents", icon: Bot },
    ],
  },
  {
    titleKey: "nav.section.runtime",
    items: [
      { href: APP_ROUTES.runs, labelKey: "nav.runs", icon: PlayCircle },
      { href: APP_ROUTES.approvals, labelKey: "nav.approvals", icon: BadgeCheck },
      { href: APP_ROUTES.audit, labelKey: "nav.audit", icon: ClipboardList },
      { href: APP_ROUTES.tools, labelKey: "nav.tools", icon: Wrench },
      {
        href: APP_ROUTES.skills,
        labelKey: "nav.skills",
        sidebarLabelKey: "nav.skills.sidebar",
        icon: Boxes,
      },
      { href: APP_ROUTES.memory, labelKey: "nav.memory", icon: Search },
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
      { href: APP_ROUTES.settingsToolPolicy, labelKey: "nav.settingsToolPolicy", icon: ShieldCheck },
      { href: APP_ROUTES.settingsCommandPolicy, labelKey: "nav.settingsCommandPolicy", icon: Terminal },
      { href: APP_ROUTES.settingsRuntimeSafety, labelKey: "nav.settingsRuntimeSafety", icon: SlidersHorizontal },
      { href: APP_ROUTES.settingsRuntimeSnapshot, labelKey: "nav.settingsRuntimeSnapshot", icon: DatabaseBackup },
    ],
  },
];
