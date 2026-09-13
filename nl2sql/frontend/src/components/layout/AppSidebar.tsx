import { useCallback, useMemo } from "react";
import { Bug, KeyRound } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";

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
  const navigate = useNavigate();
  const auth = useAuth();
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
  const handleLogout = () => void auth.logout().finally(() => navigate(APP_ROUTES.login, { replace: true }));
  const passwordChangeActive =
    pathname === APP_ROUTES.passwordChange || pathname.startsWith(`${APP_ROUTES.passwordChange}/`);

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
        auth.user ? (
          <SidebarAccountFooter
            name={auth.user.display_name}
            roles={t("auth.sidebar.roles", { roles: auth.user.role_codes.join(", ") })}
            collapsed={collapsed}
            labels={{ logout: t("auth.sidebar.logout"), switchToLight: "", switchToDark: "" }}
            // ローカル DEBUG はログインしていないため、パスワード変更・ログアウトの代わりに状態を示す。
            notice={auth.user.debug_mode ? <DebugModeNotice collapsed={collapsed} /> : undefined}
            actions={
              !auth.user.debug_mode && auth.user.password_change_allowed !== false
                ? [
                    {
                      id: "password-change",
                      label: t("auth.sidebar.password"),
                      icon: KeyRound,
                      active: passwordChangeActive,
                      onClick: () => navigate(APP_ROUTES.passwordChange),
                    },
                  ]
                : []
            }
            onLogout={auth.user.debug_mode ? undefined : handleLogout}
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
