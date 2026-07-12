import {
  ArrowRightLeft,
  BookOpen,
  BrainCircuit,
  Cloud,
  Database,
  Eye,
  FileSpreadsheet,
  FlaskConical,
  History,
  KeyRound,
  Layers3,
  MessageSquareText,
  Network,
  Palette,
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
      { href: APP_ROUTES.sqlToQuestion, labelKey: "nav.sqlToQuestion", icon: ArrowRightLeft },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History },
    ],
  },
  {
    titleKey: "nav.section.prepare",
    items: [
      { href: APP_ROUTES.tableManagement, labelKey: "nav.tableManagement", icon: Table2 },
      { href: APP_ROUTES.viewManagement, labelKey: "nav.viewManagement", icon: Eye },
      { href: APP_ROUTES.dataManagement, labelKey: "nav.dataManagement", icon: FileSpreadsheet },
      { href: APP_ROUTES.commentManagement, labelKey: "nav.commentManagement", icon: MessageSquareText },
      { href: APP_ROUTES.annotationManagement, labelKey: "nav.annotationManagement", icon: BookOpen },
      { href: APP_ROUTES.glossaryRules, labelKey: "nav.glossaryRules", icon: BookOpen },
      { href: APP_ROUTES.globalRules, labelKey: "nav.globalRules", icon: Layers3 },
      { href: APP_ROUTES.sampleData, labelKey: "nav.sampleData", icon: Database },
    ],
  },
  {
    titleKey: "nav.section.improve",
    items: [
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: UserCog },
      { href: APP_ROUTES.ontologyBuild, labelKey: "nav.ontologyBuild", icon: Network },
      { href: APP_ROUTES.feedbackManagement, labelKey: "nav.feedbackManagement", icon: MessageSquareText },
      { href: APP_ROUTES.questionClassifierModels, labelKey: "nav.questionClassifierModels", icon: BrainCircuit },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical },
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
      { href: APP_ROUTES.settingsAppearance, labelKey: "nav.settingsAppearance", icon: Palette },
    ],
  },
];
