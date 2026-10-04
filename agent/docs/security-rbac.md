# ログインと権限（#215 / #750）

Agent の画面は、RAG / NL2SQL と同じ共通認証（platform の `pr_system_settings.auth`）でログインします。
ユーザー・ロール・セッションは 3 製品で共有する `PLATFORM_*` テーブル、ロールに付ける Agent の権限と対象範囲（エージェント）は
`AGENT_ROLE_*` テーブルに保存します。local でも production でも同じ Oracle のテーブルを使います（#750）。
Agent 独自の header / JWT / 外部 policy の認可（旧 `AGENT_RBAC_*`）は #750 で削除しました。

## 1. 認証モード（`AGENT_AUTH_MODE`）

| 値 | 画面と API | ユーザー・ロール | 用途 |
|---|---|---|---|
| `local`（既定） | ログインを省略し、全権限のローカル利用者として使う（DB セッションを作らない） | Oracle の `PLATFORM_*` / `AGENT_ROLE_*`（RAG / NL2SQL と同じ） | 開発 |
| `production` | 共通認証のログインが必要（Cookie のセッション）。Cookie のないリクエストは **401**（`X-Agent-Roles` などの header は使わない） | 同上 | 本番（Resource Manager の stack は常にこちら） |

- local でもユーザー管理・ロール管理・権限管理は共通 DB を読み書きします。共通 `.env` の `PLATFORM_ORACLE_*` が必要です
  （RAG / NL2SQL と同じ。旧版は local だけ InMemory で、画面が 0 件になり再起動で消えていました）。
- RAG / NL2SQL への呼出しはサービストークンです（§7）。

- 構成管理者（ログインユーザー ID は `system_admin` 固定）のパスワードは共通 `.env` の `PLATFORM_ADMIN_LOGIN_USER_PASSWORD`。
  3 製品で同じ値です。最初は構成管理者でログインし、ユーザー管理・ロール管理・権限管理で利用者を作ります。
- 認証ポリシー（セッションの有効期限・ロック・ログインの試行の回数の制限・パスワード長・Argon2 のコスト・`PLATFORM_AUTH_COOKIE_SECURE`）は共通 `.env` の `PLATFORM_AUTH_*`。
  ログインの失敗が多すぎる送信元（ログイン ID と送信元 IP の組・送信元 IP ごと）には、構成管理者を含めて `429 SECURITY_RATE_LIMITED` を返します（#1087。`PLATFORM_AUTH_LOGIN_ATTEMPT_*`）。
- Cookie 名は製品ごと（`AGENT_APP_AUTH_SESSION_COOKIE_NAME=agent_session` / `AGENT_APP_AUTH_CSRF_COOKIE_NAME=agent_csrf`）。
  同じホストで 3 製品を動かしてもセッションは混ざりません。
- テーブルは 運用設定 > システムテーブル の「作成・更新」か、`cd agent/backend && uv run python -m app.cli.agent_system_schema --initialize` で作ります（`PLATFORM_ORACLE_*` で接続し、
  `PLATFORM_*` → `AGENT_ROLE_*` → 組み込み SYSTEM_ADMIN ロールの順に冪等に適用。ユーザーは作りません）。
  未適用のままログインすると `409 SECURITY_SCHEMA_MIGRATION_REQUIRED` になります。

## 2. 判定の順序

`/api` の全 route は `app.security.dependencies.authorize_api_request` を通ります。

1. local: ローカル利用者（全権限・対象範囲の制限なし）を `request.state.principal` に入れて通す。
2. production の公開 path: `/health`・`/ready`・`/ready/database`（画面の DB ゲート。#325）・`/auth/login`。
3. production で session Cookie あり: セッションを検証し、更新系（GET / HEAD / OPTIONS 以外）は `X-CSRF-Token` header と CSRF Cookie を照合し、
   強制パスワード変更中は `/auth/me`・`/auth/logout`・`/auth/password/change` 以外を 403 にし、権限 manifest の権限を確認します
   （manifest に登録のない API は 403）。Cookie が不正・期限切れなら 401。
4. production で session Cookie なし: 401（RAG / NL2SQL と同じ。header の自己申告は使わない）。

