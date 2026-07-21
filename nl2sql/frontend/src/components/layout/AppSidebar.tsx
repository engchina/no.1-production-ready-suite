import { useMemo } from "react";
import { Bug, KeyRound, LogOut, UserRound } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  Sidebar as UiSidebar,
  type NavSection as UiNavSection,
  type SidebarLabels,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { useAuth } from "@/features/security/AuthProvider";
import { Button } from "@/components/ui/button";
import { NAV_SECTIONS } from "./nav-config";

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
  const collapsedSections = useUiStore((state) => state.collapsedSections);
  const toggleSection = useUiStore((state) => state.toggleSection);
  const setSectionCollapsed = useUiStore((state) => state.setSectionCollapsed);

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
          <div className="space-y-2">
            <div className={`flex items-center gap-2 ${collapsed ? "justify-center" : "px-1"}`}>
              <UserRound size={18} className="shrink-0" aria-hidden />
              {!collapsed ? (
                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold">{auth.user.display_name}</p>
                  <p className="truncate text-[10px] text-sidebar-foreground/70">
                    {t("auth.sidebar.roles", { roles: auth.user.role_codes.join(", ") })}
                  </p>
                </div>
              ) : null}
            </div>
            {auth.user.debug_mode ? (
              <div
                className={`sidebar-debug-status flex min-h-9 items-center gap-2 rounded-md border ${collapsed ? "justify-center px-1" : "px-2 py-1.5"}`}
                role="status"
                aria-label={t("auth.sidebar.debugMode")}
                title={collapsed ? t("auth.sidebar.debugMode") : undefined}
              >
                <Bug size={15} className="shrink-0" aria-hidden />
                {!collapsed ? (
                  <span className="text-[11px] leading-4">
                    {t("auth.sidebar.debugMode")}
                  </span>
                ) : null}
              </div>
            ) : (
              <div className={`flex gap-1 ${collapsed ? "flex-col" : "flex-wrap"}`}>
                <Button
                  size="sm"
                  variant="ghost"
                  className={collapsed ? "w-full px-0 text-sidebar-foreground" : "flex-1 text-sidebar-foreground"}
                  aria-label={t("auth.sidebar.password")}
                  onClick={() => navigate(APP_ROUTES.passwordChange)}
                >
                  <KeyRound size={14} aria-hidden />
                  {!collapsed ? t("auth.sidebar.password") : null}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  className={collapsed ? "w-full px-0 text-sidebar-foreground" : "flex-1 text-sidebar-foreground"}
                  aria-label={t("auth.sidebar.logout")}
                  onClick={() => void auth.logout().finally(() => navigate(APP_ROUTES.login, { replace: true }))}
                >
                  <LogOut size={14} aria-hidden />
                  {!collapsed ? t("auth.sidebar.logout") : null}
                </Button>
              </div>
            )}
          </div>
        ) : null
      }
    />
  );
}
