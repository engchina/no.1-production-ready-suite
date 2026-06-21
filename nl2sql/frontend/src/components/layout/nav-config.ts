import {
  Database,
  FlaskConical,
  History,
  KeyRound,
  LayoutDashboard,
  Settings,
  Sparkles,
  Table2,
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

/** NL2SQL コンソールのサイドナビ構成（共有 Sidebar が消費する）。 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.data",
    items: [
      { href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard },
      { href: APP_ROUTES.schema, labelKey: "nav.schema", icon: Table2 },
    ],
  },
  {
    titleKey: "nav.section.nl2sql",
    items: [
      { href: APP_ROUTES.query, labelKey: "nav.query", icon: Sparkles },
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: Settings },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical },
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
