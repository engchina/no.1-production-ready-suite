import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { LogOut, type LucideIcon, Moon, PanelLeftClose, PanelLeftOpen, Sun, UserRound, X } from "lucide-react";

import { cn } from "../../lib/utils";
import type { NavLinkComponent, NavSection, SidebarLabels } from "../../navigation/types";
import { BUTTON_ICON_SIZE, Button, buttonVariants } from "../ui/button";
import { DisclosureChevron } from "../ui/disclosure-chevron";
import { FloatingActionMenu } from "../ui/floating-menu";
import { Tooltip } from "../ui/tooltip";
import { useNavDrawer, useSidebarCollapsed } from "./nav-drawer";
import { nextMenuIndex } from "./PageHeader";

export interface SidebarProps {
  /** ナビ構成（解決済みラベル）。 */
  sections: NavSection[];
  /** 現在の URL パス（react-router の useLocation().pathname 等）。 */
  currentPath: string;
  /** アプリ名（展開時に 2 行で表示 + ツールチップ）。 */
  title: { line1: string; line2: string; full: string };
  /** サイドバー全体の折りたたみ状態とハンドラ（md 以上）。md 未満のドロワーの中では常に展開して描く。 */
  collapsed: boolean;
  onToggleCollapsed: () => void;
  /**
   * セクション単位の折りたたみの保存値とハンドラ。キーの無いセクションは `NavSection.defaultCollapsed`
   * （既定は展開）に従い、見出しを押すと今の開閉の逆を `onSetSectionCollapsed` で保存する（#1563）。
   */
  collapsedSections: Record<string, boolean>;
  onSetSectionCollapsed: (key: string, collapsed: boolean) => void;
  /** ルーター非依存のリンク（react-router Link 等）。 */
  linkComponent: NavLinkComponent;
  labels: SidebarLabels;
  /** 下部のユーザー/ログアウト等のスロット（アプリ側が auth を注入）。 */
  footer?: ReactNode;
}

/**
 * 折りたたみ可能なサイドナビ（参照実装の sideTabBar 構造を踏襲）。
 * ルーター・i18n・auth・状態ストアには依存せず、すべて props で注入する。
 * md 未満では AppShell のドロワーの中に描かれ（#367）、展開した幅・閉じるボタンで描く。
 */
