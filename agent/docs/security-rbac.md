# ログインと権限（#215）

Agent Control Plane の画面は、RAG / NL2SQL と同じ共通認証（platform の `pr_system_settings.auth`）でログインします。
ユーザー・ロール・セッションは 3 製品で共有する `PLATFORM_*` テーブル、ロールに付ける Agent の権限と対象範囲は
`AGENT_ROLE_*` テーブルに保存します。

## 1. 認証モード（`AGENT_AUTH_MODE`）

| 値 | 画面 | API（Cookie のないリクエスト） | 用途 |
|---|---|---|---|
| `local`（既定） | 全権限のローカル利用者（ログイン不要。DB セッションを作らない） | 従来どおり `AGENT_RBAC_ENABLED` の header / JWT / 外部 policy に従う（無効なら全開放） | 開発・CI |
| `production` | 共通認証のログインが必要（Cookie のセッション） | `AGENT_RBAC_ENABLED=true` かつ信頼できる identity（HMAC 署名 header・JWT bearer・外部 policy URL のどれか）があるときだけ、その identity で判定。それ以外は **401**（`X-Agent-Roles` / `X-Agent-Actor` の自己申告は信じない） | 本番（Resource Manager の stack は常にこちら） |

- 構成管理者（ログインユーザー ID は `system_admin` 固定）のパスワードは共通 `.env` の `PLATFORM_ADMIN_LOGIN_USER_PASSWORD`。
  3 製品で同じ値です。最初は構成管理者でログインし、ユーザー管理・ロール管理・権限管理で利用者を作ります。
- 認証ポリシー（セッションの有効期限・ロック・パスワード長・Argon2 のコスト・`PLATFORM_AUTH_COOKIE_SECURE`）は共通 `.env` の `PLATFORM_AUTH_*`。
- Cookie 名は製品ごと（`AGENT_APP_AUTH_SESSION_COOKIE_NAME=agent_session` / `AGENT_APP_AUTH_CSRF_COOKIE_NAME=agent_csrf`）。
  同じホストで 3 製品を動かしてもセッションは混ざりません。
- テーブルは `cd agent/backend && uv run python -m app.cli.agent_security_migrate` で作ります（`PLATFORM_ORACLE_*` で接続し、
  `PLATFORM_*` → `AGENT_ROLE_*` → 組み込み SYSTEM_ADMIN ロールの順に冪等に適用。ユーザーは作りません）。
  未適用のままログインすると `409 SECURITY_SCHEMA_MIGRATION_REQUIRED` になります。

## 2. 判定の順序

`/api` の全 route は `app.security.dependencies.authorize_api_request` を通ります。

1. local: ローカル利用者として通す（router の RBAC は従来の header / JWT 判定のまま。ローカル利用者は使わない）。
2. production の公開 path: `/health`・`/ready`・`/auth/login`・`POST /mcp/{binding_id}`。
   `POST /mcp/{binding_id}` は Runtime からの呼出し境界で、従来どおり Binding 固有 token（Bearer）で認証します（Cookie・manifest の対象外）。
3. production で session Cookie あり: セッションを検証し、更新系（GET / HEAD / OPTIONS 以外）は `X-CSRF-Token` header と CSRF Cookie を照合し、
   強制パスワード変更中は `/auth/me`・`/auth/logout`・`/auth/password/change` 以外を 403 にし、権限 manifest の権限を確認します
   （manifest に登録のない API は 403）。Cookie が不正・期限切れなら 401 で、header の RBAC には切り替えません。
4. production で session Cookie なし: `AGENT_RBAC_ENABLED=true` **かつ**信頼できる identity
   （`AGENT_RBAC_IDENTITY_HMAC_SECRET` の HMAC 署名 header・`AGENT_RBAC_JWT_BEARER_ENABLED` の JWT bearer・`AGENT_RBAC_POLICY_URL` の外部 policy のどれか）が
   設定されているときだけ、その identity からロールを求め、ロールから作った権限で manifest を確認します（外部連携用）。
   ロールがなければ 401、権限が足りなければ 403。
   信頼できる identity がなければ、`AGENT_RBAC_ENABLED=true` でも `X-Agent-Roles` / `X-Agent-Actor`（と `AGENT_RBAC_ACTOR_POLICIES_JSON` の
   actor header 引き）は自己申告のため使わず 401 です。この組み合わせは起動時に警告ログを出します。
   `AGENT_RBAC_ENABLED=false` も 401（全開放にしない）。ログインだけで使える API とユーザー管理は Cookie のセッションが必要です。

