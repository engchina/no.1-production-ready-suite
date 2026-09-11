# フロントエンドのローカルフォント

Noto Sans JP / Roboto / Google Sans Code は Fontsource パッケージ（各 5.3.0）として
`npm ci` 時にローカルの `node_modules/@fontsource/` へダウンロードする。
`src/main.tsx` が通常体の 400 / 500 / 600 / 700 を読み込み、Vite が CSS と
フォント実体を `dist/assets/` に同梱する。実行時のフォント取得はアプリと
同一 origin へのリクエストのみで、Google Fonts / CDN や端末への事前インストールを必要としない。

日本語の文字範囲別サブセットは CSS の `unicode-range` により必要な分だけ取得する。
`font-display: swap` で初回ロード中も文字を表示する。
共有 UI の `"Noto Sans JP", "Roboto", system-ui, ...` の優先順位を維持し、
表頭もグローバルフォントを継承する。英数字も Noto Sans JP に収録される文字は同フォントが優先される。

| 用途 | 指定 |
| --- | --- |
| 通常 UI・日本語の業務名・件数・日時・割合・経過時間・サンプル値 | `font-sans`（Noto Sans JP → Roboto） |
| SQL・コード・技術ログ・物理表/列/Schema 名・技術 ID | `font-mono`（Google Sans Code → Noto Sans JP → Roboto） |

`code` / `pre` / `kbd` / `samp` もローカルコード用フォントを使う。
Google Sans Code に日本語は含まれないため Noto Sans JP で補う。
混在する日本語と英数字の幅比は固定されないため、空白文字による表レイアウトを前提としない。
業務数字の桁揃えが必要な場合は `tabular-nums` を併用する。
別の `font-family` を画面ごとに追加せず、この二つの共通スタックを使用する。

フォントのライセンス原文は `public/fonts/licenses/` に保管し、production build にも同梱する。
更新時は package / lockfile とライセンス原文を合わせて更新する。

検証:

```bash
npm run build
npx playwright test tests/e2e/profile-archive-reset.spec.ts --grep 'ローカルフォント'
npx playwright test tests/e2e/nl2sql-workflows.spec.ts --grep 'ローカルコードフォント'
```

Playwright は外部 origin を遮断し、フォントの全使用ウェイトのロード、
日本語表頭と英日混在 SQL の実描画フォントを確認する。
主要画面では可視テキスト・入力欄を走査して上記二つのスタック以外の使用を検出し、
desktop / mobile-375 の配置とキーボード操作を確認する。
