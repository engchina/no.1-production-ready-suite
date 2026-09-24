# 共有プラットフォームの運用ルール（governance）

`no.1-production-ready-platform` は **RAG / NL2SQL / Agent の前後端 single source of truth**。
共通コードはここに集約し、各業務 repo は `features/*`・ページ・業務文言だけを持つ。

- フロント共有: `@engchina/production-ready-ui`（`packages/ui`）
- バックエンド共有: `production-ready-backend-core` / `pr_backend_core`（`packages/backend_core`）

原則は前後端で共通: **共通機能の変更は必ずこの platform で行い、各業務 repo にコピーしない。**
各業務 repo の CI は共通パッケージを解決して typecheck/build/test を回す。

---

# 共有 UI の運用ルール

`@engchina/production-ready-ui` は **RAG / NL2SQL / Agent の UI/UX の single source of truth**。
3 プロジェクトの見た目・操作・アクセシビリティを揃えるための運用ルールを定める。

## 1. 変更はここで、タグで配る

- **共通 UI（トークン・基本コンポーネント・レイアウト・状態/通知）の変更は必ずこのパッケージで行う。**
  各アプリ repo の `components/ui/*` を私的にコピー・改変しない（コピーは禁止。漂移の元）。
- 変更は **semver** で管理し、リリースは **`vX.Y.Z` タグ**を push する（`.github/workflows/release.yml` が GitHub Packages へ publish）。
  - `patch`: バグ修正・内部実装。`minor`: 後方互換の追加。`major`: 破壊的変更（props 改名・削除等）。
- アプリ固有のもの（nav 構成・ページ・API hooks・業務文言）は各アプリ repo に置く。

## 2. CI ゲート

- **このパッケージの CI**（`.github/workflows/ci.yml`）: `typecheck` + `build` + `dist` 成果物検証。
- **各アプリの CI**: 共有パッケージを解決したうえで `typecheck` + `build`（+ アプリにテストがあれば `test` / `e2e`）。
  - **現在（`file:` リンク期）**: アプリ CI は **UI repo を sibling に checkout して build** してから `npm ci` する
    （`docs/consume-in-ci.md` 参照。各アプリの `.github/workflows/ci.yml` に実装済み）。
  - **切替後（GitHub Packages 期）**: アプリ CI は registry 認証付きの `npm ci` のみ。sibling checkout は不要になる。

## 3. リンク方式：`file:` → GitHub Packages 切替手順（安定後）

開発中は同一マシンの sibling を指す `file:` で高速反復する。安定したら以下で registry 配布へ切替える。

1. このパッケージで `vX.Y.Z` タグを push → `release.yml` が GitHub Packages へ publish。
2. 各アプリ `frontend/package.json` を変更:
   ```diff
   - "@engchina/production-ready-ui": "file:../../no.1-production-ready-platform/packages/ui",
   + "@engchina/production-ready-ui": "^X.Y.Z",
   ```
3. 各アプリ `frontend/` に `.npmrc`（`.npmrc.example` 参照）を置き、`@engchina` scope を GitHub Packages に向ける。
4. `npm install` で lock を更新 → コミット。CI から sibling checkout ステップを削除し、`NODE_AUTH_TOKEN` を渡す。

> どちらの期でも Vite の `resolve.dedupe: ["react","react-dom"]` と
> `@source ".../@engchina/production-ready-ui/dist"`（globals.css）は必須。詳細は README 参照。

---

# 共有 backend（`production-ready-backend-core`）の運用ルール

`packages/backend_core`（import 名 `pr_backend_core`）は 3 サービスの **FastAPI インフラの正本**。
標準は [`docs/backend-standard.md`](docs/backend-standard.md)。

## 1. 変更はここで

- **共通 backend 機能（app factory / logging / metrics / request-id / エラー envelope /
  health-ready / settings 基底 / pagination）の変更は必ず backend_core で行う。** 各業務 repo に
  コピーしない。業務ロジック（RAG ingestion / NL2SQL の NL→SQL / Agent の tool 実行）は repo 側。
- **oci / oracledb は backend_core に入れない**（依存を軽く保つ）。各サービスが自分で持つ。
- envelope は `ApiResponse`（`data` / `error_messages` / `warning_messages`）で固定。
  破壊的変更は major として扱い、3 フロントの API 契約と同時に更新する。

## 2. CI ゲート

- **backend_core の CI**（`.github/workflows/ci.yml` の `backend-core` job）: `black --check` +
  `ruff` + `mypy strict` + `pytest`(Oracle 不要) + `bandit` + `pip-audit`。
- **各サービス backend の CI**: 共通スタックの quality gate（同上）。
  - **現在（path source 期）**: platform を sibling に checkout してから `uv sync --locked`
    （各 repo の `backend` job に実装済み）。
  - **配布切替後**: Release/index から解決し sibling checkout を削除。

## 3. 依存方法

dev は uv の path source（`docs/backend-standard.md` 参照）。path source を変更したら **`uv lock` 再生成**。
新サービスは `templates/backend-service` から派生し、`service_name` / `features/<domain>` だけ実装する。
