import { useMemo } from "react";
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
import { OPEN_COMMAND_PALETTE_EVENT } from "./CommandPalette";

/**
 * RAG コンソールのサイドナビ。
 * 構造・挙動は共有 UI パッケージの <Sidebar> に集約し、ここでは RAG 固有の
 * i18n / router(Link, useLocation) / 権限 / 状態ストア / nav 構成を注入する。
 * 権限のない項目と、項目が 0 件になったセクションは出さない（#214）。
 */
export function Sidebar() {
  const { pathname } = useLocation();
  const { hasPermission } = useAuth();
  // 表示名・ロール・パスワード変更・ログアウトは共通の helper が作る（#220）。
  const account = useSidebarAccount({
    routes: { login: APP_ROUTES.login, passwordChange: APP_ROUTES.passwordChange },
  });
  const collapsed = useUiStore((state) => state.sidebarCollapsed);
  const toggleSidebarCollapsed = useUiStore((state) => state.toggleSidebarCollapsed);
  const collapsedSections = useUiStore((state) => state.collapsedSections);
  const toggleSection = useUiStore((state) => state.toggleSection);
  const setSectionCollapsed = useUiStore((state) => state.setSectionCollapsed);

  // NAV_SECTIONS（i18n キー保持）→ 解決済みラベルの NavSection へ変換。
  // セクションキーは従来どおり titleKey を使い、永続化済みの折りたたみ状態と整合させる。
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

  // ローカル DEBUG（ログイン省略）はログインしていないため、今までどおりアカウント欄を出さない。
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