その後、router の既存の判定（`require_viewer` などのロール、エージェント・業務ビューの対象範囲）が同じ利用者で動きます。
Cookie のセッションの利用者からは `ActorPolicy(roles, business_view_ids, agent_ids)` を作り、header の RBAC 情報は使いません。

## 3. 権限カタログ

### capability（従来の 5 ロールに対応）

| capability | 従来のロール | 内容 | 暗黙に含むメニュー |
|---|---|---|---|
| `agent.runs.view` | viewer | 利用できる範囲の Run・イベント・成果物の閲覧 | Run |
| `agent.runs.operate` | operator | Run の作成・取消・再開・再実行（閲覧を含む） | Run |
| `agent.approvals.decide` | approver | 承認・却下（閲覧を含む） | 承認・監査 |
| `agent.audit.view` | auditor | Run の監査・ツール呼出し履歴・trace event（閲覧を含む） | 監査 |
| `agent.admin` | admin | 業務 Agent・スキル・Runtime・Binding・プラグイン・運用設定の変更とすべての操作（システム設定のメニューも暗黙に含む）。対象範囲の制限なし | ユーザーとロール・権限管理以外のすべてのメニュー |

### メニュー権限（`agent/frontend` のナビと同じ並び）

| グループ | コード |
|---|---|
| Control Plane | `menu.agents` / `menu.skills` / `menu.runtimes` / `menu.runs` / `menu.approvals` / `menu.audit` / `menu.plugin_marketplaces` |
| 運用設定 | `menu.settings_connection` / `menu.settings_external_rag` / `menu.settings_external_nl2sql` / `menu.settings_external_mcp` / `menu.settings_runtime_snapshot` |
| システム設定（3 製品共通） | `menu.settings_oci` / `menu.settings_upload_storage` / `menu.settings_model` / `menu.settings_database` / `menu.settings_appearance` |
| ユーザーとロール（3 製品共通） | `menu.security_users` / `menu.security_roles` |
| Agent セキュリティ | `menu.security_permissions` |

画面の表示にはメニュー権限、実データの閲覧・操作には capability が必要です。たとえば Run 画面は `menu.runs` で開けますが、
Run の一覧・詳細は `agent.runs.view`（または operate / decide / audit / admin）がないと 403 です。capability は関連メニューを
暗黙に含むため、capability だけを付けたロールでも画面を開けます。

ダッシュボード（`menu.dashboard`・グループ「概要」）は廃止しました（#262）。`/` は画面を持たず、ナビの並び順で最初に開ける画面へ移します
（未知の URL・ログイン後は Run を開ければ Run）。既存ロールに残る `menu.dashboard` は `agent_security_migrate` が削除します（§8）。
削除前でも、カタログにないコードは実効権限・権限管理の表示から除かれ、権限管理で保存すると消えます。

### manifest（抜粋。正本は `backend/app/security/permissions.py`）

| API | 必要な権限（いずれか） | router の追加の判定 |
|---|---|---|
| `GET /runs` | `menu.runs` / `menu.approvals` | viewer 以上・対象範囲で絞る |
| `GET /runs/{id}`・`/audit`・`/artifacts*` | `menu.runs` / `menu.approvals` / `menu.audit` | viewer 以上（監査は auditor）・範囲外は 403 |
| `GET /runs/{id}/events`（SSE） | `menu.runs` / `menu.approvals` | viewer 以上・範囲外は 403 |
| `WS /runs/{id}/events/ws` | `menu.runs` / `menu.approvals` | viewer 以上・範囲外は close 1008 |
| `POST /runs`・`/runs/{id}/cancel`・`resume`・`replay` | `agent.runs.operate` / `agent.admin` | operator・範囲外は 403 |
| `POST /approvals/{id}/decision` | `agent.approvals.decide` / `agent.admin` | approver・範囲外は 403・決定者は利用者 |
| `GET /audit/tool-calls(.csv)` | `menu.audit` | auditor・範囲で絞る |
| `GET /agents` | `menu.agents` / `menu.runs` / `menu.settings_runtime_snapshot` | 利用できるエージェントだけ |
| `GET /runtime-bindings` | `menu.agents` / `menu.runs` / `menu.runtimes` / `menu.settings_runtime_snapshot` | viewer 以上・利用できるエージェントの Binding だけ |
| 業務 Agent・スキル・Runtime・Binding・プラグイン・Agent 固有の設定（外部 RAG / NL2SQL / MCP・snapshot）の変更 | `agent.admin` | admin |
| システム設定（OCI 認証・アップロード保存先・モデル・データベース）の保存・接続テスト・ADB 操作 | 各メニュー（`menu.settings_oci` / `menu.settings_upload_storage` / `menu.settings_model` / `menu.settings_database`。RAG / NL2SQL と同じ割り当て） | Cookie のセッションは同じメニュー権限、header / JWT の経路は admin（`require_system_settings_write`） |
| 設定の GET（外部 RAG / NL2SQL / MCP、システム設定） | 各メニュー | — |
| `GET /tools` | `menu.audit` / `agent.admin` | — |
| `GET /observability/status`・`GET /settings/trace-policy` | `menu.audit`（画面からは使わない。運用スクリプトの確認用） | — |
| ナビに出さない設定（ツール権限・Command Policy・Runtime Safety・Planner） | `agent.admin` | — |
| legacy Memory の検索 | `agent.audit.view` / `agent.admin` | — |
| `/security/users*` | `menu.security_users` | 共通の昇格防止 |
| `/security/roles*`（GET） | `menu.security_users` / `menu.security_roles` / `menu.security_permissions` | — |
| `/security/roles*`（変更） | `menu.security_roles` | — |
| `GET /security/permissions`・`/security/access-targets`、`PUT /security/roles/{id}/access` | `menu.security_permissions` | 昇格防止 |

