import {
  BriefcaseBusiness,
  ClipboardCheck,
  FileCog,
  FileSearch,
  FileStack,
  FlaskConical,
  HardDriveDownload,
  Layers3,
  LayoutGrid,
  Library,
  LockKeyhole,
  MessagesSquare,
  NotebookPen,
  ScanText,
  Scissors,
  Search,
  Server,
  ShieldAlert,
  TableProperties,
  ThumbsUp,
  Upload,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

import {
  SYSTEM_SETTINGS_NAV_ITEMS,
  USER_ROLE_NAV_ITEMS,
  type HasPermission,
  type SystemSettingsKey,
  type UserRoleKey,
} from "@production-ready/system-settings";

import { APP_ROUTES } from "@/lib/routes";
import { ja, type I18nKey } from "@/lib/i18n";
import { MENU_PERMISSIONS } from "@/lib/permissions";

export interface NavItem {
  href: string;
  labelKey: I18nKey;
  sidebarLabelKey?: I18nKey;
  icon: LucideIcon;
  /** 画面を開くのに必要なメニュー権限（ナビの表示とルートの保護に使う。#214）。 */
  permission: string;
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

/** 共通5項目（共有パッケージ）に付ける RAG の menu 権限（NL2SQL と同じコード）。 */
const SYSTEM_SETTINGS_MENU_PERMISSIONS = {
  oci: MENU_PERMISSIONS.settingsOci,
  uploadStorage: MENU_PERMISSIONS.settingsUploadStorage,
  model: MENU_PERMISSIONS.settingsModel,
  database: MENU_PERMISSIONS.settingsDatabase,
  appearance: MENU_PERMISSIONS.settingsAppearance,
} satisfies Record<SystemSettingsKey, string>;

/** 共通2項目（ユーザー管理・ロール管理）に付ける RAG の menu 権限。 */
const USER_ROLE_MENU_PERMISSIONS = {
  users: MENU_PERMISSIONS.securityUsers,
  roles: MENU_PERMISSIONS.securityRoles,
} satisfies Record<UserRoleKey, string>;

/**
 * RAG コンソールのサイドナビ構成。並び方は NL2SQL と同じ
 * （製品のセクション → 改善・運用 → セキュリティ設定 → 共通のユーザーとロール → 運用設定 → 共通のシステム設定。#658）。
 */
export const NAV_SECTIONS: NavSection[] = [
  {
    // 日常の利用の画面。対話のチャットを先頭に置く（`/` はナビの順で最初に開ける画面へ移るので、既定はチャット。#399）。
    titleKey: "nav.section.use",
    items: [
      { href: APP_ROUTES.chat, labelKey: "nav.chat", icon: MessagesSquare, permission: MENU_PERMISSIONS.chat },
      { href: APP_ROUTES.search, labelKey: "nav.search", icon: FileSearch, permission: MENU_PERMISSIONS.search },
    ],
  },
  {
    titleKey: "nav.section.ingestion",
    items: [
      {
        href: APP_ROUTES.upload,
        labelKey: "nav.upload",
        sidebarLabelKey: "nav.upload.sidebar",
        icon: Upload,
        permission: MENU_PERMISSIONS.upload,
      },
      { href: APP_ROUTES.fileList, labelKey: "nav.fileList", icon: FileStack, permission: MENU_PERMISSIONS.fileList },
      { href: APP_ROUTES.knowledgeBases, labelKey: "nav.knowledgeBases", icon: Library, permission: MENU_PERMISSIONS.knowledgeBases },
      // 検索・回答プロファイルはナレッジベースの上に検索の範囲を切り出すので、ナレッジベースの直下に置く（#402）。
      {
        href: APP_ROUTES.searchAnswerProfiles,
        labelKey: "nav.searchAnswerProfiles",
        sidebarLabelKey: "nav.searchAnswerProfiles.sidebar",
        icon: BriefcaseBusiness,
        permission: MENU_PERMISSIONS.searchAnswerProfiles,
      },
    ],
  },
  {
    // RAG の検索・回答設定を、利用者が理解しやすい処理順で並べる。「設定の概要」の工程の番号と同じ順番
    // （ナレッジ構築の工程 → 検索・回答の工程。PipelineHubClient の INGESTION_HREFS）にする（#267）。
    titleKey: "nav.section.pipeline",
    items: [
      {
        href: APP_ROUTES.settingsPipeline,
        labelKey: "nav.settingsPipeline",
        icon: LayoutGrid,
        permission: MENU_PERMISSIONS.settingsPipeline,
      },
      {
        href: APP_ROUTES.settingsPreprocess,
        labelKey: "nav.settingsPreprocess",
        sidebarLabelKey: "nav.settingsPreprocess.sidebar",
        icon: FileCog,
        permission: MENU_PERMISSIONS.settingsPreprocess,
      },
      {
        href: APP_ROUTES.settingsParserAdapters,
        labelKey: "nav.settingsParserAdapters",
        sidebarLabelKey: "nav.settingsParserAdapters.sidebar",
        icon: ScanText,
        permission: MENU_PERMISSIONS.settingsParserAdapters,
      },
      {
        href: APP_ROUTES.settingsChunking,
        labelKey: "nav.settingsChunking",
        sidebarLabelKey: "nav.settingsChunking.sidebar",
        icon: Scissors,
        permission: MENU_PERMISSIONS.settingsChunking,
      },
      {
        href: APP_ROUTES.settingsVectorIndex,
        labelKey: "nav.settingsVectorIndex",
        sidebarLabelKey: "nav.settingsVectorIndex.sidebar",
        icon: Layers3,
        permission: MENU_PERMISSIONS.settingsVectorIndex,
      },
      {
        href: APP_ROUTES.settingsGraph,
        labelKey: "nav.settingsGraph",
        icon: Waypoints,
        permission: MENU_PERMISSIONS.settingsGraph,
      },
      {
        href: APP_ROUTES.settingsRetrieval,
        labelKey: "nav.settingsRetrieval",
        sidebarLabelKey: "nav.settingsRetrieval.sidebar",
        icon: Search,
        permission: MENU_PERMISSIONS.settingsRetrieval,
      },
      {
        href: APP_ROUTES.settingsPrompts,
        labelKey: "nav.settingsPrompts",
        sidebarLabelKey: "nav.settingsPrompts.sidebar",
        icon: NotebookPen,
        permission: MENU_PERMISSIONS.settingsPrompts,
      },
      {
        href: APP_ROUTES.settingsGuardrail,
        labelKey: "nav.settingsGuardrail",
        sidebarLabelKey: "nav.settingsGuardrail.sidebar",
        icon: ShieldAlert,
        permission: MENU_PERMISSIONS.settingsGuardrail,
      },
      {
        href: APP_ROUTES.settingsEvaluation,
        labelKey: "nav.settingsEvaluation",
        sidebarLabelKey: "nav.settingsEvaluation.sidebar",
        icon: ClipboardCheck,
        permission: MENU_PERMISSIONS.settingsEvaluation,
      },
    ],
  },
  {
    // 回答の品質を確かめて直す画面。NL2SQL の「改善・運用」（nav.section.improve）と同じ名前・同じ位置
    // （利用 → 準備 → 改善 → セキュリティ設定 → ユーザーとロール → 運用設定 → システム設定）にする（#409 / #658）。
    titleKey: "nav.section.improve",
    items: [
      { href: APP_ROUTES.evaluation, labelKey: "nav.evaluation", icon: FlaskConical, permission: MENU_PERMISSIONS.evaluation },
      { href: APP_ROUTES.feedback, labelKey: "nav.feedback", icon: ThumbsUp, permission: MENU_PERMISSIONS.feedback },
    ],
  },
  {
    // RAG 固有のセキュリティ（ロールへの機能権限と検索・回答プロファイル / KB の対象範囲の付与。#214）。
    titleKey: "nav.section.security",
    items: [
      {
        href: APP_ROUTES.securityPermissions,
        labelKey: "nav.securityPermissions",
        icon: LockKeyhole,
        permission: MENU_PERMISSIONS.securityPermissions,
      },
    ],
  },
  {
    // 3製品で共通のユーザー管理・ロール管理（画面は platform の共有パッケージ。#206）。
    titleKey: "nav.section.userRoles",
    // 共有パッケージの型は platform 側の @types/react で解決されるため、icon だけ RAG の型へそろえる
    // （実体は同じ lucide-react のコンポーネント）。
    items: USER_ROLE_NAV_ITEMS.map((item) => ({
      ...item,
      icon: item.icon as LucideIcon,
      permission: USER_ROLE_MENU_PERMISSIONS[item.key],
    })),
  },
  {
    // RAG 固有の運用設定（システムテーブル、モデルの取得先、parser などのサービスの起動・停止）。
    // システムテーブルは NL2SQL と同じく先頭に置く（#658）。
    titleKey: "nav.section.operations",
    items: [
      {
        href: APP_ROUTES.settingsSystemTables,
        labelKey: "nav.settingsSystemTables",
        sidebarLabelKey: "nav.settingsSystemTables.sidebar",
        icon: TableProperties,
        permission: MENU_PERMISSIONS.settingsSystemTables,
      },
      {
        href: APP_ROUTES.settingsHuggingface,
        labelKey: "nav.settingsHuggingface",
        sidebarLabelKey: "nav.settingsHuggingface.sidebar",
        icon: HardDriveDownload,
        permission: MENU_PERMISSIONS.settingsHuggingface,
      },
      {
        href: APP_ROUTES.settingsServices,
        labelKey: "nav.settingsServices",
        sidebarLabelKey: "nav.settingsServices.sidebar",
        icon: Server,
        permission: MENU_PERMISSIONS.settingsServices,
      },
    ],
  },
  {
    // 3製品で共通のシステム設定（画面は platform の共有パッケージ。#70）。
    titleKey: "nav.section.settings",
    items: SYSTEM_SETTINGS_NAV_ITEMS.map((item) => ({
      ...item,
      icon: item.icon as LucideIcon,
      permission: SYSTEM_SETTINGS_MENU_PERMISSIONS[item.key],
    })),
  },
];

/** ナビの全項目（並び順どおり）。ルートの権限と既定の入口の判定に使う。 */
export const NAV_ITEMS: readonly NavItem[] = NAV_SECTIONS.flatMap((section) => section.items);

/**
 * 利用者に権限のある項目だけを残したナビ構成。項目が 0 件になったセクションは除く
 * （サイドナビ・検索・回答設定の概要が同じ判定を使う。#214）。
 */
export function visibleNavSections(
  hasPermission: HasPermission,
  sections: readonly NavSection[] = NAV_SECTIONS,
): NavSection[] {
  return sections
    .map((section) => ({
      ...section,
      items: section.items.filter((item) => hasPermission(item.permission)),
    }))
    .filter((section) => section.items.length > 0);
}

/**
 * ナビの画面（route）を利用者が開けるか。画面の中のリンク（レシピの「グローバル設定を開く」や
 * 設定の概要の一覧。#528）を、サイドナビと同じ権限の判定で出し分ける。ナビに無い route は開けない扱い。
 */
export function canOpenNavRoute(route: string, hasPermission: HasPermission): boolean {
  const item = NAV_ITEMS.find((candidate) => candidate.href === route);
  return item ? hasPermission(item.permission) : false;
}

/**
 * 設定画面の説明（その画面の PageHeader の subtitle）の i18n キー。`nav.settingsX` → `settings.x.subtitle`。
 * 設定の概要（PipelineHubClient）とサービス管理（#638）が工程の説明に使い、設定画面と同じ文を出す。
 * 対応するキーが無い項目は null。
 */
export function settingsSubtitleKey(item: NavItem): I18nKey | null {
  const raw = item.labelKey.replace(/^nav\.settings/, "");
  if (raw === item.labelKey || raw === "") return null;
  const key = `settings.${raw.charAt(0).toLowerCase()}${raw.slice(1)}.subtitle`;
  return key in ja ? (key as I18nKey) : null;
}
