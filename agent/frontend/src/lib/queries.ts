/**
 * RAG 由来のシステム設定画面が利用する TanStack Query の key。
 * 画面と hook は platform の共有パッケージにある（#97 / #100 / #103 / #108）。
 * Agent Runtime 側の既存 query key と衝突しないよう settings 名前空間に閉じる。
 */

export const queryKeys = {
  dashboardSummary: ["dashboard", "summary"] as const,
  modelSettings: ["settings", "model"] as const,
  databaseSettings: ["settings", "database"] as const,
  adbInfo: ["settings", "database", "adb"] as const,
  uploadStorageSettings: ["settings", "upload-storage"] as const,
};
