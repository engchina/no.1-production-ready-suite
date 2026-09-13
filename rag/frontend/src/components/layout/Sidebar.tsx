import { useMemo } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  Sidebar as UiSidebar,
  SidebarAccountFooter,
  type NavSection as UiNavSection,
  type SidebarLabels,
} from "@engchina/production-ready-ui";

import { useAuth } from "@/lib/auth";
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
    <SidebarAccountFooter
      name={auth.user?.name ?? t("auth.user.unknown")}
      roles={auth.user?.role ?? t("auth.user.role")}
      collapsed={collapsed}
      theme="light"
      onLogout={() => {
        if (!auth.isLoggingOut) void handleLogout();
      }}
      labels={{
        logout: t("auth.logout"),
        switchToLight: t("theme.switchToLight"),
        switchToDark: t("theme.switchToDark"),
      }}
    />
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
