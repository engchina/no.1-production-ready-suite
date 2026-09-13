// e2e（Playwright）からも import するため相対パスで参照する。
import { APP_ROUTES } from "./routes";

type AppRoute = (typeof APP_ROUTES)[keyof typeof APP_ROUTES];

/**
 * 本文を画面幅いっぱいに使う作業画面（共有 `PageHeader` / `PageBody` の `wide`）。
 *
 * 一覧 + 詳細の 2 ペイン、実行トレース（タイムライン）、多列の監査テーブルのように
 * 横幅がそのまま情報量になる画面だけを入れる。フォーム・設定・読む画面は既定の 1440px のまま。
 * `PageHeader` と `PageBody` には必ず同じ値を渡す（ずらすと広い画面でタイトルと本文の左端がずれる）。
 */
export const WIDE_PAGE_ROUTES: readonly AppRoute[] = [APP_ROUTES.runs, APP_ROUTES.audit];

export function isWidePage(route: AppRoute): boolean {
  return WIDE_PAGE_ROUTES.includes(route);
}