export function Sidebar({
  sections,
  currentPath,
  title,
  collapsed: collapsedPreference,
  onToggleCollapsed,
  collapsedSections,
  onSetSectionCollapsed,
  linkComponent: Link,
  labels,
  footer,
}: SidebarProps) {
  const nav = useNavDrawer();
  const inDrawer = Boolean(nav?.drawer);
  // ドロワーの中では常に展開する。製品の折りたたみの状態（md 以上の選好）は変えない。
  const collapsed = inDrawer ? false : collapsedPreference;
  const sidebarState = collapsed ? "collapsed" : "expanded";
  const setBrand = nav?.setBrand;
  const closeDrawer = nav?.closeDrawer;

  // ドロワーの外の上端のバーに製品名を出す（ドロワーを閉じている間も製品が分かるように）。
  useLayoutEffect(() => {
    if (inDrawer) setBrand?.({ line1: title.line1, line2: title.line2, full: title.full });
  }, [inDrawer, setBrand, title.line1, title.line2, title.full]);

  // 画面を移ったらドロワーを閉じる（フッターのパスワード変更など、リンク以外からの移動も含む）。
  useEffect(() => {
    if (inDrawer) closeDrawer?.();
  }, [currentPath, inDrawer, closeDrawer]);

  const isActive = (href: string) =>
    currentPath === href || currentPath.startsWith(href + "/");

  // 現在地を含むセクション（無ければ undefined）。
  const activeSectionKey = sections.find((section) =>
    section.items.some((item) => isActive(item.href))
  )?.key;

  // ナビゲーションで別セクションへ入ったら、そのセクションを自動展開して現在地を露出させる。
  useEffect(() => {
    if (activeSectionKey) {
      onSetSectionCollapsed(activeSectionKey, false);
    }
  }, [activeSectionKey, onSetSectionCollapsed]);

  return (
    <aside
      data-surface="inverted"
      className={cn(
        "sidebar-shell flex shrink-0 flex-col overflow-hidden bg-surface text-fg-muted",
        inDrawer
          ? "h-full w-[var(--sidebar-width)] max-w-full"
          : cn(
              "h-screen transition-[width] duration-200 ease-out motion-reduce:transition-none",
              collapsed ? "w-[var(--sidebar-width-collapsed)]" : "w-[var(--sidebar-width)]"
            )
      )}
      aria-label={labels.aria}
      data-state={sidebarState}
    >
      <div
        className={cn(
          "flex h-14 shrink-0 items-center border-b border-border",
          collapsed ? "justify-center px-2" : "justify-between px-3"
        )}
      >
        <div
          className={cn(
            "sidebar-reveal min-w-0 px-1 text-fg",
            collapsed ? "w-0 px-0" : "flex-1"
          )}
          aria-hidden={collapsed}
          title={title.full}
        >
          {/* 見出しは 14px。16px にすると長いブランド名が折りたたみボタンに重なる。
              truncate は行の高さで切り取るため、行の高さは Noto Sans JP の字面（約 1.45em）以上にし、
              g / y の下端を切らない（#658）。 */}
          <span className="block truncate whitespace-nowrap text-sm font-bold leading-6">
            {title.line1}
          </span>
          <span className="block truncate whitespace-nowrap text-xs font-semibold leading-5 text-fg-muted">
            {title.line2}
          </span>
        </div>
        {inDrawer && nav ? (
          // ドロワーの中では折りたたみの代わりに「閉じる」（Escape・scrim のタップと同じ）。
          // 見える名前は共有の Tooltip（#372。HTML の title は使わない）。
          <Tooltip content={nav.labels.close} placement="bottom">
            <button
              type="button"
              className="inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
              aria-label={nav.labels.close}
              data-testid="nav-drawer-close"
              onClick={nav.closeDrawer}
            >
              <X size={20} aria-hidden />
            </button>
          </Tooltip>
        ) : (
          <Tooltip content={collapsed ? labels.expand : labels.collapse} placement="bottom">
            <button
              type="button"
              className="inline-flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center rounded-md text-fg-muted transition-colors hover:bg-surface-hover hover:text-fg"
              aria-label={collapsed ? labels.expand : labels.collapse}
              aria-expanded={!collapsed}
              onClick={onToggleCollapsed}
            >
              {collapsed ? <PanelLeftOpen size={20} aria-hidden /> : <PanelLeftClose size={20} aria-hidden />}
            </button>
          </Tooltip>
        )}
      </div>
      <nav className={cn("flex-1 overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable] py-3", collapsed ? "px-2" : "px-3", inDrawer && "overscroll-contain")}>
        {sections.map((section) => {
          const containsActive = section.items.some((item) => isActive(item.href));
          // セクション開閉は展開幅サイドバーでのみ作用（icon-only 幅は常に全表示）。
          const collapsible = section.collapsible !== false && !collapsed;
          // 保存値が無ければ nav config の既定（defaultCollapsed）に従う（#1563）。
          const sectionExpanded =
            !collapsible || !(collapsedSections[section.key] ?? section.defaultCollapsed ?? false);
          const collapsedWithActive = collapsible && !sectionExpanded && containsActive;
          const regionId = `nav-section-${sectionId(section.key)}`;
          return (
            <div key={section.key} className={cn(collapsed ? "mb-3" : "mb-4")}>
              {collapsible ? (
                <button
                  type="button"
                  className="sidebar-reveal flex w-full items-center justify-between gap-2 rounded-md px-3 py-1 text-xs font-semibold tracking-wide transition-colors hover:bg-surface-hover [--sidebar-reveal-opacity:0.6]"
                  aria-expanded={sectionExpanded}
                  aria-controls={regionId}
                  aria-label={
                    collapsedWithActive
                      ? `${labels.sectionToggleExpand(section.title)}（${labels.sectionContainsActive}）`
                      : sectionExpanded
                        ? labels.sectionToggleCollapse(section.title)
                        : labels.sectionToggleExpand(section.title)
                  }
                  onClick={() => onSetSectionCollapsed(section.key, sectionExpanded)}
                >
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span className="truncate">{section.title}</span>
                    {collapsedWithActive ? (
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent-emphasis"
                        aria-hidden
                        title={labels.sectionContainsActive}
                      />
                    ) : null}
                  </span>
                  <DisclosureChevron expanded={sectionExpanded} size={14} />
                </button>
              ) : (
                <div
                  className={cn(
                    "sidebar-reveal px-3 py-1 text-xs font-semibold tracking-wide [--sidebar-reveal-opacity:0.6]",
                    collapsed && "sr-only"
                  )}
                >
                  {section.title}
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
                    !sectionExpanded && "invisible"
                  )}
                >
                  {section.items.map((item) => {
                    const active = isActive(item.href);
                    const Icon = item.icon;
                    const fullLabel = item.label;
                    const displayLabel = item.sidebarLabel ?? item.label;
                    const ariaLabel =
                      collapsed || displayLabel !== fullLabel ? fullLabel : undefined;
                    return (
                      <li key={item.href}>
                        <SidebarTooltip label={fullLabel} enabled={collapsed}>
                          <Link
                            to={item.href}
                            className={navRowClass(collapsed, active)}
                            aria-current={active ? "page" : undefined}
                            aria-label={ariaLabel}
                            title={collapsed ? undefined : fullLabel}
                          >
                            <NavRowContent icon={Icon} label={displayLabel} collapsed={collapsed} active={active} />
                          </Link>
                        </SidebarTooltip>
                      </li>
                    );
                  })}
                </ul>
              </div>
            </div>
          );
        })}
      </nav>
      {footer ? (
        <div className={cn("border-t border-border py-3", collapsed ? "px-2" : "px-3")}>
          {footer}
        </div>
      ) : null}
    </aside>
  );
}

