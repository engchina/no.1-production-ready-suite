# 外部の MCP クライアントから RAG / NL2SQL を使う（API キー）

RAG（非構造化データ）と NL2SQL（構造化データ）は、標準の MCP（Streamable HTTP の `POST /api/mcp`。`initialize` / `tools/list` / `tools/call`）を公開している。Claude Code・Codex・Cursor などの外部の MCP クライアントは、利用者が自分で発行した **API キー** で接続する（#1805）。Agent は外部に MCP を公開しない。

## 接続先

| 製品 | URL（1 台の Compute に配備した Suite） | 開発の dev server |
|---|---|---|
| RAG | `https://<host>[:<https_port>]/rag/api/mcp` | `http://localhost:8000/api/mcp` |
| NL2SQL | `https://<host>[:<https_port>]/nl2sql/api/mcp` | `http://localhost:8010/api/mcp` |

- 配備では Nginx の `/<製品>/api/` の location が backend へ転送する（`platform/deploy/suite-nginx.sh`。`Authorization` ヘッダーはそのまま渡る）。RAG の `mcp` は LLM を待つため長い待ち時間の location に入っている。
- HTTPS は既定で Suite の自作の CA で署名している。クライアントの OS / ランタイムに CA 証明書（`https://<host>/platform/ca.crt`）を信頼させるか、正規の証明書に替える（`terraform/README.md`）。
- 画面の「API キー」に、開いている環境の接続先の URL が出る（Suite の配備では RAG と NL2SQL の両方）。

## API キー

- **各利用者が自分で発行する**: 画面の左下のアカウントのメニュー →「API キー」。名前（用途。例「会社の PC の Claude Code」）・使える製品（RAG / NL2SQL。既定は両方）・有効期限（30 / 90 / 365 日・無期限）を選ぶ。メニュー権限は要らない。1 人 10 件まで。
- 秘密（`prak_<id>_<秘密>`）は **発行の直後に 1 回だけ** 表示する。DB には SHA-256 だけを保存する（表 `PLATFORM_API_KEYS`）。
- キーは **発行した利用者として動く**。呼び先は認証のたびに利用者の現在のロールから権限とデータの範囲（検索・回答プロファイル・ナレッジベース・業務プロファイル・Deep Data Security）を組み立てる。ツールの一覧（`tools/list`）も利用者の権限で決まる。
- 利用者が無効化・削除されたら、そのキーは使えない（削除ではキーも消える）。初回のパスワード変更が済んでいない利用者のキーも使えない。
- 失効: 自分のキーはアカウントのメニューの「API キー」から、全員のキーは「ユーザーとロール」→「API キー」（メニュー権限 `menu.security_api_keys`）から。失効はすぐに効く。
- キーは **MCP の入口（`POST /api/mcp`）だけ** で使える。画面用の API（Cookie / CSRF）に付けると 401（`SECURITY_API_KEY_NOT_ALLOWED`）。

## 応答

| 状況 | status | `error_code` |
|---|---|---|
| キーなし | 401 | `SECURITY_AUTHENTICATION_REQUIRED` 等（サービストークンとして検証） |
| 誤り・失効済み・期限切れ | 401 | `SECURITY_API_KEY_INVALID` |
| 使える製品にこの製品が無い | 403 | `SECURITY_API_KEY_PRODUCT_FORBIDDEN` |
| 利用者が無効・初回パスワード変更待ち | 403 | `SECURITY_PERMISSION_DENIED` |
| MCP 以外の API にキーを付けた | 401 | `SECURITY_API_KEY_NOT_ALLOWED` |
| ツールの権限が無い | 200（`isError: true`） | `structuredContent.error_code` = `MCP_TOOL_FORBIDDEN` |

## 例

### Claude Code

