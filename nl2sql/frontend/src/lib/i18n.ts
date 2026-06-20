// 最小 i18n。日本語第一。UI 文言はここ経由で参照し、ハードコードしない。
const ja = {
  "app.title": "Production Ready NL2SQL",
  "app.sidebarTitle.line1": "Production Ready",
  "app.sidebarTitle.line2": "NL2SQL",

  "nav.sidebar.aria": "サイドナビゲーション",
  "nav.sidebar.expand": "サイドバーを展開",
  "nav.sidebar.collapse": "サイドバーを折りたたむ",
  "nav.command.open": "コマンドパレットを開く",
  "nav.section.containsActive": "現在地を含む",

  "nav.section.data": "データ",
  "nav.section.nl2sql": "NL2SQL",
  "nav.section.settings": "システム設定",

  "nav.dashboard": "ダッシュボード",
  "nav.schema": "スキーマ管理",
  "nav.query": "クエリ生成 (NL2SQL)",
  "nav.history": "実行履歴",
  "nav.evaluation": "NL2SQL 評価",
  "nav.settingsConnection": "接続設定",
  "nav.settingsModel": "モデル",
  "nav.settingsDatabase": "データベース",

  "page.dashboard.subtitle": "NL2SQL の利用状況と主要導線",
  "page.query.subtitle": "自然言語から SQL を生成して実行する",
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