/** ナビ行（リンク・フッターの行アクション共通）。現在地は塗り + 左バーで示す。 */
function navRowClass(collapsed: boolean, active: boolean) {
  return cn(
    "relative flex h-11 min-h-11 w-full items-center overflow-hidden rounded-md text-sm transition-colors",
    // フォーカスリングは base.css の :focus-visible（コントラスト優先設定で太くなる）に任せる
    collapsed ? "justify-center px-0" : "gap-2.5 px-3 py-2 text-left",
    active
      ? "bg-accent-emphasis text-fg-on-accent forced-colors:bg-[Highlight] forced-colors:text-[HighlightText] forced-colors:forced-color-adjust-none"
      : "cursor-pointer hover:bg-surface-hover hover:text-fg forced-colors:hover:bg-[Highlight] forced-colors:hover:text-[HighlightText]"
  );
}

function NavRowContent({ icon: Icon, label, collapsed, active }: { icon: LucideIcon; label: string; collapsed: boolean; active: boolean }) {
  return (
    <>
      {/* 左アクセントバー: 現在地を背景色だけに頼らず位置でも示す（color-not-only）。 */}
      {active ? <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-fg" aria-hidden /> : null}
      <Icon className="shrink-0" size={20} aria-hidden />
      {/* 行の高さは字面より高くする（leading-5 では「HuggingFace」の g の下端が切れた。#658）。 */}
      <span className={cn("sidebar-reveal min-w-0 truncate whitespace-nowrap leading-6", collapsed && "w-0")} aria-hidden={collapsed}>
        {label}
      </span>
    </>
  );
}

/** フッターの行アクション（パスワード変更など）。ナビ行と同じ見た目で、`active` は現在地（`aria-current="page"`）。 */
export interface SidebarFooterAction {
  id: string;
  /** 翻訳済みラベル。折りたたみ時はツールチップと読み上げ名になる。 */
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  active?: boolean;
}

/**
 * 利用者のメニューの「アカウント」の項目（#1873・#4724）。
 * `href` は製品の base を含む URL（`useHref`）、`onNavigate` はクライアント側の移動（修飾キーなしの左クリック）。
 */
export interface SidebarAccountLink {
  href: string;
  /** メニューの項目の文言（翻訳済み。例「アカウント」）。 */
  label: string;
  /** 今開いている画面か（欄とメニューの項目の `aria-current="page"` と、欄の強調）。 */
  active?: boolean;
  onNavigate?: () => void;
}

/**
 * サイドバー下部のアカウント領域。上から「ユーザー（name があるとき）→ notice → actions → テーマ切替」。
 * ユーザーの欄（アイコン・名前・ロール）を押すと、上に「アカウント」「ログアウト」のメニューを開く（#4724）。
 * ログアウトの操作はメニューの中だけに置くので、`onLogout` は `name` と一緒に渡す。
 * Sidebar の `footer` スロットに渡す。ラベルは翻訳済み文字列で上書きする。
 * - 認証の無いアプリ（Agent 等）は `name` を省き、テーマ切替だけを置ける。
 * - `notice` はモード表示（ローカル DEBUG など）。ログアウトを出さない場合は `onLogout` を省く。
 *   折りたたみ時の表示はアプリ側で `useSidebarCollapsed(collapsed)` の値を見て切り替える（ドロワーの中では展開）。
 */
export function SidebarAccountFooter({
  name,
  roles,
  rolesLabel,
  collapsed: collapsedPreference,
  theme = "light",
  onToggleTheme,
  onLogout,
  actions = [],
  notice,
  account,
  labels = { logout: "ログアウト", switchToLight: "ライトテーマに切り替え", switchToDark: "ダークテーマに切り替え" },
}: {
  name?: string;
  /** 2 行目に出すロール（翻訳済みの表示名）。 */
  roles?: string;
  /** ロールの行の全文（「ロール: …」など）。title と読み上げに使い、見える行は `roles` のまま。 */
  rolesLabel?: string;
  collapsed: boolean;
  /** 現在適用中のテーマ（system 設定の場合は解決後の値）。`onToggleTheme` と一緒に渡す。 */
  theme?: "light" | "dark";
  onToggleTheme?: () => void;
  onLogout?: () => void;
  actions?: SidebarFooterAction[];
  notice?: ReactNode;
  account?: SidebarAccountLink;
  /** `accountMenu` は利用者のメニューの読み上げ名の先頭（翻訳済み。例「アカウントのメニュー」）。 */
  labels?: { logout: string; switchToLight: string; switchToDark: string; accountMenu?: string };
}) {
  // ドロワーの中（md 未満）では常に展開して描く。
  const collapsed = useSidebarCollapsed(collapsedPreference);
  const themeLabel = theme === "dark" ? labels.switchToLight : labels.switchToDark;
  return (
    // grid の列は minmax(0, 1fr)（grid-cols-1）。暗黙の auto の列は項目の最小幅（nowrap の名前の全幅）まで広がり、
    // 名前の行がサイドバーの右端からはみ出して省略記号の出ないまま切り取られていた（#1644）。
    <div className="grid grid-cols-1 gap-1">
      {name && (account || onLogout) ? (
        <SidebarAccountMenu
          name={name}
          roles={roles}
          rolesLabel={rolesLabel}
          collapsed={collapsed}
          account={account}
          onLogout={onLogout}
          logoutLabel={labels.logout}
          menuLabel={labels.accountMenu}
        />
      ) : null}
      {name && !collapsed && !account && !onLogout ? (
        <div className="flex min-h-11 min-w-0 items-center gap-2.5 px-3 py-0.5" data-testid="sidebar-account">
          <SidebarAccountIdentity name={name} roles={roles} rolesLabel={rolesLabel} active={false} />
        </div>
      ) : null}
      {notice}
      {actions.map((action) => (
        <SidebarTooltip key={action.id} label={action.label} enabled={collapsed}>
          <button
            type="button"
            onClick={action.onClick}
            aria-current={action.active ? "page" : undefined}
            aria-label={collapsed ? action.label : undefined}
            className={navRowClass(collapsed, Boolean(action.active))}
          >
            <NavRowContent icon={action.icon} label={action.label} collapsed={collapsed} active={Boolean(action.active)} />
          </button>
        </SidebarTooltip>
      ))}
      {onToggleTheme ? (
        <SidebarTooltip label={themeLabel} enabled={collapsed}>
          <button
            type="button"
            onClick={onToggleTheme}
            aria-label={collapsed ? themeLabel : undefined}
            className={navRowClass(collapsed, false)}
          >
            <NavRowContent icon={theme === "dark" ? Sun : Moon} label={themeLabel} collapsed={collapsed} active={false} />
          </button>
        </SidebarTooltip>
      ) : null}
    </div>
  );
}

/**
 * 利用者の欄とメニュー（#4724。WAI-ARIA Menu Button）。開くと先頭の項目へフォーカスし、↓ ↑ Home End で移動する。
 * Escape・外側のクリック・Tab で閉じ、ボタンへ戻す。メニューは body へ Portal で描くが、React のイベントは
 * ドロワー（AppShell）へ伝わる。Escape はメニューだけを閉じるよう伝播を止め、Tab はドロワーのフォーカスの閉じ込めに任せ、
 * 「アカウント」のリンクの click はドロワーを閉じる。
 */
function SidebarAccountMenu({
  name,
  roles,
  rolesLabel,
  collapsed,
  account,
  onLogout,
  logoutLabel,
  menuLabel,
}: {
  name: string;
  roles?: string;
  rolesLabel?: string;
  collapsed: boolean;
  account?: SidebarAccountLink;
  onLogout?: () => void;
  logoutLabel: string;
  menuLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const active = Boolean(account?.active);
  const accessibleName = [menuLabel, name].filter(Boolean).join(" ");
  const items = () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);

  useEffect(() => {
    if (!open) return;
    items()[0]?.focus({ preventScroll: true });
    const closeOnOutside = (event: globalThis.MouseEvent) => {
      const target = event.target as Node;
      // メニューは body へ Portal で描くため、トリガー側（rootRef）とメニューの両方を内側として扱う。
      if (!rootRef.current?.contains(target) && !menuRef.current?.contains(target)) setOpen(false);
    };
    document.addEventListener("mousedown", closeOnOutside);
    return () => document.removeEventListener("mousedown", closeOnOutside);
  }, [open]);

  const closeAndRestoreFocus = () => {
    setOpen(false);
    triggerRef.current?.focus({ preventScroll: true });
  };

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeAndRestoreFocus();
      return;
    }
    if (event.key === "Tab") {
      // メニューは body の末尾にあるため、ボタンへ戻してから既定の Tab を進める。Shift+Tab はボタンで止める。
      if (event.shiftKey) event.preventDefault();
      closeAndRestoreFocus();
      return;
    }
    // ネイティブのリンクは Space で押されないが、menuitem は Enter と Space の両方で押せる。
    if (event.key === " " && document.activeElement instanceof HTMLAnchorElement) {
      event.preventDefault();
      document.activeElement.click();
      return;
    }
    const list = items();
    const next = nextMenuIndex(event.key, list.indexOf(document.activeElement as HTMLElement), list.length);
    if (next === null) return;
    event.preventDefault();
    list[next]?.focus({ preventScroll: true });
  };

  return (
    <div ref={rootRef} className="min-w-0">
      <SidebarTooltip label={accessibleName} enabled={collapsed}>
        <button
          ref={triggerRef}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-controls={open ? menuId : undefined}
          aria-current={active ? "page" : undefined}
          aria-label={collapsed ? accessibleName : [accessibleName, roles].filter(Boolean).join(" ")}
          // 展開時は上下の余白で 2 行の欄を 44px 以上にする。現在地は塗り + 左バー。
          className={cn(navRowClass(collapsed, active), !collapsed && "h-auto py-1", !active && "aria-expanded:bg-surface-hover")}
          // 開いている間の灰色の面の上でも、ロールの行のコントラストを保つ（README §6）。
          data-surface-tint={open && !active ? "neutral" : undefined}
          data-testid="sidebar-account"
          onClick={() => setOpen((current) => !current)}
        >
          {collapsed ? (
            <NavRowContent icon={UserRound} label={name} collapsed active={active} />
          ) : (
            <>
              {active ? <span className="absolute left-0 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-fg" aria-hidden /> : null}
              <SidebarAccountIdentity name={name} roles={roles} rolesLabel={rolesLabel} active={active} />
            </>
          )}
        </button>
      </SidebarTooltip>
      {open ? (
        <FloatingActionMenu
          id={menuId}
          open={open}
          align="start"
          ariaLabel={accessibleName}
          triggerRef={triggerRef}
          menuRef={menuRef}
          onKeyDown={onMenuKeyDown}
          className="min-w-56 gap-0.5"
        >
          {account ? (
            <a
              href={account.href}
              role="menuitem"
              aria-current={active ? "page" : undefined}
              className={cn(buttonVariants({ variant: "ghost" }), "w-full justify-start no-underline")}
              onClick={(event) => {
                closeAndRestoreFocus();
                followAccountLink(event, account);
              }}
            >
              <UserRound size={BUTTON_ICON_SIZE} aria-hidden />
              <span>{account.label}</span>
            </a>
          ) : null}
          {onLogout ? (
            <Button
              variant="ghost"
              role="menuitem"
              icon={LogOut}
              className="w-full justify-start"
              onClick={() => {
                closeAndRestoreFocus();
                onLogout();
              }}
            >
              <span>{logoutLabel}</span>
            </Button>
          ) : null}
        </FloatingActionMenu>
      ) : null}
    </div>
  );
}

