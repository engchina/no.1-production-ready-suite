import { useCallback, useMemo } from "react";
import { Bug } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import { useSidebarAccount } from "@engchina/production-ready-system-settings";

import {
  Sidebar as UiSidebar,
  SidebarAccountFooter,
  cn,
  type NavSection as UiNavSection,
  type SidebarLabels,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { useAuth } from "@/features/security/AuthProvider";
import { NAV_SECTIONS, resolveCollapsedSections } from "./nav-config";

/**
 * NL2SQL コンソールのサイドナビ。共有 UI パッケージの <Sidebar> に
 * i18n / router / 状態ストア / nav 構成を注入する NL2SQL shell。
 */
export function AppSidebar() {
  const { pathname } = useLocation();
  const auth = useAuth();
  // 表示名・ロール・パスワード変更・ログアウトは共通の helper が作る（#220）。
  const account = useSidebarAccount({
    routes: { login: APP_ROUTES.login, passwordChange: APP_ROUTES.passwordChange },
  });
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const savedCollapsedSections = useUiStore((state) => state.collapsedSections);
  const setSectionCollapsed = useUiStore((state) => state.setSectionCollapsed);
  const collapsedSections = useMemo(
    () => resolveCollapsedSections(savedCollapsedSections),
    [savedCollapsedSections]
  );
  const toggleSection = useCallback(
    (key: string) => setSectionCollapsed(key, !collapsedSections[key]),
    [collapsedSections, setSectionCollapsed]
  );

  const sections = useMemo<UiNavSection[]>(
    () =>
      NAV_SECTIONS.map((section) => ({
        key: section.titleKey,
        title: t(section.titleKey),
        collapsible: section.collapsible,
        items: section.items.filter((item) => auth.hasPermission(item.permission)).map((item) => ({
          href: item.href,
          label: t(item.labelKey),
          sidebarLabel: item.sidebarLabelKey ? t(item.sidebarLabelKey) : undefined,
          icon: item.icon,
        })),
      })).filter((section) => section.items.length > 0),
    [auth]
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
      footer={
        account ? (
          <SidebarAccountFooter
            name={account.name}
            roles={account.roles}
            collapsed={collapsed}
            labels={account.labels}
            // ローカル DEBUG はログインしていないため、パスワード変更・ログアウトの代わりに状態を示す。
            notice={account.debugMode ? <DebugModeNotice collapsed={collapsed} /> : undefined}
            actions={account.actions}
            onLogout={account.onLogout}
          />
        ) : null
      }
    />
  );
}

function DebugModeNotice({ collapsed }: { collapsed: boolean }) {
  const label = t("auth.sidebar.debugMode");
  return (
    <div
      className={cn("sidebar-debug-status flex min-h-9 items-center gap-2 rounded-md border", collapsed ? "justify-center px-1" : "px-2 py-1.5")}
      role="status"
      aria-label={label}
      title={collapsed ? label : undefined}
    >
      <Bug size={16} className="shrink-0" aria-hidden />
      {!collapsed ? <span className="text-xs leading-4">{label}</span> : null}
    </div>
  );
}
