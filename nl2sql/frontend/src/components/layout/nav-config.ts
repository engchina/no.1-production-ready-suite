import {
  BookA,
  Boxes,
  BrainCog,
  BrainCircuit,
  Cloud,
  Database,
  Eye,
  FileCode2,
  FileSpreadsheet,
  FlaskConical,
  History,
  KeyRound,
  MessageSquareCode,
  MessageSquareText,
  Network,
  Palette,
  ScrollText,
  Shield,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Table2,
  TableProperties,
  Tags,
  ThumbsUp,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import { MENU_PERMISSIONS } from "@/features/security/menu-permissions";
import type { I18nKey } from "@/lib/i18n";
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
}

/** NL2SQL コンソールのサイドナビ構成（共有 Sidebar が消費する）。 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.use",
    items: [
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
      { href: APP_ROUTES.glossaryRules, labelKey: "nav.glossaryRules", icon: BookA, permission: MENU_PERMISSIONS.glossaryRules },
      { href: APP_ROUTES.globalRules, labelKey: "nav.globalRules", icon: ScrollText, permission: MENU_PERMISSIONS.globalRules },
      { href: APP_ROUTES.sampleData, labelKey: "nav.sampleData", icon: Boxes, permission: MENU_PERMISSIONS.sampleData },
    ],
  },
  {
    titleKey: "nav.section.improve",
    items: [
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: UserCog, permission: MENU_PERMISSIONS.profiles },
      { href: APP_ROUTES.ontologyBuild, labelKey: "nav.ontologyBuild", icon: Network, permission: MENU_PERMISSIONS.ontologyBuild },
      { href: APP_ROUTES.feedbackManagement, labelKey: "nav.feedbackManagement", icon: ThumbsUp, permission: MENU_PERMISSIONS.feedbackManagement },
      { href: APP_ROUTES.questionClassifierModels, labelKey: "nav.questionClassifierModels", icon: BrainCircuit, permission: MENU_PERMISSIONS.questionClassifierModels },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical, permission: MENU_PERMISSIONS.evaluation },
    ],
  },
  {
    titleKey: "nav.section.security",
    items: [
      { href: APP_ROUTES.securityUsers, labelKey: "nav.securityUsers", icon: Users, permission: MENU_PERMISSIONS.securityUsers },
      { href: APP_ROUTES.securityRoles, labelKey: "nav.securityRoles", icon: Shield, permission: MENU_PERMISSIONS.securityRoles },
      { href: APP_ROUTES.securityDeepSec, labelKey: "nav.securityDeepSec", icon: ShieldCheck, permission: MENU_PERMISSIONS.securityDeepSec },
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
        permission: MENU_PERMISSIONS.settingsOci,
      },
      { href: APP_ROUTES.settingsUploadStorage, labelKey: "nav.settingsUploadStorage", icon: Cloud, permission: MENU_PERMISSIONS.settingsUploadStorage },
      {
        href: APP_ROUTES.settingsModel,
        labelKey: "nav.settingsModel",
        sidebarLabelKey: "nav.settingsModel.sidebar",
        icon: BrainCog,
        permission: MENU_PERMISSIONS.settingsModel,
      },
      {
        href: APP_ROUTES.settingsDatabase,
        labelKey: "nav.settingsDatabase",
        sidebarLabelKey: "nav.settingsDatabase.sidebar",
        icon: Database,
        permission: MENU_PERMISSIONS.settingsDatabase,
      },
      {
        href: APP_ROUTES.settingsSystemTables,
        labelKey: "nav.settingsSystemTables",
        sidebarLabelKey: "nav.settingsSystemTables.sidebar",
        icon: TableProperties,
        permission: MENU_PERMISSIONS.settingsSystemTables,
      },
      { href: APP_ROUTES.settingsAppearance, labelKey: "nav.settingsAppearance", icon: Palette, permission: MENU_PERMISSIONS.settingsAppearance },
    ],
  },
];