/** 修飾キーなしの左クリックだけをクライアント側の移動にする（新しいタブ・ウィンドウで開く操作は既定のまま）。 */
function followAccountLink(event: MouseEvent<HTMLAnchorElement>, account: SidebarAccountLink) {
  if (!account.onNavigate) return;
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.altKey || event.ctrlKey || event.shiftKey) return;
  event.preventDefault();
  account.onNavigate();
}

/**
 * アカウントの欄の中身（アイコン・名前・ロール）。
 * 1 行目は名前、2 行目は補足（ロール）。各行は 1 行で省略記号で切り、高さを変えない（#1644）。
 * 全文は各行の title（Tooltip はフォーカスできるトリガー用。README §4「Tooltip」）。DOM の文字は省略しないので、
 * 読み上げは常に全文になる。truncate は行の高さで切り取るため、行の高さは字面（約 1.45em）以上にする（名前 14px は
 * leading-6 = 21px、ロール 12px は leading-5 = 17.5px。#658）。リンクの欄は上下 4px の余白で 45.5px（修正前の 50px より低い）。
 * 現在地（アカウントの画面を開いている）のときは、塗りの上の文字の色にする。
 */
function SidebarAccountIdentity({
  name,
  roles,
  rolesLabel,
  active,
}: {
  name: string;
  roles?: string;
  rolesLabel?: string;
  active: boolean;
}) {
  return (
    <>
      {/* 利用者のアイコンは名前と役割の 2 行のまとまりの中央に置く（ナビの行のアイコン。文字の 1 行目にそろえる規則の例外。#2371）。 */}
      <UserRound className="shrink-0" size={20} aria-hidden data-icon-align-skip="" />
      <div className="min-w-0 flex-1">
        <div className={cn("truncate whitespace-nowrap text-sm font-medium leading-6", active ? "text-fg-on-accent" : "text-fg")} title={name}>
          {name}
        </div>
        {roles ? (
          <div
            className={cn("truncate whitespace-nowrap text-xs leading-5", active ? "text-fg-on-accent" : "text-fg-subtle")}
            title={rolesLabel ?? roles}
          >
            {rolesLabel ? (
              <>
                {/* 読み上げは「ロール: …」の全文、見える行は表示名だけ。 */}
                <span className="sr-only">{rolesLabel}</span>
                <span aria-hidden>{roles}</span>
              </>
            ) : (
              roles
            )}
          </div>
        ) : null}
      </div>
    </>
  );
}