## 4. 対象範囲（エージェント・業務ビュー）

- ロールにエージェント ID と業務ビュー ID を割り当てます。利用者の範囲は、有効なロールの割り当ての和集合です（割り当てがなければ何も見えない）。
- SYSTEM_ADMIN・構成管理者・`agent.admin` を持つ利用者は制限なし。`agent.admin` を含むロールの対象リストは保存時に空へ正規化します。
- 業務ビューは Agent にマスタがありません（Run の `metadata.business_view_id` などの文字列）。権限管理で選べる業務ビューは、
  Run に現れた ID・ロールに割り当て済みの ID・RAG の業務ビューの和集合です。形式は `^[A-Za-z0-9._:-]{1,64}$`（合わない RAG の ID は候補にしない）。
  業務ビューを持たない Run は、エージェントの範囲だけで判定します（従来どおり）。
- RAG の業務ビュー（#233）: 外部 RAG の MCP（`AGENT_EXTERNAL_RAG_MCP_URL`）が設定されていれば、`GET /security/access-targets` は
  RAG の `rag_list_business_views` を**画面を開いた管理者の `user_uuid`** のサービストークンで呼び、その管理者が RAG で使える ACTIVE な
  業務ビュー（最大 200 件）を名前付きで候補に足します。未設定・失敗のときは従来どおりの候補に、`warning_messages` で理由を返します
  （権限管理はそのまま使えます）。
- 対象範囲が制限された利用者の `GET /observability/events` は、範囲内の Run の event だけを返します。

## 5. 権限昇格の防止

- `PUT /security/roles/{id}/access`: SYSTEM_ADMIN 以外は、自分が持たない権限・自分の範囲外のエージェント / 業務ビューを
  ロールに足すと 403。存在しないエージェント ID・未登録の権限コード・形式が不正な業務ビュー ID は 400。組み込み / アーカイブ済みのロールは 409。
- ユーザーへのロールの割り当て・ロールの復元は、ロールの権限と対象範囲が操作者に収まる場合だけ（共通の判定と、他製品の権限テーブルの判定）。

## 6. WebSocket と承認の決定者

- `WS /runs/{id}/events/ws` は Request 前提の認可を通らないため、handler の中で accept 前に認証します。
  production で session Cookie があるときは **`Origin` が `Host` と一致すること**を必須にし（CSRF header を送れないため）、
  セッション・強制パスワード変更・manifest を確認します。失敗は accept せずに close 1008。
  Cookie がなければ `AGENT_RBAC_ENABLED=true` かつ信頼できる identity があるときだけ、その identity で従来の判定、それ以外は close 1008。
  接続後のロール・範囲の不足は従来どおり `rbac.*` の error を送って close 1008。コマンド（cancel / resume / approval_decision）も同じ利用者で判定します。
- Nginx は `/api/` に `proxy_set_header Host $http_host;`（port を含む）を渡します。前段に HTTPS の終端を置く場合も、ブラウザの Origin と
  backend に届く Host を一致させてください。
- 承認の決定者（`decided_by`）は、Cookie のセッションでは利用者の `login_user_id` です（HTTP の body・WebSocket の message の値は使わない）。

## 7. 外部連携の RBAC との関係

