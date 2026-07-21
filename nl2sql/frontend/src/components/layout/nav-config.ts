import {
  ArrowRightLeft,
  BookOpen,
  BrainCircuit,
  Cloud,
  Code2,
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
  ScrollText,
  Settings,
  Shield,
  ShieldCheck,
  Sparkles,
  Table2,
  UserCog,
  Users,
  type LucideIcon,
} from "lucide-react";

import { APP_ROUTES } from "@/lib/routes";
import type { I18nKey } from "@/lib/i18n";

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
      { href: APP_ROUTES.query, labelKey: "nav.query", icon: Sparkles, permission: "search.view" },
      {
        href: APP_ROUTES.directSql,
        labelKey: "nav.directSql",
        sidebarLabelKey: "nav.directSql.sidebar",
        icon: Code2,
        permission: "settings.database.sql_execute",
      },
      { href: APP_ROUTES.sqlAnalysis, labelKey: "nav.sqlAnalysis", icon: ShieldCheck, permission: "search.view" },
      { href: APP_ROUTES.sqlToQuestion, labelKey: "nav.sqlToQuestion", icon: ArrowRightLeft, permission: "search.view" },
      { href: APP_ROUTES.history, labelKey: "nav.history", icon: History, permission: "search.view" },
    ],
  },
  {
    titleKey: "nav.section.prepare",
    items: [
      { href: APP_ROUTES.tableManagement, labelKey: "nav.tableManagement", icon: Table2, permission: "documents.view" },
      { href: APP_ROUTES.viewManagement, labelKey: "nav.viewManagement", icon: Eye, permission: "documents.view" },
      { href: APP_ROUTES.dataManagement, labelKey: "nav.dataManagement", icon: FileSpreadsheet, permission: "documents.view" },
      { href: APP_ROUTES.commentManagement, labelKey: "nav.commentManagement", icon: MessageSquareText, permission: "documents.view" },
      { href: APP_ROUTES.annotationManagement, labelKey: "nav.annotationManagement", icon: BookOpen, permission: "documents.view" },
      { href: APP_ROUTES.glossaryRules, labelKey: "nav.glossaryRules", icon: BookOpen, permission: "knowledge_bases.view" },
      { href: APP_ROUTES.globalRules, labelKey: "nav.globalRules", icon: Layers3, permission: "knowledge_bases.view" },
      { href: APP_ROUTES.sampleData, labelKey: "nav.sampleData", icon: Database, permission: "documents.view" },
    ],
  },
  {
    titleKey: "nav.section.improve",
    items: [
      { href: APP_ROUTES.profiles, labelKey: "nav.profiles", icon: UserCog, permission: "knowledge_bases.view" },
      { href: APP_ROUTES.ontologyBuild, labelKey: "nav.ontologyBuild", icon: Network, permission: "knowledge_bases.view" },
      { href: APP_ROUTES.feedbackManagement, labelKey: "nav.feedbackManagement", icon: MessageSquareText, permission: "evaluation.view" },
      { href: APP_ROUTES.questionClassifierModels, labelKey: "nav.questionClassifierModels", icon: BrainCircuit, permission: "evaluation.view" },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical, permission: "evaluation.view" },
      {
        href: APP_ROUTES.nl2sqlSettingsDatabase,
        labelKey: "nav.nl2sqlSettingsDatabase",
        sidebarLabelKey: "nav.nl2sqlSettingsDatabase.sidebar",
        icon: Database,
        permission: "settings.database.view",
      },
    ],
  },
  {
    titleKey: "nav.section.security",
    items: [
      { href: APP_ROUTES.securityUsers, labelKey: "nav.securityUsers", icon: Users, permission: "security.users.view" },
      { href: APP_ROUTES.securityRoles, labelKey: "nav.securityRoles", icon: Shield, permission: "security.roles.view" },
      { href: APP_ROUTES.securityDeepSec, labelKey: "nav.securityDeepSec", icon: ShieldCheck, permission: "security.deepsec.view" },
      { href: APP_ROUTES.securityAudit, labelKey: "nav.securityAudit", icon: ScrollText, permission: "security.audit.view" },
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
        permission: "settings.oci.view",
      },
      { href: APP_ROUTES.settingsUploadStorage, labelKey: "nav.settingsUploadStorage", icon: Cloud, permission: "settings.object_storage.view" },
      {
        href: APP_ROUTES.settingsModel,
        labelKey: "nav.settingsModel",
        sidebarLabelKey: "nav.settingsModel.sidebar",
        icon: Settings,
        permission: "settings.models.view",
      },
      {
        href: APP_ROUTES.settingsDatabase,
        labelKey: "nav.settingsDatabase",
        sidebarLabelKey: "nav.settingsDatabase.sidebar",
        icon: Database,
        permission: "settings.database.view",
      },
      { href: APP_ROUTES.settingsAppearance, labelKey: "nav.settingsAppearance", icon: Palette, permission: "dashboard.view" },
    ],
  },
];