/** キー文字列を DOM id 用の安全な slug に変換する。 */
function sectionId(key: string): string {
  return key.replace(/[^a-zA-Z0-9]+/g, "-");
}

/**
 * icon-only 幅のサイドバー項目に表示するツールチップ。
 * サイドバーは横方向 overflow-hidden のため、右側へ出すラベルが clip される。
 * これを避けるため portal で document.body へ描画し、fixed 配置で trigger の右に出す。
 * 視覚補助のため `aria-hidden`（読み上げは trigger の `aria-label` が担う）。
 * hover とキーボード focus の双方で表示する（hover 依存にしない）。
 */
function SidebarTooltip({
  label,
  enabled,
  children,
}: {
  label: string;
  enabled: boolean;
  children: ReactNode;
}) {
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);

  if (!enabled) {
    return <>{children}</>;
  }

  function show(target: HTMLElement) {
    const rect = target.getBoundingClientRect();
    setCoords({ top: rect.top + rect.height / 2, left: rect.right + 10 });
  }

  function hide() {
    setCoords(null);
  }

  return (
    <span
      className="block"
      onMouseEnter={(event) => show(event.currentTarget)}
      onMouseLeave={hide}
      onFocus={(event) => show(event.currentTarget)}
      onBlur={hide}
    >
      {children}
      {coords
        ? createPortal(
            <div
              role="tooltip"
              aria-hidden
              data-surface="inverted"
              className="pointer-events-none fixed z-[var(--z-popover)] max-w-[16rem] -translate-y-1/2 truncate whitespace-nowrap rounded-md bg-surface-overlay px-2.5 py-1.5 text-xs font-medium text-fg shadow-[var(--shadow-popover)] ring-1 ring-border"
              style={{ top: coords.top, left: coords.left }}
            >
              {label}
            </div>,
            document.body
          )
        : null}
    </span>
  );
}