その後、router の判定（`require_viewer` などのロール、エージェントの対象範囲）が同じ利用者で動きます。
利用者（Cookie のセッションの利用者か、local のローカル利用者）から `ActorPolicy(roles, agent_ids)` を作り、利用者がいなければ何も許可しません。

## 3. 権限カタログ

### capability（従来の 5 ロールに対応）

グループは NL2SQL / RAG と同じ「参照権限 / 実行権限 / 管理権限」で、権限管理ではナビのメニュー権限の後ろに並びます（#791）。

| グループ | capability | 名前（権限管理） | 従来のロール | 内容 | 暗黙に含むメニュー |
|---|---|---|---|---|---|
| 参照権限 | `agent.runs.view` | 実行履歴の参照 | viewer | 利用できる範囲の Run・イベント・成果物の閲覧 | 実行履歴 |
| 参照権限 | `agent.audit.view` | 監査ログの参照 | auditor | Run の監査・ツール呼出し履歴・trace event（閲覧を含む） | 監査ログ |
| 実行権限 | `agent.runs.operate` | 業務 Agent の実行 | operator | チャットと、Run の作成・取消・再開・再実行（閲覧を含む） | 実行履歴・チャット |
| 実行権限 | `agent.approvals.decide` | 承認の判断 | approver | 承認・却下（閲覧を含む） | 承認 |
| 管理権限 | `agent.admin` | Agent 管理 | admin | 業務 Agent・スキル・プラグイン・運用設定の変更とすべての操作（システム設定のメニューも暗黙に含む）。対象範囲の制限なし | ユーザーとロール・権限管理以外のすべてのメニュー |

### メニュー権限（`agent/frontend` のナビと同じ並び）

グループ・名前・並びはサイドナビと同じです（#567。上に一般の利用者の画面、下に管理者の画面。#791）。

| グループ | コード |
|---|---|
| AI 活用 | `menu.chat`（チャット。`agent.runs.operate` が含む。#768）/ `menu.runs`（実行履歴）/ `menu.approvals`（承認） |
| Agent 構築 | `menu.agents`（業務 Agent）/ `menu.skills`（スキル）/ `menu.automations`（自動実行。#784）/ `menu.plugin_marketplaces`（マーケットプレイス） |
| 改善・運用 | `menu.evaluation`（品質評価。#776。評価の Run は始めた利用者の Run なので、業務 Agent の対象範囲も確かめる） / `menu.feedback`（フィードバック。#774。集計は Run の一覧と同じく利用できる業務 Agent の Run だけ。チャットの回答の評価は `agent.runs.operate` を持つ会話の本人が付け、管理者の評価（`PUT /api/runs/{id}/admin-review`）は `agent.admin` がだれの回答にも本人の評価とは別に付ける） / `menu.usage`（利用状況。#772。集計は Run の一覧と同じく利用できる業務 Agent の Run だけ）/ `menu.audit`（監査ログ） |
| セキュリティ設定 | `menu.security_permissions` |
| ユーザーとロール（3 製品共通） | `menu.security_users` / `menu.security_roles` |
| 運用設定 | `menu.settings_system_tables` / `menu.runtimes`（実行環境）/ `menu.settings_external_mcp`（MCP 接続）/ `menu.settings_api_keys`（API キー。#778。作成・削除は `agent.admin`）/ `menu.settings_runtime_snapshot`（バックアップと復元） |
| システム設定（3 製品共通） | `menu.settings_oci` / `menu.settings_upload_storage` / `menu.settings_model` / `menu.settings_database` / `menu.settings_appearance` |

権限コードは保存値なので、ナビの名前・グループを変えてもコードは変えません（#791 で「Control Plane」グループを分けたときも同じ）。

画面の表示にはメニュー権限、実データの閲覧・操作には capability が必要です。たとえば実行履歴の画面は `menu.runs` で開けますが、
Run の一覧・詳細は `agent.runs.view`（または operate / decide / audit / admin）がないと 403 です。capability は関連メニューを
暗黙に含むため、capability だけを付けたロールでも画面を開けます。

ダッシュボード（`menu.dashboard`・グループ「概要」）は廃止しました（#262）。`/` は画面を持たず、ナビの並び順で最初に開ける画面へ移します
（未知の URL・ログイン後はチャットを開ければチャット。#791）。既存ロールに残る `menu.dashboard` はシステムテーブルの作成・更新（migration）が削除します（§8）。
削除前でも、カタログにないコードは実効権限・権限管理の表示から除かれ、権限管理で保存すると消えます。

