/**
 * 画面の配信の基点（base path。#1316）。
 *
 * 1 台の Compute に 3 製品を置く配備では、Nginx が RAG を `/rag/` の下で配信し、frontend は
 * `FRONTEND_BASE_PATH=/rag/ npm run build` で build する（Vite の `base` → `import.meta.env.BASE_URL`）。
 * ローカルの開発（`npm run dev`）・e2e・製品ごとの Compute の配備は `/` のままで、何も変わらない。
 *
 * backend へ送る request・`href` / `src` / `<iframe>` / ダウンロードの URL は、送る所・URL を作る所で
 * `appPath()` を通す（`/api/...` → `/rag/api/...`）。`withBasePath` は何度通しても 1 回だけ付き、
 * `https://`・`blob:`・`data:`・相対パスは変えない。
 *
 * 画面の path も同じ: `<a href>`（一覧の題名の `RowTitleButton` の `href` など、新しいタブ・URL のコピーで開く
 * 文書の URL）は `appPath()` を通す。React Router の `Link` の `to`・`navigate()`・`useLocation()` の path は
 * router の basename が補う / 除くので通さない（#4560）。
 */

import { routerBasename, withBasePath } from "@production-ready/ui";

/** build 時の base（`/` または `/rag/` のような形）。Vite 以外の実行環境（node の単体テスト）では `/`。 */
// `?.` は import.meta.env を持たない実行環境のため。Vite の build では値に置き換わる。
export const APP_BASE_PATH: string = import.meta.env?.BASE_URL ?? "/";

/** アプリの絶対パス（`/api/...` など）に base を付ける。base が `/` なら何も変えない。 */
export function appPath(path: string): string {
  return withBasePath(path, APP_BASE_PATH);
}

/** React Router の `basename`。base が `/` なら `undefined`（今までどおり）。 */
export const ROUTER_BASENAME: string | undefined = routerBasename(APP_BASE_PATH);
