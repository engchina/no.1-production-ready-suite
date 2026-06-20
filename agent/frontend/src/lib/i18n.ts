// 最小 i18n。日本語第一。UI 文言はここ経由で参照し、ハードコードしない。
const ja = {
  "app.title": "Production Ready Agent",
  "app.sidebarTitle.line1": "Production Ready",
  "app.sidebarTitle.line2": "Agent",

  "nav.sidebar.aria": "サイドナビゲーション",
  "nav.sidebar.expand": "サイドバーを展開",
  "nav.sidebar.collapse": "サイドバーを折りたたむ",
  "nav.command.open": "コマンドパレットを開く",
  "nav.section.containsActive": "現在地を含む",

  "nav.section.overview": "概要",
  "nav.section.runtime": "実行",
  "nav.section.settings": "システム設定",

  "nav.dashboard": "ダッシュボード",
  "nav.agents": "エージェント一覧",
  "nav.runs": "実行 (Runs)",
  "nav.tools": "ツール",
  "nav.history": "実行履歴",
  "nav.settingsConnection": "接続設定",
  "nav.settingsModel": "モデル",
  "nav.settingsDatabase": "データベース",

  "page.dashboard.subtitle": "エージェントの稼働状況と主要導線",
  "page.runs.subtitle": "エージェントの実行を起動・監視する",
  "page.settings.subtitle": "接続・モデル・データベースの設定",

  "common.empty.title": "まだデータがありません",
  "common.empty.hint": "バックエンド接続後にここへ表示されます。",
} as const;

export type I18nKey = keyof typeof ja;

/** 文言を取得する。`{name}` プレースホルダを params で置換する。 */
export function t(key: I18nKey, params?: Record<string, string | number>): string {
  let value: string = ja[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      value = value.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return value;
}