### manifest（抜粋。正本は `backend/app/security/permissions.py`）

API は `(method, route template)` ごとに登録し、登録のない API は拒否します（前方一致では割り当てない。
共通のユーザー管理・ロール管理の API も同じ。#503）。

| API | 必要な権限（いずれか） | router の追加の判定 |
|---|---|---|
| `GET /runs` | `menu.runs` / `menu.approvals` | viewer 以上・対象範囲で絞る |
| `POST /agents/{id}/publish`・`POST /agents/{id}/versions/{version}/restore`（#770） | `agent.admin` | 下書きで実行（`POST /runs` の `draft=true`）も `agent.admin` だけ |
| `GET /runs/{id}`・`/audit`・`/artifacts*` | `menu.runs` / `menu.approvals` / `menu.audit` | viewer 以上（監査は auditor）・範囲外は 403 |
| `GET /runs/{id}/events`（SSE） | `menu.runs` / `menu.approvals` | viewer 以上・範囲外は 403 |
| `WS /runs/{id}/events/ws` | `menu.runs` / `menu.approvals` | viewer 以上・範囲外は close 1008 |
| `POST /runs`・`/runs/{id}/cancel`・`resume`・`replay` | `agent.runs.operate` / `agent.admin` | operator・範囲外は 403 |
| `GET /threads`・`GET /threads/{thread_id}`（チャットの会話。#768） | `menu.chat` | 作った利用者の会話だけ（別の利用者の会話は 404）・範囲外の Agent の会話は出さない |
| `POST /approvals/{id}/decision` | `agent.approvals.decide` / `agent.admin` | approver・範囲外は 403・決定者は利用者 |
| `GET /audit/tool-calls(.csv)` | `menu.audit` | auditor・範囲で絞る |
| `GET /agents` | `menu.agents` / `menu.runs` / `menu.settings_runtime_snapshot` | 利用できるエージェントだけ |
| `GET /runtime/status` | `menu.runtimes` / `menu.agents` | 組み込み Runtime の状態（API key は出さない） |
| 業務 Agent・スキル・プラグイン・Agent 固有の設定（MCP 接続・snapshot）の変更 | `agent.admin` | admin |
| システム設定（OCI 認証・アップロード保存先・モデル・データベース）の保存・接続テスト・ADB 操作 | 各メニュー（`menu.settings_oci` / `menu.settings_upload_storage` / `menu.settings_model` / `menu.settings_database`。RAG / NL2SQL と同じ割り当て） | 同じメニュー権限（`require_system_settings_write`） |
| 設定の GET（MCP 接続（Skill の画面からも読む）・ツールの取得、システム設定） | 各メニュー（MCP 接続は `menu.settings_external_mcp`。`menu.settings_external_rag` / `menu.settings_external_nl2sql` は #757 で廃止し、migration 004 が既存ロールから消す） | — |
| `GET /tools` | `menu.audit` / `agent.admin` | — |
| `GET /observability/status`・`GET /settings/trace-policy` | `menu.audit`（画面からは使わない。運用スクリプトの確認用） | — |
| ナビに出さない設定（ツール権限） | `agent.admin` | — |
| ユーザー管理（`GET`・`POST /security/users`、`GET`・`PATCH`・`DELETE /security/users/{user_uuid}`、`POST /security/users/{user_uuid}/disable`・`enable`・`reset-password`・`unlock`） | `menu.security_users` | 共通の昇格防止 |
| ロールの参照（`GET /security/roles`・`GET /security/roles/{role_id}`） | `menu.security_users` / `menu.security_roles` / `menu.security_permissions` | — |
| ロールの変更（`POST /security/roles`、`PATCH`・`DELETE /security/roles/{role_id}`、`POST /security/roles/{role_id}/archive`・`restore`） | `menu.security_roles` | — |
| `GET /security/permissions`・`/security/access-targets/agents`、`PUT /security/roles/{id}/access` | `menu.security_permissions` | 昇格防止 |

## 4. 対象範囲（エージェント）

