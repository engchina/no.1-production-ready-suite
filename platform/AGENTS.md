# AGENTS.md — Production Ready Platform

> 共通基盤（`platform/`）固有のルール。共通のルールは root の [../AGENTS.md](../AGENTS.md) を先に適用する。
> 共有パッケージの運用（版管理・変更時の確認範囲）は [CONTRIBUTING.md](./CONTRIBUTING.md)。Issue の label と PR title の scope は `platform`。

## デザインシステム / UI

- **`packages/ui` と各製品の `frontend/` の UI は `docs/design-system/` が正本。** 依存の向き・製品が持てるもの・禁止事項・画面の構成・UI 変更の検証は root の [docs/agents/ui.md](../docs/agents/ui.md) に従う。
- 共通の権限管理（`packages/system-settings` の `RolePermissionsPage`）は、渡された権限の一覧の順にグループを並べる。一覧は製品が `permissionNavSections` / `arrangePermissionsByNav` で左のナビにそろえてから渡す（root の [docs/agents/shared-features.md](../docs/agents/shared-features.md)）。
- 1 製品しか使わないもの（`WorkflowProgressStrip`、オントロジーグラフ等）はその製品に置いてよい。

### `packages/ui` の実装の規則

- **回すのは `Spinner` だけ。** `packages/ui` の部品の中でも、`Button` の `loading`・`ProcessingIndicator` / `TimedLoadingState`・`ChatProgress`・`LoadMoreFooter` などは `Spinner` を通す。`Spinner` の形（全周のトラック + 180 度対称の 2 本のアーク）と箱（回転しない固定の正方形の `span.pr-spinner` の中で svg だけが回る）を崩さない（理由は README §4「`Spinner`」。#1180）。
- アイコンは `lucide-react` のコンポーネントを `LucideIcon` として受け取る（`icon={Upload}`。Lucide 名の文字列では受け取らない）。
- `wide` の画面では、カード・危険な操作の区画・一覧のコンテナは 100% の幅を使う（max-width を付けない）。例外はフォームの欄の並び（`FormGrid` が `--form-max-width` 60rem で止める）と、一覧の検索欄（`ListToolbar` が `lg` 28rem で固定する）（README §4「wide 画面の 100% 充填」。#1891）。

### `packages/ui` の変更の検証

- ライト / ダーク、1280px / 1920px、キーボード操作（Tab 順・フォーカスリング・`Tabs` の ← → / Home / End）を確かめる。
- 色・コントラストに関わる変更は、`docs/design-system/reference/*.html` をブラウザで開いて実測値と突き合わせる。
- 状態を表す UI はアイコンでも符号化し、グレースケールでも判別できるか確かめる。
- `docs/design-system/README.md` §9 の検収基準を PR の `検証結果` に転記する。

### lint

遵守ルールの正本は `docs/design-system/adherence.oxlintrc.json`。規則を足す・変える・製品から参照するときは [docs/agents/lint.md](./docs/agents/lint.md) を読む。**各製品はルールもプラグインもコピーせず、相対パスで参照する。**

## CI / 検証コマンド

CI の job は `Platform / UI`・`Platform / backend_core`・`Platform / system_settings_backend`・`Platform / evaluation_scoring`・`Platform / enterprise_ai` と、影響を受ける全製品の job（root の [docs/agents/ci.md](../docs/agents/ci.md)）。ローカルでは変更した package の command を実行し、製品の全件の検査は CI の job の結果を引用してよい。

```bash
# frontend（共有 UI）— platform/ で実行
npm ci && npm run typecheck && npm run lint && npm test && npm run build

# backend（production-ready-backend-core）
cd packages/backend_core
uv sync --locked --dev
uv run ruff format --check . && uv run ruff check . && uv run mypy src
uv run pytest --cov=pr_backend_core && uv run bandit -r src && uv run pip-audit
```

- 1 台の Compute の配備（`deploy/`）のテストは `for t in deploy/tests/*.test.sh; do bash "$t"; done`（nginx と openssl が要る。CI は `Suite / Deploy`）。
