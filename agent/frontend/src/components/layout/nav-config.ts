import {
  Bot,
  Database,
  History,
  KeyRound,
  LayoutDashboard,
  PlayCircle,
  Settings,
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
      { href: APP_ROUTES.tools, labelKey: "nav.tools", icon: Wrench },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History },
    ],
  },
  {
    titleKey: "nav.section.settings",
    items: [
      { href: APP_ROUTES.settingsConnection, labelKey: "nav.settingsConnection", icon: KeyRound },
      { href: APP_ROUTES.settingsModel, labelKey: "nav.settingsModel", icon: Settings },
      { href: APP_ROUTES.settingsDatabase, labelKey: "nav.settingsDatabase", icon: Database },
    ],
  },
];