- ロールにエージェント ID を割り当てます。利用者の範囲は、有効なロールの割り当ての和集合です（割り当てがなければ何も見えない）。
- SYSTEM_ADMIN・構成管理者・`agent.admin` を持つ利用者は制限なし。`agent.admin` を含むロールの対象リストは保存時に空へ正規化します。
- エージェントを削除すると、全ロールの割り当て（`AGENT_ROLE_AGENTS`）からも外します。後始末に失敗して残った ID は、
  権限管理の保存で黙って外します（新しく足す未知の ID だけ 400。#750）。
- 検索・回答プロファイルは Agent の対象範囲にしません（#750）。RAG のツールは Run の利用者を `sub` にしたサービストークンで呼ぶため、
  検索・回答プロファイルの判定は RAG の権限管理（`RAG_ROLE_SEARCH_ANSWER_PROFILES`）がそのまま行います。旧版の `AGENT_ROLE_SEARCH_ANSWER_PROFILES` は使いません
  （テーブルの削除はシステムテーブルの Issue #751）。
- 対象範囲が制限された利用者の `GET /observability/events` は、範囲内の Run の event だけを返します。

## 5. 権限昇格の防止

- `PUT /security/roles/{id}/access`: SYSTEM_ADMIN 以外は、自分が持たない権限・自分の範囲外のエージェントを
  ロールに足すと 403。新しく足す存在しないエージェント ID・未登録の権限コードは 400。組み込み / アーカイブ済みのロールは 409。
- ユーザーへのロールの割り当て・ロールの復元は、ロールの権限と対象範囲が操作者に収まる場合だけ（共通の判定と、他製品の権限テーブルの判定）。

## 6. WebSocket と承認の決定者

- `WS /runs/{id}/events/ws` は Request 前提の認可を通らないため、handler の中で accept 前に認証します。
  production で session Cookie があるときは **`Origin` が `Host` と一致すること**を必須にし（CSRF header を送れないため）、
  セッション・強制パスワード変更・manifest を確認します。失敗は accept せずに close 1008。
  Cookie がなければ close 1008。local はローカル利用者として接続します。
  接続後のロール・範囲の不足は従来どおり `rbac.*` の error を送って close 1008。コマンド（cancel / resume / approval_decision）も同じ利用者で判定します。
- Nginx は `/api/` に `proxy_set_header Host $http_host;`（port を含む）を渡します。前段に HTTPS の終端を置く場合も、ブラウザの Origin と
  backend に届く Host を一致させてください。
- 承認の決定者（`decided_by`）は利用者の `login_user_id` です（HTTP の body・WebSocket の message の値は使わない。local は `local`）。

## 7. 画面以外からの呼出し

