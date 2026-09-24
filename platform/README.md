# no.1-production-ready-platform

No.1 Production Ready 製品群（**RAG / NL2SQL / Agent**）が共有する **前後端の single source of truth**。
1 回の変更（トークン・コンポーネント / ログ・metrics・エラー envelope・app factory）で 3 プロジェクトを同時に底上げする。

```
packages/
  ui/             @engchina/production-ready-ui    — 共有フロント UI/UX（Vite library / React 19 / Tailwind v4）
  backend_core/   production-ready-backend-core    — 共有 FastAPI インフラ（Python 3.12 / pydantic v2 / uv）
templates/
  backend-service/  FastAPI サービス雛形（backend_core 利用）
docs/
  consume-in-ci.md  各アプリ CI から共有パッケージを解決する方法
```

- フロント標準・使い方: 本ファイル以下 + [`packages/ui/README.md`](packages/ui/README.md)
- バックエンド標準・使い方: [`docs/backend-standard.md`](docs/backend-standard.md) + [`packages/backend_core/README.md`](packages/backend_core/README.md)
- 変更運用ルール（前後端共通）: [`CONTRIBUTING.md`](CONTRIBUTING.md)

各業務 repo は自分の `features/*`・ページ・API hooks・業務文言だけを持ち、共通基盤はこの platform に集約する。

---

# @engchina/production-ready-ui（フロント共有 UI）

3 プロジェクトが共有する UI/UX の single source of truth。
デザイントークン・基本コンポーネント・アプリシェル(Sidebar / AppShell / PageHeader / Breadcrumbs)・
状態ビュー・通知機構をまとめ、同一の見た目・操作・アクセシビリティを共有する。

技術スタック: **Vite (library mode) + React 19 + TypeScript + Tailwind v4 + shadcn/ui 流コンポーネント**。
日本語第一(`Noto Sans JP` / `Roboto` / system-ui、本文 14px)。

## 構成

```
packages/ui/
  src/
    styles/tokens.css        デザイントークン(CSS 変数 + @theme + base + keyframes)
    lib/utils.ts             cn()
    components/
      ui/                    Button / Card / Switch / Skeleton / Select / Banner /
                             Toast / MessageText / ConfirmDialog / ToggleChip / FieldError / FormStatus
      feedback/              LoadingState / ErrorState / EmptyState
      data/                  StatusBadge(汎用 variant)
      app-shell/             AppShell / Sidebar / PageHeader / Breadcrumbs
    navigation/types.ts      NavItem / NavSection / NavLinkComponent / SidebarLabels
    store/                   createUiStore(factory) / toast store
    index.ts                 公開 API バレル
```

## ビルド

```bash
npm install
npm run build         # dist/index.js + dist/index.d.ts + dist/tokens.css
```

## 消費側(各アプリ)の使い方

通知・バナー・フォーム状態・確認ダイアログ・状態ビューの文字列は `MessageText` を内部利用し、
改行や連続空白を正規化したうえで、日本語・英語の文末を優先して折り返す。単独の長文や URL は
コンテナ幅内で安全に折り返す。独自の通知本文を組む場合も公開 `MessageText` を使用する。

### 1. 依存追加(開発時はローカル file: リンク)

```jsonc
// frontend/package.json
"dependencies": {
  "@engchina/production-ready-ui": "file:../../no.1-production-ready-platform/packages/ui"
}
```

> 安定後は GitHub tag / npm registry へ切替可能。

### 2. Vite 設定(必須: React 重複回避)

file: リンクは自分の `node_modules` の React を解決しうるため、必ず dedupe する:

```ts
// vite.config.ts
resolve: { dedupe: ["react", "react-dom"] }
```

### 3. スタイル取り込み

```css
/* src/globals.css */
@import "tailwindcss";
@import "@engchina/production-ready-ui/tokens.css";
/* 共有コンポーネントの utility クラスを Tailwind v4 のスキャン対象に含める */
@source "../node_modules/@engchina/production-ready-ui/dist";
```

### 4. アプリシェル

```tsx
import { AppShell, Sidebar } from "@engchina/production-ready-ui";

<AppShell sidebar={<AppSidebar />}>
  <Routes>…</Routes>
</AppShell>
```

`Sidebar` はルーター・i18n・auth・状態ストアに依存せず、すべて props で注入する
(`linkComponent` に react-router の `Link`、`labels` に翻訳済み文字列、`footer` に
ユーザー/ログアウト slot、`sections` に解決済みラベルの NavSection)。各アプリは
`AppSidebar` ラッパで自分の nav-config / i18n / ストアを束ねる(RAG / NL2SQL / Agent 同一パターン)。

## 役割分担

| 層 | 置き場所 |
|---|---|
| Design tokens / 基本コンポーネント / レイアウト / 状態・通知 | **このパッケージ** |
| アプリ固有の nav 構成・ページ・API hooks・業務文言 | 各アプリ repo |

共通 UI の変更は **必ずこのパッケージで行い** タグを切る。各アプリは `components/ui/*` を
私的にコピーしない。
