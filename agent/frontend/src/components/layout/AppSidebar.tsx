import { useEffect, useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { useSidebarAccount } from "@engchina/production-ready-system-settings";

import {
  Sidebar as UiSidebar,
  SidebarAccountFooter,
  type NavSection as UiNavSection,
  type SidebarLabels,
} from "@engchina/production-ready-ui";

import { useAuth } from "@/components/security/AuthProvider";
import { t } from "@/lib/i18n";
import { confirmPendingLeave } from "@/lib/leave-guard";
import { APP_ROUTES } from "@/lib/routes";
import { useUiStore } from "@/lib/ui-store";
import { visibleNavSections } from "./nav-config";

/**
 * Agent コンソールのサイドナビ。共有 UI パッケージの <Sidebar> に
 * i18n / router / 権限 / 状態ストア / nav 構成を注入する（RAG / NL2SQL と同一パターン）。
 * 権限のない項目と、項目が 0 件になったセクションは出さない（#215）。
 */
export function AppSidebar() {
  const { pathname } = useLocation();
  const { hasPermission } = useAuth();
  // 表示名・ロール・パスワード変更・ログアウトは共通の helper が作る（#220）。
  const account = useSidebarAccount({
    routes: { login: APP_ROUTES.login, passwordChange: APP_ROUTES.passwordChange },
  });
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const setSidebarCollapsed = useUiStore((state) => state.setSidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const collapsedSections = useUiStore((state) => state.collapsedSections);
  const toggleSection = useUiStore((state) => state.toggleSection);
  const setSectionCollapsed = useUiStore((state) => state.setSectionCollapsed);

  const sections = useMemo<UiNavSection[]>(
    () =>
      visibleNavSections(hasPermission).map((section) => ({
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
    [hasPermission]
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

  // ローカル（ログイン省略）はログインしていないため、アカウント欄を出さない（agent/AGENTS.md）。
  const logout = account?.onLogout;
  const footer =
    account && !account.debugMode ? (
      <SidebarAccountFooter
        name={account.name}
        roles={account.roles}
        collapsed={collapsed}
        labels={account.labels}
        actions={account.actions}
        onLogout={
          logout
            ? () => {
                // ログアウトは画面を離れる操作。未保存の編集があれば先に確認する。
                void confirmPendingLeave().then((confirmed) => {
                  if (confirmed) logout();
                });
              }
            : undefined
        }
      />
    ) : undefined;

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }

    const mediaQuery = window.matchMedia("(max-width: 640px)");
    const collapseForMobile = () => {
      if (mediaQuery.matches) {
        setSidebarCollapsed(true);
      }
    };

    collapseForMobile();
    mediaQuery.addEventListener("change", collapseForMobile);
    return () => mediaQuery.removeEventListener("change", collapseForMobile);
  }, [setSidebarCollapsed]);

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
      footer={footer}
    />
  );
}
