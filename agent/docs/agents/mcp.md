# Agent の MCP 境界

> agent/AGENTS.md から移した詳細（#2276）。agent/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## MCP 境界

- **Agent は外部の MCP クライアントに業務 Agent へのチャットを公開する**（`POST /api/mcp`。`features/agent/mcp_server.py`。#1874）。認証は共通の API キー（使える製品に `agent`）だけで、キーを発行した利用者の権限・対象範囲で判定し、キーに業務 Agent の範囲があればさらに絞る（範囲の外は 403）。ツールは `agent_list` / `agent_chat` / `agent_get_run` / `agent_answer_confirmation` / `agent_skill_export`（契約は `platform/contracts/mcp/agent-tools.json`）。
  - MCP から作った Run（`metadata.source=mcp`・キーの ID と名前）は、無人の実行と同じく承認の要るツールを呼ばない（`mcp.approval_required`。#1813 と同じ流れ）。回答前の確認（#1839）は構造化して返し、`agent_answer_confirmation` で答える。
  - Run・会話の見え方は画面と同じ（キーの持ち主が system_admin なら他の利用者の Run を読み、会話を続けられる。Run はキーの持ち主として実行する。#1928）。
  - キーごとの直近 24 時間の Run の数の上限（`PLATFORM_API_KEY_RUNS_PER_DAY`。超えたら 429 相当のツールのエラー）。1 分あたりの呼び出しの回数は共通の認証が数える。
  - RAG は Agent を呼ばない（Agent の MCP を呼ぶのは外部のクライアントだけ）。サービストークンでの Agent の MCP の呼び出しは持たない。
- RAG / NL2SQL / 外部 MCP は「MCP 接続」1 つの仕組みで管理する（旧「外部 RAG」「外部 NL2SQL」「外部 MCP」は持たない）。RAG / NL2SQL は標準の接続 `rag` / `nl2sql`（画面の名前は「ナレッジ検索（RAG）」「データ問い合わせ（NL2SQL）」。認証はサービストークンで、削除・名前・認証方式の変更はできない）。URL は `AGENT_EXTERNAL_RAG_MCP_URL` / `AGENT_EXTERNAL_NL2SQL_MCP_URL` で、配備がこれを与えた接続は URL も変えられない（API は 400、`base_url_locked`）。空の構成（ローカルの開発・その製品を配備しない構成）だけ画面で URL を設定できる。外部の MCP は認証方式（なし / API キー / OAuth client credentials / サービストークン）を選んで追加する。
- MCP 接続のツールは、組み込み Runtime から `tool_registry.invoke`（`definition` / `handler` を渡す）を通してだけ呼ぶ（policy・masking・監査を再利用する）。承認の要否は MCP の `readOnlyHint` とツール権限（`<接続>__<ツール>` の名前）。外部 API key・token を snapshot、API、ログ、Artifact に出さない。
  - 例外は画面の操作の「RAG の図の根拠を開く」（`GET /api/runs/{run_id}/figure-url`）だけで、Run のツール呼び出しではないため `tool_registry` を通さず、**画面を見ている利用者**を `sub`・`purpose=figure_url` にしたサービストークンで RAG の `rag_read_source`（`include_image_url`）を呼び、返った短命の URL を保存しない（`features/agent/rag_figures.py`）。
- サービストークンは呼び出しごとの `issue_service_token`（`sub` = Run の利用者 `RunState.created_by_user_uuid`、なければ `AGENT_MCP_SERVICE_USER_LOGIN_ID` のサービス利用者。`aud` = 接続の audience）。承認後の実行も承認者ではなく Run の利用者で呼ぶ。業務 Agent にデータの範囲があれば、その接続の範囲の ID を claim `profile_ids` に入れ、呼び先でも強制させる（#1379。範囲なしなら付けない）。
- 再試行は、送信前の失敗と 429 / 503 は全メッセージ、502 / 504 は手順と読み取り専用（`readOnlyHint`）のツールだけで、書き込みのツールは呼び先に届いた後の失敗では再送しない。待ちは backoff + jitter（`Retry-After` に従う）、呼び出し全体は接続の timeout に収める（docs/agent-control-plane-design.md §4.1）。
