import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  ChevronDown,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Search,
  UserRound,
} from "lucide-react";

import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { NAV_SECTIONS } from "./nav-config";
import { OPEN_COMMAND_PALETTE_EVENT } from "./CommandPalette";

/**
 * 折りたたみ可能なサイドナビ（参照実装の sideTabBar 構造を踏襲）。
 */
export function Sidebar() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const auth = useAuth();
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const collapsedSections = useUiStore((state) => state.collapsedSections);
  const toggleSection = useUiStore((state) => state.toggleSection);
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
        <button
          type="button"
          onClick={() => window.dispatchEvent(new Event(OPEN_COMMAND_PALETTE_EVENT))}
          aria-label={t("nav.command.open")}
          title={t("nav.command.open")}
          className={cn(
            "mb-3 flex h-9 min-h-9 w-full items-center overflow-hidden rounded-md border border-white/10 text-sm text-sidebar-foreground/80 transition-colors hover:bg-white/10 hover:text-white",
            collapsed ? "justify-center px-0" : "gap-2 px-3"
          )}
        >
          <Search className="shrink-0" size={16} aria-hidden />
          <span
            className={cn(
              "sidebar-reveal min-w-0 flex-1 truncate text-left",
              collapsed && "w-0"
            )}
            aria-hidden={collapsed}
          >
            {t("nav.command.open")}
          </span>
          <kbd
            className={cn(
              "sidebar-reveal shrink-0 rounded border border-white/20 px-1.5 py-0.5 text-[10px] font-medium text-sidebar-foreground/70",
              collapsed && "hidden"
            )}
            aria-hidden
          >
            ⌘K
          </kbd>
        </button>
        {NAV_SECTIONS.map((section) => {
          const sectionTitle = t(section.titleKey);
          const containsActive = section.items.some(
            (item) => pathname === item.href || pathname.startsWith(item.href + "/")
          );
          // セクション開閉は展開幅サイドバーでのみ作用（icon-only 幅は常に全表示）。
          const collapsible = section.collapsible !== false && !collapsed;
          // 現在地を含むセクションは必ず展開し、現在のページが隠れないよう保証する。
          const sectionExpanded =
            !collapsible || containsActive || !collapsedSections[section.titleKey];
          const regionId = `nav-section-${sectionId(section.titleKey)}`;
          return (
            <div key={section.titleKey} className={cn(collapsed ? "mb-3" : "mb-4")}>
              {collapsible ? (
                <button
                  type="button"
                  className="sidebar-reveal flex w-full items-center justify-between gap-2 rounded-md px-3 py-1 text-xs font-semibold uppercase tracking-wide transition-colors hover:bg-white/10 [--sidebar-reveal-opacity:0.6]"
                  aria-expanded={sectionExpanded}
                  aria-controls={regionId}
                  aria-label={t(
                    sectionExpanded ? "nav.section.toggle.collapse" : "nav.section.toggle.expand",
                    { section: sectionTitle }
                  )}
                  onClick={() => toggleSection(section.titleKey)}
                >
                  <span className="truncate">{sectionTitle}</span>
                  <ChevronDown
                    size={14}
                    aria-hidden
                    className={cn(
                      "shrink-0 transition-transform duration-200 ease-out motion-reduce:transition-none",
                      sectionExpanded ? "rotate-0" : "-rotate-90"
                    )}
                  />
                </button>
              ) : (
                <div
                  className={cn(
                    "sidebar-reveal px-3 py-1 text-xs font-semibold uppercase tracking-wide [--sidebar-reveal-opacity:0.6]",
                    collapsed && "sr-only"
                  )}
                >
                  {sectionTitle}
                </div>
              )}
              <div
                id={regionId}
                className={cn(
                  "grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
                  sectionExpanded ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
                )}
                inert={sectionExpanded ? undefined : true}
              >
                <ul
                  className={cn(
                    "min-h-0 space-y-1 overflow-hidden",
                    collapsed ? "pt-0" : "pt-1",
                    // 折りたたみ時は visibility:hidden で a11y ツリー / タブ順 / 視認から除外。
                    // 高さは grid-template-rows でアニメーションするため見た目はスムーズに閉じる。
                    !sectionExpanded && "invisible"
                  )}
                >
                  {section.items.map((item) => {
                    const active =
                      pathname === item.href || pathname.startsWith(item.href + "/");
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
                            active ? "bg-sidebar-active text-white" : "hover:bg-white/10"
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
            </div>
          );
        })}
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

/** i18n キー（`nav.section.pipeline` 等）を DOM id 用の安全な slug に変換する。 */
function sectionId(titleKey: string): string {
  return titleKey.replace(/[^a-zA-Z0-9]+/g, "-");
}
