import type { ThemePreference } from "@engchina/production-ready-ui";

import { useUiStore } from "@/lib/ui-store";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function prefersDark(): boolean {
  return typeof window !== "undefined" && window.matchMedia(DARK_QUERY).matches;
}

/** テーマ選好を実効値（dark 真偽）へ解決する。"system" は OS 設定に追従。 */
export function resolveDark(pref: ThemePreference): boolean {
  if (pref === "dark") return true;
  if (pref === "light") return false;
  return prefersDark();
}

/**
 * テーマ切替の瞬間だけ CSS transition を止める。
 * 行・ボタン等の `transition-colors`（150ms）が旧テーマの色から補間し、ダークへ切り替えた直後に
 * 明るい地と暗い地の中間色が一瞬描かれる（NL2SQL #571: 選択行が明るく読みにくく見えた原因）。
 * テーマは状態の切替であって動きではないため、補間せずに一度で切り替える。
 */
const THEME_SWITCH_STYLE = "*,*::before,*::after{transition:none!important}";

function applyTheme(pref: ThemePreference) {
  if (typeof document === "undefined") return;
  const dark = resolveDark(pref);
  const root = document.documentElement;
  if (root.classList.contains("dark") === dark) return;
  const pause = document.createElement("style");
  pause.textContent = THEME_SWITCH_STYLE;
  document.head.appendChild(pause);
  // color-scheme（ネイティブコントロールと light-dark() トークン）は共有 CSS の .dark が切り替える。
  root.classList.toggle("dark", dark);
  // transition を止めたまま新しいテーマの computed style を確定させてから戻す。
  void window.getComputedStyle(root).color;
  window.setTimeout(() => pause.remove(), 1);
}

/**
 * 永続化されたテーマ選好を即時適用し、store 変更と OS 設定変更を購読する。
 * FOUC を避けるため main.tsx から React 描画前に1回だけ呼ぶ。
 */
export function initTheme() {
  applyTheme(useUiStore.getState().theme);
  useUiStore.subscribe((state) => applyTheme(state.theme));
  if (typeof window !== "undefined") {
    window.matchMedia(DARK_QUERY).addEventListener("change", () => {
      if (useUiStore.getState().theme === "system") applyTheme("system");
    });
  }
}
