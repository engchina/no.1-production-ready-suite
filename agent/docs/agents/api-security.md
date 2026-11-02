# Agent の API・セキュリティ・技術スタック

> agent/AGENTS.md の詳細。agent/AGENTS.md の規則と同じ強さで守る。

## API

- Snapshot 正式版は `agent-control-plane.snapshot.v2`。業務 Agent はツールの項目を持たない（保存済みの `tool_names`・`migration_required` は読むときに無視する。#3240）。
- `POST /api/runs` は `agent_id` と `goal`（会話の続きは `thread_id`、確認の取り消しの操作は `clarification_cancel`。#1441・#1445）を受け取り、組み込み Runtime で実行する。旧エンジン（v1 の Run・`tool_calls`・planner・Memory・`X-Agent-API-Version: 1`）は持たない。
- Skill は instructions と MCP ツールの許可リスト（`mcp_requirements`）だけを持つ。ToolCall のテンプレート（`tool_calls`）・`POST /api/skills/plan`・`agent_skill_run` は持たない。

## セキュリティ

- manifest ID を検証する。
- 業務 Agent が使う Skill の削除は `409`。
- RBAC は viewer / operator / approver / auditor / admin を維持する。自分の業務 Agent の作成（`agent.agents.build`）はロールに対応させず、持ち主の判定は router の `_policy_manages_agent` で行う（#2523）。
- 画面のログインは共通認証（Cookie のセッション）。capability は 5 ロールに対応し、利用者（local はローカル利用者）から `ActorPolicy` を作って router の既存の判定に流す。local でもユーザー・ロールは Oracle の `PLATFORM_*`。Agent 独自の header / JWT / 外部 policy の認可（旧 `AGENT_RBAC_*`）は持たない。対象範囲はエージェントだけで、検索・回答プロファイルの判定は RAG に任せる。
- 新しい API は必ず権限 manifest（`app/security/permissions.py`）に登録する（登録外は 403。完全性テストがある）。API は HTTP だけで、push は SSE、操作は REST にする（WebSocket は持たない。#2260）。
- 承認の決定者（`decided_by`）は Cookie の利用者から決め、request の値を使わない。

## 技術スタック（Agent 固有の分）

- Backend は httpx と Oracle（`python-oracledb`）。
- 状態は開発で memory / file、本番で Oracle AI Database。外部 queue・外部 vector DB は追加しない。
- 観測は Prometheus・OpenTelemetry・Langfuse。業務データの原文と secret は trace に送らない。
