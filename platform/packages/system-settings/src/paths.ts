/**
 * 共有システム設定画面のパス。3製品で同じ URL にそろえる（#70）。
 * 各製品の lib/routes.ts はこの値を参照する。
 */
export const SYSTEM_SETTINGS_PATHS = {
  appearance: "/settings/appearance",
} as const;
