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

function applyTheme(pref: ThemePreference) {
  if (typeof document === "undefined") return;
  const dark = resolveDark(pref);
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  // ネイティブコントロール（scrollbar 等）も追従させる。
  root.style.colorScheme = dark ? "dark" : "light";
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
