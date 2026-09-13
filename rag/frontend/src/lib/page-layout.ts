import { APP_ROUTES } from "./routes";

/**
 * 画面幅いっぱい（共有 PageHeader / PageBody の `wide`）を使う作業画面。
 * 多列の表、会話 + 比較、プレビュー + 抽出の多ペインのように、横幅がそのまま作業量になる画面に限る。
 * RAG 検索（回答の文章が主）・ダッシュボード・フォーム・設定など、読む・入力する画面は
 * 1440px の計測幅（行が長すぎると追えない）のまま。
 * 各画面の `<PageHeader wide>` / `<PageBody wide>` とこの一覧の一致は page-layout.test.ts で検査する。
 */
export const WIDE_PAGE_ROUTES: ReadonlySet<string> = new Set([
  APP_ROUTES.chat,
  APP_ROUTES.documents,
  APP_ROUTES.fileList,
  APP_ROUTES.feedback,
]);