- 外部 Runtime からの呼出し（Binding の MCP）は #754 で削除しました（実行は組み込み Runtime）。
- Control Plane → RAG / NL2SQL: 呼び先の `POST /api/mcp` を、Run の利用者を `sub` にした短命のサービストークンで呼びます
  （[agent-control-plane-design.md §4.1](./agent-control-plane-design.md#41-rag--nl2sql-の-mcp233)）。
- 旧版の `AGENT_RBAC_*`（`X-Agent-Roles` などの header・HMAC 署名 header・JWT bearer・外部 policy URL・
  `AGENT_RBAC_ACTOR_POLICIES_JSON`）は #750 で削除しました。設定が `.env` に残っていても読みません。

## 8. 既存環境の更新手順（#215）

1. 共通 `.env`（`platform/.env`）に `PLATFORM_ADMIN_LOGIN_USER_ID=system_admin` と `PLATFORM_ADMIN_LOGIN_USER_PASSWORD`
   （12〜30 文字、大文字・小文字・数字を含み、`admin` と `"` を含まない）があることを確認する（RAG / NL2SQL と共通）。
   HTTPS で配信している場合は `PLATFORM_AUTH_COOKIE_SECURE=true`。
2. `agent/backend/.env` に `AGENT_AUTH_MODE=production` を書く。
3. テーブルを作る: `cd agent/backend && uv sync --locked --no-dev && uv run python -m app.cli.agent_system_schema --initialize`（画面の 運用設定 > システムテーブル でもよい）。
4. Nginx の Basic 認証を廃止する（Resource Manager の stack で配備した instance）。`/etc/nginx/sites-available/production-ready-agent` から
   `auth_basic` / `auth_basic_user_file` を削除し、`location /api/` の `proxy_set_header Host` を `$http_host` にして
   `sudo nginx -t && sudo systemctl reload nginx`。`/etc/nginx/production-ready-agent.htpasswd` と
   `/u01/aipoc/props/basic_auth_*` は削除してよい（`init_script.sh` を再実行しても同じ設定になる）。
5. `sudo systemctl restart production-ready-agent-backend`（開発は backend の `uv run` を起動し直す）。
6. `system_admin` でログインし、ロール管理でロールを作り、権限管理で Agent の権限・エージェントを割り当て、ユーザーに付ける。
7. （#262 以降に更新する環境）手順 3 のシステムテーブルの作成・更新を再実行し、既存ロールに残る廃止した権限コード
   `menu.dashboard` を削除する（何度実行してもよい。出力の `retired_permission_rows` が削除した行数）。削除しないと、RAG / NL2SQL の
   ユーザー管理から非 SYSTEM_ADMIN の管理者がそのロールを割り当てるとき、他製品の権限の判定（生のコードで比べる）で 403 になることがあります。
8. （#750 以降に更新する環境）`backend/.env` の `AGENT_RBAC_*` は読まれないので削除してよい。header / JWT で API を呼んでいた
   クライアントは 401 になるため、画面のログインに移す。local の開発環境も共通 `.env` の `PLATFORM_ORACLE_*` が必要。
   権限管理で検索・回答プロファイルを割り当てていたロールは、RAG の権限管理で検索・回答プロファイルを割り当てる。

## 9. 既知の制約

- WebSocket のセッションは接続時だけ確認します（接続中に失効・権限変更しても切断しません）。
- 範囲が制限された利用者の `GET /observability/events` は、件数上限（limit）を適用した後に範囲で絞ります。
- local の Run は Run の利用者が `00000000-0000-0000-0000-000000000000`（ローカル利用者）です。production の RAG / NL2SQL を呼ぶと、
  その利用者は呼び先に存在しないため拒否されます（local 同士で使う）。

## 自動実行（スケジュール・Webhook。#784）

- メニュー権限 `menu.automations`（Agent 構築 /「自動実行」）。作成・変更・削除・今すぐ実行・Webhook の秘密の発行は `agent.admin`。
- 自動実行は作った利用者として Run を作る（実行のたびに利用者の現在の権限と対象範囲を確かめ、実行できなければ「開始できず」）。前回の Run が終わっていなければ、その回は飛ばす。
- Webhook（`POST /api/hooks/{automation_id}`）は公開 path で、Cookie・CSRF を使わず自動実行の秘密（`Authorization: Bearer prwh_…`）で認証する。秘密は発行時に 1 回だけ返し、`AGENT_CONTROL_PLANE_ITEMS`（kind `automation`）には SHA-256 の hash だけを保存する。自動実行が無い・秘密が違う・Webhook でないは区別せず 401。

## 業務 Agent の MCP（`POST /api/mcp`。#778）

- Cookie・CSRF を使わず `Authorization: Bearer` で認証する。`prak_` で始まるものは Agent の API キー、それ以外は共通のサービストークン（audience `agent`、署名鍵は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`。RAG / NL2SQL と同じ）。
- API キーは「実行する利用者」として動く（既定は作った利用者。ほかの利用者＝連携用の専用の利用者を選べるのはシステム管理者だけで、その利用者は有効で初回のパスワード変更が済んでいること。権限は利用者の現在のロールから毎回計算し直す）。キーに付けた業務 Agent に絞る。秘密は作成時の応答で 1 回だけ返し、`AGENT_CONTROL_PLANE_ITEMS`（kind `api_key`）には SHA-256 の hash だけを保存する。local で作ったキーは production では使えない。
- ツールの権限: `agent_list_agents` / `agent_get_run` は `agent.runs.view`（または operate / admin）、`agent_ask` は `agent.runs.operate`（または admin）。`agent_get_run` は呼び出し元が作った Run だけを読める。`agent_ask` の `thread_id`（#798）で続けられるのは、呼び出し元が作った同じ業務 Agent の会話だけ（それ以外は存在を漏らさず `THREAD_NOT_FOUND`）。
