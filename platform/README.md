# platform — No.1 Production Ready 共通基盤

monorepo `no.1-production-ready-suite` の `platform/`。No.1 Production Ready 製品群（**RAG / NL2SQL / Agent**）が共有する **前後端の single source of truth**。
1 回の変更（トークン・コンポーネント / ログ・metrics・エラー envelope・app factory）で 3 プロジェクトを同時に底上げする。

```
packages/
  ui/             @engchina/production-ready-ui    — 共有フロント UI/UX（Vite library / React 19 / Tailwind v4）
  system-settings/ @engchina/production-ready-system-settings — 3製品共通のシステム設定画面（外観ほか。#70）とユーザー管理・ロール管理画面（#206）
  backend_core/   production-ready-backend-core    — 共有 FastAPI インフラ（Python 3.12 / pydantic v2 / uv）
  system_settings_backend/ production-ready-system-settings-backend — 3製品共通のシステム設定 API と、共通認証基盤（ユーザー・ロール・セッション・ログイン。PLATFORM_* テーブル。pr_system_settings。#70 / #206 / #212）
templates/
  backend-service/  FastAPI サービス雛形（backend_core 利用）
docs/
  design-system/    デザインシステムの正本（ARCHITECTURE / README / components-reference / adherence lint）
  ux-contracts/     3 製品共通の画面の振る舞いの正本（ボタン・通知・ページの型・作業状態・横断契約。#118）
  backend-standard.md  共有 backend の標準
```

- フロント標準・使い方: 本ファイル以下 + [`packages/ui/README.md`](packages/ui/README.md)
- バックエンド標準・使い方: [`docs/backend-standard.md`](docs/backend-standard.md) + [`packages/backend_core/README.md`](packages/backend_core/README.md)
- 変更運用ルール（前後端共通）: [`CONTRIBUTING.md`](CONTRIBUTING.md)

各製品（`../rag/` `../nl2sql/` `../agent/`）は自分の `features/*`・ページ・API hooks・業務文言だけを持ち、共通基盤はこの `platform/` に集約する。

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
    styles/structure/        utility で表せない共有部品の構造 CSS（FixedSplitPane）
    lib/                     cn() / 経過時間の計算 / 分割比率の計算 / メニューのフォーカス復帰
    components/
      ui/                    Button / Card / Switch / Skeleton / Select / Banner /
                             Toast / MessageText / ConfirmDialog / ToggleChip / Tabs / FieldError / FormStatus /
                             ContentActionBar / BulkSelectionActions / ClearActionButton /
                             FloatingActionMenu / DisclosureChevron
      feedback/              LoadingState / ErrorState / EmptyState /
                             ActionResultRegion / ProcessingIndicator / TimedLoadingState
      data/                  StatusBadge(汎用 variant) / DataTable / Pagination /
                             RowActionMenu / ObjectActionBar（EntityAction）
      app-shell/             AppShell / Sidebar / PageHeader / PageBody / Section / Breadcrumbs / FixedSplitPane
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

### 1. 依存追加(monorepo 内の file: リンク)

```jsonc
// frontend/package.json
"dependencies": {
  "@engchina/production-ready-ui": "file:../../platform/packages/ui"
}
```


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

---

## 既存環境の更新手順（#499 既定のモデルの変数名）

#499 で「システム設定 › モデル」の既定のモデルを **既定の Vision モデル**（必須。画像を読む処理）と **既定のテキストモデル**
（任意。画像を扱わない処理。未設定なら既定の Vision モデル）の 2 つに分け、共通 `.env`（`platform/.env`）の変数名を変えた。
旧名は読まないため、`.env` に既定のモデルを書いている環境は次の手順で書き換える（画面だけで設定している環境は手順 2〜3 は不要）。

| 旧名 | 新名 |
|---|---|
| `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_MODEL` | `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_TEXT_MODEL` |
| `PLATFORM_OCI_ENTERPRISE_AI_LLM_MODEL` | `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_TEXT_MODEL`（`DEFAULT_MODEL` が無いか空のときだけ。旧版と同じ優先順） |
| `PLATFORM_OCI_ENTERPRISE_AI_VLM_MODEL` | `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_VISION_MODEL` |

- 画面で保存した `model-settings.json` は書き換えなくてよい。旧 key の `default_model_id` は、backend が読み込むときに
  既定のテキストモデルへ移し、既定の Vision モデルは従来と同じ規則（既定モデルが Vision 対応ならそれ、そうでなければ一覧で最初の
  Vision 対応のモデル）で補う。画面の「登録モデル」で保存し直すと新しい key（`default_text_model_id` / `default_vision_model_id`）で書かれる。
- 登録モデルに Vision 対応のモデルが 1 つもない環境は、画面を開くと「既定の Vision モデル」にエラーが出る。登録モデルの 1 つ以上で
  「画像入力（Vision）に対応」をオンにして保存する（接続情報・Generative AI の節の保存は止めない）。

1. 3 製品の backend と worker を停止する（Compute では各製品の systemd の unit。RAG は backend と ingestion-worker）。
2. 書き換えの内容を確認する（書き換えない）。リポジトリ root で実行する。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_model_env_names.py
   ```

3. 問題がなければ `--apply` で書き換える（`platform/.env.bak-499` を作ってから書き換える）。新名が既にあれば新名の値を残し、
   旧名の行を消す（競合として表示する）。`PLATFORM_ENV_FILE` で別の場所を使っている場合は `--env-file <path>` を渡す。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_model_env_names.py --apply
   ```

4. 3 製品の backend と worker を起動する。RAG は、解析サービス（parser）に渡す実行用の env（`RAG_SERVICE_RUNTIME_ENV_FILE`）を
   backend が書き直すので、RAG の「サービス管理」画面から OCI の parser を再起動する（`systemctl restart` だけでは古い env のまま）。
