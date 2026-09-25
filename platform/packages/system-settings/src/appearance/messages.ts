/** 外観画面の既定の文言（日本語）。製品側は `messages` prop で一部だけ上書きできる。 */
export const APPEARANCE_MESSAGES = {
  title: "外観",
  subtitle: "配色テーマ（ライト / ダーク）を切り替えます。既定はライトです。",
  themeLabel: "配色テーマ",
  themeHint: "画面全体の配色を切り替えます。「自動」は OS の設定に追従します。",
  themeLight: "ライト",
  themeDark: "ダーク",
  themeSystem: "自動（OS 設定）",
};

export type AppearanceMessages = typeof APPEARANCE_MESSAGES;
