# フロントエンドのローカルフォント

Noto Sans JP / Roboto は Fontsource パッケージ（各 5.3.0）として
`npm ci` 時にローカルの `node_modules/@fontsource/` へダウンロードする。
`src/main.tsx` が通常体の 400 / 500 / 600 / 700 を読み込み、Vite が CSS と
フォント実体を `dist/assets/` に同梱する。実行時のフォント取得はアプリと
同一 origin へのリクエストのみで、Google Fonts / CDN や端末への事前インストールを必要としない。

日本語の文字範囲別サブセットは CSS の `unicode-range` により必要な分だけ取得する。
`font-display: swap` で初回ロード中も文字を表示する。
共有 UI の `"Noto Sans JP", "Roboto", system-ui, ...` の優先順位を維持し、
表頭もグローバルフォントを継承する。英数字も Noto Sans JP に収録される文字は同フォントが優先される。

フォントのライセンス原文は `public/fonts/licenses/` に保管し、production build にも同梱する。
更新時は package / lockfile とライセンス原文を合わせて更新する。

検証:

```bash
npm run build
npx playwright test tests/e2e/profile-archive-reset.spec.ts --grep 'ローカルフォント'
```

Playwright は外部 origin を遮断し、両フォントの全使用ウェイトのロード、
日本語表頭の実描画フォント、desktop / mobile-375 の配置とキーボード操作を確認する。
