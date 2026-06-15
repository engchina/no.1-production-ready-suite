import {
  Database,
  FlaskConical,
  FileSearch,
  FileStack,
  Library,
  Cloud,
  KeyRound,
  LayoutDashboard,
  Settings,
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
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical },
    ],
  },
  {
    titleKey: "nav.section.settings",
    items: [
      { href: APP_ROUTES.settingsOci, labelKey: "nav.settingsOci", icon: KeyRound },
      { href: APP_ROUTES.settingsUploadStorage, labelKey: "nav.settingsUploadStorage", icon: Cloud },
      { href: APP_ROUTES.settingsModel, labelKey: "nav.settingsModel", icon: Settings },
      { href: APP_ROUTES.settingsDatabase, labelKey: "nav.settingsDatabase", icon: Database },
    ],
  },
];
