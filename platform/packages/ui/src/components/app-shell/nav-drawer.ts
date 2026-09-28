import { createContext, useContext, useSyncExternalStore } from "react";

/**
 * md 未満（767px 以下）のナビのドロワー（#367。案 A・決定待ち）。
 * AppShell がドロワーを持ち、Sidebar / SidebarAccountFooter は context で「ドロワーの中にいるか」を知る。
 * 製品のナビの設定（nav config）とサイドバーの折りたたみ状態（ui-store）は変えない。
 */

/** ドロワーにする幅（Tailwind の md = 768px 未満）。 */
export const NAV_DRAWER_QUERY = "(max-width: 767px)";

/** ドロワーの文言（翻訳済み）。`menu` は開くボタンとドロワーの名前、`close` はドロワーの中の閉じるボタン。 */
export interface NavDrawerLabels {
  menu: string;
  close: string;
}

export const DEFAULT_NAV_DRAWER_LABELS: NavDrawerLabels = { menu: "メニュー", close: "メニューを閉じる" };

/** 製品のサイドバーの見出し（Sidebar の `title`）。ドロワーの外の上端のバーにも出す。 */
export interface NavDrawerBrand {
  line1: string;
  line2: string;
  full: string;
}

export interface NavDrawerContextValue {
  /** md 未満で、サイドバーがドロワーの中に描かれている。 */
  drawer: boolean;
  open: boolean;
  closeDrawer: () => void;
  labels: NavDrawerLabels;
  setBrand: (brand: NavDrawerBrand) => void;
}

export const NavDrawerContext = createContext<NavDrawerContextValue | null>(null);

export function useNavDrawer(): NavDrawerContextValue | null {
  return useContext(NavDrawerContext);
}

/**
 * サイドバーの中身を畳んで描くか。ドロワーの中では常に展開して描く（ラベルを出す）。
 * `collapsed` は製品の ui-store の値（md 以上の折りたたみ）。ドロワーを閉じても値は変えない。
 * サイドバーの `footer` に置く製品・共通の部品は、`collapsed` をこのフックに通してから使う。
 */
export function useSidebarCollapsed(collapsed: boolean): boolean {
  const nav = useNavDrawer();
  return nav?.drawer ? false : collapsed;
}

export function useNavDrawerMode(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(NAV_DRAWER_QUERY);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(NAV_DRAWER_QUERY).matches,
    () => false
  );
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** ドロワーの中で Tab が止まる要素（畳んだナビのセクションは `inert` なので除く）。 */
export function focusableIn(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((element) => !element.closest("[inert]"));
}

export type NavDrawerKeyAction = { type: "close" } | { type: "focus"; index: number } | null;

/**
 * ドロワーの中のキー操作。Escape で閉じ、Tab / Shift+Tab は端で反対の端へ回す（フォーカスの閉じ込め）。
 * `index` は今のフォーカスの位置（ドロワーの中の Tab が止まる要素の中で。無ければ -1）。
 */
export function navDrawerKeyAction(key: string, shiftKey: boolean, index: number, count: number): NavDrawerKeyAction {
  if (key === "Escape") return { type: "close" };
  if (key !== "Tab" || count === 0) return null;
  if (shiftKey && index <= 0) return { type: "focus", index: count - 1 };
  if (!shiftKey && (index === -1 || index === count - 1)) return { type: "focus", index: 0 };
  return null;
}

/** ドロワーの中のクリックでドロワーを閉じるか（ナビのリンクを選んだとき。今のページのリンクも閉じる）。 */
export function closesNavDrawer(target: EventTarget | null): boolean {
  return typeof (target as Element | null)?.closest === "function" && Boolean((target as Element).closest("a[href]"));
}
