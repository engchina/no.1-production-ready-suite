import { APP_ROUTES } from "./routes";

/**
 * 画面幅いっぱい（PageHeader / PageBody の `wide`）を使う作業画面。
 * SQL エディタ + 結果の表、一覧 + 詳細の 2 ペイン、グラフのように、横幅がそのまま作業量になる画面に限る。
 * 設定・セキュリティ・プロファイル・ルールなど、読む・入力する画面は 1440px の計測幅（行が長すぎると追えない）のまま。
 * 各画面の `<PageHeader wide>` / `<PageBody wide>` と、この一覧は tests/page-layout-contract.test.ts で一致を検査する。
 */
export const WIDE_PAGE_ROUTES: ReadonlySet<string> = new Set([
  APP_ROUTES.query,
  APP_ROUTES.directSql,
  APP_ROUTES.sqlToQuestion,
  APP_ROUTES.history,
  APP_ROUTES.adminSql,
  APP_ROUTES.tableManagement,
  APP_ROUTES.viewManagement,
  APP_ROUTES.dataManagement,
  APP_ROUTES.commentManagement,
  APP_ROUTES.annotationManagement,
  APP_ROUTES.sampleData,
  APP_ROUTES.ontologyBuild,
  APP_ROUTES.feedbackManagement,
]);

export function isWidePage(pathname: string): boolean {
  return WIDE_PAGE_ROUTES.has(pathname);
}
