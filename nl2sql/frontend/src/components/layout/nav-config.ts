import {
  BookOpen,
  Bot,
  Cloud,
  Database,
  FileSpreadsheet,
  FlaskConical,
  History,
  KeyRound,
  LayoutDashboard,
  MessageSquareText,
  Settings,
  ShieldCheck,
  Sparkles,
  Table2,
  UserCog,
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
    titleKey: "nav.section.use",
    items: [
      { href: APP_ROUTES.query, labelKey: "nav.query", icon: Sparkles },
      { href: APP_ROUTES.sqlAnalysis, labelKey: "nav.sqlAnalysis", icon: ShieldCheck },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History },
    ],
  },
  {
    titleKey: "nav.section.prepare",
    items: [
      { href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard },
      { href: APP_ROUTES.schema, labelKey: "nav.schema", icon: Table2 },
      { href: APP_ROUTES.dataTools, labelKey: "nav.dataTools", icon: FileSpreadsheet },
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: UserCog },
      { href: APP_ROUTES.glossaryRules, labelKey: "nav.glossaryRules", icon: BookOpen },
    ],
  },
  {
    titleKey: "nav.section.improve",
    items: [
      { href: APP_ROUTES.learning, labelKey: "nav.learning", icon: MessageSquareText },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical },
      { href: APP_ROUTES.engineOperations, labelKey: "nav.engineOperations", icon: Bot },
      {
        href: APP_ROUTES.nl2sqlSettingsConnection,
        labelKey: "nav.nl2sqlSettingsConnection",
        sidebarLabelKey: "nav.nl2sqlSettingsConnection.sidebar",
        icon: KeyRound,
      },
      {
        href: APP_ROUTES.nl2sqlSettingsModel,
        labelKey: "nav.nl2sqlSettingsModel",
        sidebarLabelKey: "nav.nl2sqlSettingsModel.sidebar",
        icon: Settings,
      },
      {
        href: APP_ROUTES.nl2sqlSettingsDatabase,
        labelKey: "nav.nl2sqlSettingsDatabase",
        sidebarLabelKey: "nav.nl2sqlSettingsDatabase.sidebar",
        icon: Database,
      },
    ],
  },
  {
    titleKey: "nav.section.settings",
    items: [
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
        icon: Database,
      },
    ],
  },
];