`AGENT_RBAC_*`（header・HMAC 署名 header・JWT・外部 policy・`AGENT_RBAC_ACTOR_POLICIES_JSON`）は、画面以外のクライアント
（サービス間連携・運用スクリプト）向けに残します。production で使うには `AGENT_RBAC_ENABLED=true` にし、
信頼できる identity（JWT・署名 header・外部 policy）を**必ず**設定してください（ないと 401 で、起動時に警告ログを出します）。
local では従来どおり、信頼できる identity がなくても `X-Agent-Roles` などの header で判定します（開発・CI 用）。ロールから作る権限は capability（implies を含む）と
Control Plane の読み取りメニュー（業務 Agent・スキル・Runtime・Run・承認・監査・マーケットプレイス）で、
ユーザーとロール・権限管理は含みません。`scripts/agent_runtime_gateway_jwks_check.py --backend-status` などの JWT の確認も同じ条件です。

## 8. 既存環境の更新手順（#215）

1. 共通 `.env`（`platform/.env`）に `PLATFORM_ADMIN_LOGIN_USER_ID=system_admin` と `PLATFORM_ADMIN_LOGIN_USER_PASSWORD`
   （12〜30 文字、大文字・小文字・数字を含み、`admin` と `"` を含まない）があることを確認する（RAG / NL2SQL と共通）。
   HTTPS で配信している場合は `PLATFORM_AUTH_COOKIE_SECURE=true`。
2. `agent/backend/.env` に `AGENT_AUTH_MODE=production` を書く。
3. テーブルを作る: `cd agent/backend && uv sync --locked --no-dev && uv run python -m app.cli.agent_security_migrate`。
4. Nginx の Basic 認証を廃止する（Resource Manager の stack で配備した instance）。`/etc/nginx/sites-available/production-ready-agent` から
   `auth_basic` / `auth_basic_user_file` を削除し、`location /api/` の `proxy_set_header Host` を `$http_host` にして
   `sudo nginx -t && sudo systemctl reload nginx`。`/etc/nginx/production-ready-agent.htpasswd` と
   `/u01/aipoc/props/basic_auth_*` は削除してよい（`init_script.sh` を再実行しても同じ設定になる）。
5. `sudo systemctl restart production-ready-agent-backend`（compose は `docker compose up -d control-plane`）。
6. `system_admin` でログインし、ロール管理でロールを作り、権限管理で Agent の権限・エージェント・業務ビューを割り当て、ユーザーに付ける。
7. （#262 以降に更新する環境）手順 3 の `agent_security_migrate` を再実行し、既存ロールに残る廃止した権限コード
   `menu.dashboard` を削除する（何度実行してもよい。出力の `retired_permission_rows` が削除した行数）。削除しないと、RAG / NL2SQL の
   ユーザー管理から非 SYSTEM_ADMIN の管理者がそのロールを割り当てるとき、他製品の権限の判定（生のコードで比べる）で 403 になることがあります。
8. 外部連携で header / JWT を使っているクライアントがあれば、`AGENT_RBAC_ENABLED=true` と信頼できる identity（JWT・HMAC 署名 header・外部 policy）で
   使えます（Cookie を送らないこと）。`X-Agent-Roles` / `X-Agent-Actor` だけで認可していたクライアントは、production では 401 になるため、
   JWT か署名 header に移すか、画面のログインを使ってください。

## 9. 既知の制約

- 外部 policy URL（`AGENT_RBAC_POLICY_URL`）だけを設定した場合、actor 名は署名のない `X-Agent-Actor` から取ります。policy service 側で
  呼出し元を確かめられないなら、HMAC 署名 header か JWT と組み合わせてください。
- WebSocket のセッションは接続時だけ確認します（接続中に失効・権限変更しても切断しません）。
- 範囲が制限された利用者の `GET /observability/events` は、件数上限（limit）を適用した後に範囲で絞ります。
- 業務ビューのマスタは Agent にないため、RAG から読めなかった業務ビューの名前は ID と同じです。RAG の業務ビューは権限管理を開いた
  管理者が RAG で使えるものだけで、201 件目以降は候補に出ません（ID を入力すれば保存できます）。
- `POST /mcp/{binding_id}`（Binding 経由の MCP）は Run と結びつかないため、RAG / NL2SQL のツールは Run の利用者ではなく
  サービス利用者（`AGENT_MCP_SERVICE_USER_LOGIN_ID`）として呼びます。未設定ならそのツールは失敗します
  （[agent-control-plane-design.md §4.1](./agent-control-plane-design.md#41-rag--nl2sql-の-mcp233)）。
- local mode のユーザー・ロールは InMemory の store で、再起動で消えます（local ではログインしないため通常は使いません）。
