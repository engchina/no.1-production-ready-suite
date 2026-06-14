import { Link, useLocation, useNavigate } from "react-router-dom";
import { LogOut, PanelLeftClose, PanelLeftOpen, UserRound } from "lucide-react";

import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { NAV_SECTIONS } from "./nav-config";

/**
 * 折りたたみ可能なサイドナビ（参照実装の sideTabBar 構造を踏襲）。
 */
export function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const sidebarState = collapsed ? "collapsed" : "expanded";

  async function handleLogout() {
    await auth.logout();
    navigate(APP_ROUTES.login, { replace: true });
  }

  return (
    <aside
      className={cn(
        "sidebar-shell flex h-screen shrink-0 flex-col overflow-hidden bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out motion-reduce:transition-none",
        collapsed ? "w-16" : "w-60"
      )}
      aria-label={t("nav.sidebar.aria")}
      data-state={sidebarState}
    >
      <div
        className={cn(
          "flex h-14 shrink-0 items-center border-b border-white/10",
          collapsed ? "justify-center px-2" : "justify-between px-3"
        )}
      >
        <div
          className={cn(
            "sidebar-reveal min-w-0 px-2 text-white",
            collapsed ? "w-0 px-0" : "flex-1"
          )}
          aria-hidden={collapsed}
          title={t("app.title")}
        >
          <span className="block whitespace-nowrap text-base font-bold leading-5">
            {t("app.sidebarTitle.line1")}
          </span>
          <span className="block whitespace-nowrap text-xs font-semibold leading-4 text-sidebar-foreground/80">
            {t("app.sidebarTitle.line2")}
          </span>
        </div>
        <button
          type="button"
          className="inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-sidebar-foreground/90 transition-colors hover:bg-white/10 hover:text-white"
          aria-label={collapsed ? t("nav.sidebar.expand") : t("nav.sidebar.collapse")}
          aria-expanded={!collapsed}
          title={collapsed ? t("nav.sidebar.expand") : t("nav.sidebar.collapse")}
          onClick={toggleSidebarCollapsed}
        >
          {collapsed ? <PanelLeftOpen size={18} aria-hidden /> : <PanelLeftClose size={18} aria-hidden />}
        </button>
      </div>
      <nav className={cn("flex-1 overflow-y-auto overflow-x-hidden py-3", collapsed ? "px-2" : "px-3")}>
        {NAV_SECTIONS.map((section) => (
          <div key={section.titleKey} className={cn(collapsed ? "mb-3" : "mb-4")}>
            <div
              className={cn(
                "sidebar-reveal px-3 py-1 text-xs font-semibold uppercase tracking-wide [--sidebar-reveal-opacity:0.6]",
                collapsed && "sr-only"
              )}
            >
              {t(section.titleKey)}
            </div>
            <ul className={cn("mt-1 space-y-1", collapsed && "mt-0")}>
              {section.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                const Icon = item.icon;
                const fullLabel = t(item.labelKey);
                const displayLabel = t(item.sidebarLabelKey ?? item.labelKey);
                const ariaLabel =
                  collapsed || displayLabel !== fullLabel ? fullLabel : undefined;
                return (
                  <li key={item.href}>
                    <Link
                      to={item.href}
                      className={cn(
                        "flex h-11 min-h-11 items-center overflow-hidden rounded-md text-sm transition-colors",
                        collapsed ? "justify-center px-0" : "gap-2.5 px-3 py-2",
                        active
                          ? "bg-sidebar-active text-white"
                          : "hover:bg-white/10"
                      )}
                      aria-current={active ? "page" : undefined}
                      aria-label={ariaLabel}
                      title={fullLabel}
                    >
                      <Icon className="shrink-0" size={18} aria-hidden />
                      <span
                        className={cn(
                          "sidebar-reveal min-w-0 truncate whitespace-nowrap leading-5",
                          collapsed && "w-0"
                        )}
                        aria-hidden={collapsed}
                      >
                        {displayLabel}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      {auth.authRequired ? (
        <div className={cn("border-t border-white/10 py-3", collapsed ? "px-2" : "px-3")}>
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
        </div>
      ) : null}
    </aside>
  );
}
