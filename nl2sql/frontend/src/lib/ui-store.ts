import { createUiStore } from "@engchina/production-ready-ui";

// UI ストア（サイドバー開閉等）。永続化キーは NL2SQL 専用 namespace。
export const UI_STORAGE_KEY = "production-ready-nl2sql.ui";

export const useUiStore = createUiStore({
  storageKey: UI_STORAGE_KEY,
  mobileBreakpoint: 640,
});
