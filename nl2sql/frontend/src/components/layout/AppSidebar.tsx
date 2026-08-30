import { useCallback, useMemo } from "react";
import { Bug, KeyRound, LogOut, UserRound, type LucideIcon } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  Sidebar as UiSidebar,
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
              <div className="space-y-1">
                {auth.user.password_change_allowed !== false ? (
                  <SidebarFooterAction
                    icon={KeyRound}
                    label={t("auth.sidebar.password")}
                    collapsed={collapsed}
                    active={passwordChangeActive}
                    onClick={() => navigate(APP_ROUTES.passwordChange)}
                  />
                ) : null}
                <SidebarFooterAction
                  icon={LogOut}
                  label={t("auth.sidebar.logout")}
                  collapsed={collapsed}
                  onClick={handleLogout}
                />
              </div>
            )}
          </div>
        ) : null
      }
    />
  );
}

interface SidebarFooterActionProps {
  icon: LucideIcon;
  label: string;
  collapsed: boolean;
  active?: boolean;
  onClick: () => void;
}

function SidebarFooterAction({
  icon: Icon,
  label,
  collapsed,
  active = false,
  onClick,
}: SidebarFooterActionProps) {
  return (
    <button
      type="button"
      className={cn(
        "relative flex h-11 min-h-11 w-full items-center overflow-hidden rounded-md text-sm transition-colors",
        collapsed ? "justify-center px-0" : "gap-2.5 px-3 py-2 text-left",
        active ? "bg-sidebar-active text-white" : "hover:bg-white/10"
      )}
      aria-current={active ? "page" : undefined}
      aria-label={label}
      title={collapsed ? label : undefined}
      onClick={onClick}
    >
      {active ? (
        <span
          className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-white"
          aria-hidden
        />
      ) : null}
      <Icon className="shrink-0" size={18} aria-hidden />
      <span
        className={cn(
          "sidebar-reveal min-w-0 truncate whitespace-nowrap leading-5",
          collapsed && "w-0"
        )}
        aria-hidden={collapsed}
      >
        {label}
      </span>
    </button>
  );
}