```bash
export PR_API_KEY='prak_...'   # 発行したキー（repo や共有の設定に書かない）
claude mcp add --scope user --transport http rag https://<host>/rag/api/mcp \
  --header 'Authorization: Bearer ${PR_API_KEY}'
claude mcp add --scope user --transport http nl2sql https://<host>/nl2sql/api/mcp \
  --header 'Authorization: Bearer ${PR_API_KEY}'
claude mcp list                 # rag・nl2sql が ✔ Connected
MCP_TOOL_TIMEOUT=660000 claude  # rag_search は 20 秒〜2 分かかる（上限 600 秒。既定の 60 秒では切れる）
```

- MCP サーバーの名前は `rag` / `nl2sql` にする（組み込みのスキルがこの名前でツールを使う）。
- 長くかかるツール（`rag_search`。サーバーの上限 600 秒）の待ち時間は 660 秒を推奨する。client が `_meta.progressToken` と `Accept: text/event-stream` を付けると、10 秒ごとに進み具合（`notifications/progress`）を送る（#1864。詳細は [skills/README.md](../../skills/README.md)「長くかかるツール」）。
- `--header` は単引用符で渡す。設定（`~/.claude.json`）にはキーの値ではなく `${PR_API_KEY}` が保存され、Claude Code が起動のたびに環境変数から展開する。
- スキルの入れ方・`claude -p` の許可（`--allowedTools "Skill,mcp__rag,mcp__nl2sql"`）・設定の JSON・つまずく所は [skills/README.md](../../skills/README.md)。

### Codex

```bash
codex mcp add rag --url https://<host>/rag/api/mcp --bearer-token-env-var PR_API_KEY
codex mcp add nl2sql --url https://<host>/nl2sql/api/mcp --bearer-token-env-var PR_API_KEY
```

### curl

```bash
URL=https://<host>/rag/api/mcp
H=(-H "Authorization: Bearer ${PR_API_KEY}" -H 'Content-Type: application/json')
curl -sS "${H[@]}" "$URL" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}'
curl -sS "${H[@]}" "$URL" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
curl -sS "${H[@]}" "$URL" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"rag_list_search_answer_profiles","arguments":{"limit":10}}}'
```

NL2SQL の読み取りのツールの例は `nl2sql_list_profiles`。

## local の認証モード

`RAG_AUTH_MODE=local` などログインを省略する開発の構成でも、`Authorization: Bearer prak_…` を付けた要求はキーを検証する（素通りにしない）。local の画面（ローカル利用者）で発行したキーはローカル利用者として動き、local でだけ使える（production では 401）。キーを付けない要求は、従来どおり全権限のローカル利用者になる。そのため local では、MCP クライアントの接続の確認（`claude mcp list` の Connected）だけではキーを確かめられない。キーが使われたかは「API キー」の最終利用日時で確かめる。

## 実装（開発者向け）

- 共通: `pr_system_settings.auth.api_keys`（形式・保存・入出力）、`AuthService.create_api_key` / `list_api_keys` / `revoke_api_key` / `authenticate_api_key`、`authorize_request(..., api_key_paths={"/mcp"})`、`build_auth_router(..., api_keys=True)`（`/account/api-keys*` と `/security/api-keys*`）。DDL は共通認証の `PLATFORM_AUTH_DDL`。
- 画面: `@production-ready/system-settings` の `MyApiKeysPage`（`API_KEY_PATHS.mine`）・`ApiKeysAdminPage`（`API_KEY_ADMIN_NAV_ITEM`）・`mcpEndpoints`。アカウントのメニューの項目は `SidebarAccountSection` の `routes.apiKeys`。
- 製品の manifest: `/account/api-keys*` はログインだけ（`OPEN_API_OPERATIONS`）、`/security/api-keys*` は `menu.security_api_keys`。
- Agent → RAG / NL2SQL の連携は今までどおりサービストークン（`backend-standard.md`「製品間の連携」）。
- 残課題: 連携用の利用者（ログインできない利用者）として動くキー・プロファイルでキーを絞る機能・OAuth 2.1（MCP の authorization 仕様）。
