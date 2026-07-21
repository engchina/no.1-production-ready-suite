// 検索クエリ(自然言語)の穴埋めテンプレート。sqlTemplates.ts(DML テンプレート)と対の定義。
// 値は空欄のまま挿入し、スキーマ参照パネルのカーソル挿入でテーブル名・列名を埋める想定。
export const QUESTION_TEMPLATES: Array<{ labelKey: string; body: string }> = [
  // 先頭は「自由入力」。body 空でクリック時に検索クエリを空へ戻す(テンプレ未適用の初期状態)。
  {
    labelKey: "nl2sql.question.template.default",
    body: "",
  },
  {
    labelKey: "nl2sql.question.template.basic",
    body: "対象テーブル：\n抽出項目：\n抽出条件：",
  },
  {
    labelKey: "nl2sql.question.template.aggregate",
    body: "対象テーブル：\n集計内容（件数・合計・平均など）：\n集計単位（グループ化）：\n抽出条件：",
  },
  {
    labelKey: "nl2sql.question.template.topn",
    body: "対象テーブル：\n抽出項目：\n並び替え（項目と昇順／降順）：\n表示件数（上位N件）：\n抽出条件：",
  },
  {
    labelKey: "nl2sql.question.template.join",
    body: "対象テーブル（複数可）：\nテーブル間の関連：\n抽出項目：\n抽出条件：",
  },
];
