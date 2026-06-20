import {
  Database,
  FlaskConical,
  FileSearch,
  FileStack,
  Library,
  Cloud,
  KeyRound,
  LayoutDashboard,
  Boxes,
  ClipboardCheck,
  Plug,
  Scissors,
  Search,
  Shuffle,
  Server,
  Settings,
  Share2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  UserCog,
  Workflow,
  Upload,
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
  /**
   * 見出しクリックでセクションを折りたたみ可能にするか（既定 true）。
   * 展開幅サイドバーでのみ作用し、icon-only 幅では無効。
   */
  collapsible?: boolean;
}

/** RAG コンソールのサイドナビ構成。 */
export const NAV_SECTIONS: NavSection[] = [
  {
    titleKey: "nav.section.ingestion",
    items: [
      { href: APP_ROUTES.dashboard, labelKey: "nav.dashboard", icon: LayoutDashboard },
      {
        href: APP_ROUTES.upload,
        labelKey: "nav.upload",
        sidebarLabelKey: "nav.upload.sidebar",
        icon: Upload,
      },
      { href: APP_ROUTES.fileList, labelKey: "nav.fileList", icon: FileStack },
      { href: APP_ROUTES.knowledgeBases, labelKey: "nav.knowledgeBases", icon: Library },
    ],
  },
  {
    titleKey: "nav.section.rag",
    items: [
      { href: APP_ROUTES.search, labelKey: "nav.search", icon: FileSearch },
      {
        href: APP_ROUTES.nl2sqlConsole,
        labelKey: "nav.nl2sqlConsole",
        sidebarLabelKey: "nav.nl2sqlConsole.sidebar",
        icon: SquareTerminal,
      },
      {
        href: APP_ROUTES.businessViews,
        labelKey: "nav.businessViews",
        sidebarLabelKey: "nav.businessViews.sidebar",
        icon: UserCog,
      },
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical },
    ],
  },
  {
    // RAG パイプラインの各段階を切り替えるアダプター群（パイプライン順に整列）。
    titleKey: "nav.section.pipeline",
    items: [
      {
        href: APP_ROUTES.settingsPreprocess,
        labelKey: "nav.settingsPreprocess",
        sidebarLabelKey: "nav.settingsPreprocess.sidebar",
        icon: Shuffle,
      },
      {
        href: APP_ROUTES.settingsParserAdapters,
        labelKey: "nav.settingsParserAdapters",
        sidebarLabelKey: "nav.settingsParserAdapters.sidebar",
        icon: Plug,
      },
      {
        href: APP_ROUTES.settingsChunking,
        labelKey: "nav.settingsChunking",
        sidebarLabelKey: "nav.settingsChunking.sidebar",
        icon: Scissors,
      },
      {
        href: APP_ROUTES.settingsVectorIndex,
        labelKey: "nav.settingsVectorIndex",
        sidebarLabelKey: "nav.settingsVectorIndex.sidebar",
        icon: Boxes,
      },
      {
        href: APP_ROUTES.settingsRetrieval,
        labelKey: "nav.settingsRetrieval",
        sidebarLabelKey: "nav.settingsRetrieval.sidebar",
        icon: Search,
      },
      {
        href: APP_ROUTES.settingsGrounding,
        labelKey: "nav.settingsGrounding",
        sidebarLabelKey: "nav.settingsGrounding.sidebar",
        icon: ShieldCheck,
      },
      {
        href: APP_ROUTES.settingsGeneration,
        labelKey: "nav.settingsGeneration",
        sidebarLabelKey: "nav.settingsGeneration.sidebar",
        icon: Sparkles,
      },
      {
        href: APP_ROUTES.settingsGuardrail,
        labelKey: "nav.settingsGuardrail",
        sidebarLabelKey: "nav.settingsGuardrail.sidebar",
        icon: ShieldAlert,
      },
      {
        href: APP_ROUTES.settingsEvaluation,
        labelKey: "nav.settingsEvaluation",
        sidebarLabelKey: "nav.settingsEvaluation.sidebar",
        icon: ClipboardCheck,
      },
      {
        href: APP_ROUTES.settingsGraph,
        labelKey: "nav.settingsGraph",
        icon: Share2,
      },
      {
        href: APP_ROUTES.settingsAgentic,
        labelKey: "nav.settingsAgentic",
        icon: Workflow,
      },
    ],
  },
  {
    // インフラ・接続まわりのシステム設定。
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
      {
        href: APP_ROUTES.settingsServices,
        labelKey: "nav.settingsServices",
        sidebarLabelKey: "nav.settingsServices.sidebar",
        icon: Server,
      },
    ],
  },
];
