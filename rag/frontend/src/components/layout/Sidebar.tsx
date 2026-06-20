import { useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { LogOut, UserRound } from "lucide-react";

import {
  Sidebar as UiSidebar,
  type NavSection as UiNavSection,
  type SidebarLabels,
} from "@engchina/production-ready-ui";

import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { NAV_SECTIONS } from "./nav-config";
import { OPEN_COMMAND_PALETTE_EVENT } from "./CommandPalette";

/**
 * RAG コンソールのサイドナビ。
 * 構造・挙動は共有 UI パッケージの <Sidebar> に集約し、ここでは RAG 固有の
 * i18n / router(Link, useLocation) / auth / 状態ストア / nav 構成を注入する。
 */
export function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const collapsedSections = useUiStore((state) => state.collapsedSections);
  const toggleSection = useUiStore((state) => state.toggleSection);
  const setSectionCollapsed = useUiStore((state) => state.setSectionCollapsed);

  // NAV_SECTIONS（i18n キー保持）→ 解決済みラベルの NavSection へ変換。
  // セクションキーは従来どおり titleKey を使い、永続化済みの折りたたみ状態と整合させる。
  const sections = useMemo<UiNavSection[]>(
    () =>
      NAV_SECTIONS.map((section) => ({
        key: section.titleKey,
        title: t(section.titleKey),
        collapsible: section.collapsible,
        items: section.items.map((item) => ({
          href: item.href,
          label: t(item.labelKey),
          sidebarLabel: item.sidebarLabelKey ? t(item.sidebarLabelKey) : undefined,
          icon: item.icon,
        })),
      })),
    []
  );

  const labels: SidebarLabels = {
    aria: t("nav.sidebar.aria"),
    expand: t("nav.sidebar.expand"),
    collapse: t("nav.sidebar.collapse"),
    commandOpen: t("nav.command.open"),
    sectionContainsActive: t("nav.section.containsActive"),
    sectionToggleExpand: (section) => t("nav.section.toggle.expand", { section }),
    sectionToggleCollapse: (section) => t("nav.section.toggle.collapse", { section }),
  };

  async function handleLogout() {
    await auth.logout();
    navigate(APP_ROUTES.login, { replace: true });
  }

  const footer = auth.authRequired ? (
    <>
      <div
        className={cn(
          "sidebar-reveal mb-2 flex min-h-11 items-center gap-2.5 overflow-hidden rounded-md px-3 py-2 text-sm text-sidebar-foreground/90",
          collapsed && "h-0 min-h-0 py-0"
        )}
        aria-hidden={collapsed}
      >
        <UserRound className="shrink-0" size={18} aria-hidden />
        <div className="min-w-0">
          <div className="truncate font-medium text-white">
            {auth.user?.name ?? t("auth.user.unknown")}
          </div>
          <div className="truncate text-xs text-sidebar-foreground/70">
            {auth.user?.role ?? t("auth.user.role")}
          </div>
        </div>
      </div>
      <button
        type="button"
        className={cn(
          "flex h-11 min-h-11 w-full cursor-pointer items-center overflow-hidden rounded-md text-sm text-sidebar-foreground transition-colors hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-60",
          collapsed ? "justify-center px-0" : "gap-2.5 px-3 py-2"
        )}
        onClick={() => void handleLogout()}
        disabled={auth.isLoggingOut}
        aria-label={collapsed ? t("auth.logout") : undefined}
        title={collapsed ? t("auth.logout") : undefined}
      >
        <LogOut className="shrink-0" size={18} aria-hidden />
        <span
          className={cn(
            "sidebar-reveal min-w-0 truncate whitespace-nowrap leading-5",
            collapsed && "w-0"
          )}
          aria-hidden={collapsed}
        >
          {t("auth.logout")}
        </span>
      </button>
    </>
  ) : undefined;

  return (
    <UiSidebar
      sections={sections}
      currentPath={pathname}
      title={{
        line1: t("app.sidebarTitle.line1"),
        line2: t("app.sidebarTitle.line2"),
        full: t("app.title"),
      }}
      collapsed={collapsed}
      onToggleCollapsed={toggleSidebarCollapsed}
      collapsedSections={collapsedSections}
      onToggleSection={toggleSection}
      onSetSectionCollapsed={setSectionCollapsed}
      linkComponent={Link}
      labels={labels}
      onOpenCommandPalette={() => window.dispatchEvent(new Event(OPEN_COMMAND_PALETTE_EVENT))}
      footer={footer}
    />
  );
}
