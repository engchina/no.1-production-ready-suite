# Agent のアーキテクチャ不変条件

> agent/AGENTS.md から移した詳細（#2276）。agent/AGENTS.md の規則と同じ強さで守る。規則を変えるときはこのファイルを直し、AGENTS.md には規則の要約と案内だけを書く。

## アーキテクチャ不変条件

- 実行は Control Plane の**組み込み Runtime**（`features/agent/builtin_runtime.py`）。
  - SDK は OpenAI Agents SDK（`openai-agents`。版を固定し、Dependabot の PR で上げる）。
  - モデルは OCI Enterprise AI の Responses API（システム設定 > モデルの接続）。別の LLM provider は組み込まない。
  - SDK の tracing は常に無効にする（業務データを外部へ送らない）。
- 外部の Runtime（OpenClaw / Hermes / DeerFlow など）・Binding・Docker の Runtime は持たない（#754）。
- 指示は「Agent の指示 + 割り当てた Skill の指示」。ツールは Skill の requirement が必要とするものだけを function tool として渡す。`server_id="control-plane"` は `tool_registry` のツール、それ以外は MCP 接続（`rag` / `nl2sql` / 登録した接続）の `tools/list` のツール（名前は `<接続>__<ツール>`）。Skill の指示は MCP のツールを素の名前（`rag_search`）で書いてよく、Run のときにモデルへ渡す名前へ書き直す。モデルが無い名前のツールを呼んでも Run は落とさず、呼ぶ名前をモデルへ返して呼び直させる（読み替えて実行はしない。#1303）。
- ツールの実行は `tool_registry.invoke`（ポリシー・ガードレール・監査・成果物）を必ず通す。
  - ポリシーが「拒否」のツールはモデルに渡さない。
  - 「承認」のツールは SDK の `needs_approval` で中断する。中断した Run は `waiting_approval` にし、SDK の状態を Run に保存する。
  - 承認がすべて決まったら状態を復元して再開する。却下したツールは実行しない。
- 業務 Agent は版を持つ。編集は下書きで、「公開」した版だけが利用者の Run に使われる。「この版に戻す」で前の版を公開し直す。下書きで実行できるのは Agent 管理（admin）だけ。
  - 公開の版は使うスキルの中身（指示・説明・MCP のツール・中身の hash）を固定し、公開中の版の Run は固定した中身で動く（下書きは今のスキル。組み込みのスキルも固定する）。スキルが変わった業務 Agent は「スキルが更新されています」を出し、再公開で取り込む（#1855）。
- Run は Agent を選ぶだけで作れる（Binding の選択は無い）。開発は API のプロセスで実行（`in_process`）し、本番は Oracle checkpoint の row lock と Run lease を使う runtime-dispatcher（`python -m app.features.agent.runtime_dispatcher`）。
- 保存先: Run・業務 Agent と、画面・API で変えた定義（Skill・MCP 接続・ツール権限）は `AGENT_RUNTIME_REPOSITORY_BACKEND` に従い、Oracle では共通の `PLATFORM_ORACLE_*` で `AGENT_RUNTIME_*` / `AGENT_CONTROL_PLANE_ITEMS` に保存する（テーブルはシステムテーブルが作る。アプリは DDL を実行しない）。
  - MCP 接続の秘密は `app.secret_box` で暗号化する。`.env` の宣言は保存しない。
  - 既定の `auto` は、DB の設定がそろっていれば `oracle_checkpoint`、無ければ `memory`（保存しない。再起動で消える）を起動時に選ぶ（`storage_backend`）。保存先の 1 件の Run の不整合では起動を止めず、Run 単位で直す・退避する（`quarantined`）。
  - DB に接続できないあいだも起動し（Run の repository は import 時に作らない）、接続できた時点で再起動せずに読み込む（`storage_bootstrap`。それまで業務の API は 503。docs/agent-control-plane-design.md §5.1.1）。
  - Oracle は Run・業務 Agent を 1 件 = 1 行（`AGENT_RUNTIME_ENTITIES`）で持ち、変えたものだけを書く（#1939。migration 016 が以前の全体の snapshot を移す）。repository の変更は変えた Run・業務 Agent に印を付け（`_mark_run_changed` など。Event を足すと自動）、書き込みは lock を放した後に行う（event loop の上の変更はバックグラウンドで書く）。`router.py` の `async def` の handler は repository を `run_in_threadpool` で呼ぶ（`tests/test_router_threadpool.py`）。
  - 書くプロセスは head（`AGENT_RUNTIME_CHECKPOINTS` の行の版 `VERSION_NO`・`WRITER_ID`）を lock し、前回の同期の後に別のプロセスが書いた行を取り込んでから書く（`runtime_checkpoint_store`。#1916。片方の書き込みがもう片方の更新を消さない。両方が同じ 1 件を変えたときだけ後の値）。
  - テストは `tests/conftest.py` で memory に固定する。保存先の状態は `GET /api/runtime/storage` が返し、「実行環境」の「保存先」のカードと、定義・実行を作る画面の先頭の `NonPersistentStorageNotice`（warning の Banner）が案内する。
- 利用状況・フィードバックの集計: Oracle の構成は Run の事実（`AGENT_RUN_FACTS`。1 Run = 1 行）を SQL で集計する（期間は 365 日まで。書き込みは `run_facts_store` のバックグラウンドの MERGE で、Run を止めない）。memory / file はメモリの Run を集計する（docs/agent-control-plane-design.md §5.2）。
- Agent はスキルだけを持つ。プラグイン（Skill・MCP・resource の配布単位）とマーケットプレイスは作り直さない（#1798・#1803 で削除）。スキルはビルトイン・`AGENT_SKILLS_DIR` の SKILL.md・`AGENT_SKILLS_DEFINITIONS_JSON`・画面で入れ、MCP 接続は直接登録する。
- SKILL.md と同じディレクトリの `references/` の下のテキスト / Markdown は、スキルの参照文書（`references`）として読む（Agent Skills の progressive disclosure。symlink を辿らず、ディレクトリの外を読まず、件数・大きさに上限）。指示には索引だけを入れ、その Run にだけ `skill_reference_read` を渡して必要な部分を読む。読み取りも `tool_registry.invoke` のポリシー・監査を通す。`scripts/` などの同梱のコードは読まず・実行しない。
- スキルは Agent Skills の形（`<name>/SKILL.md` ＋ `references/` の zip）で書き出せる（`GET /api/skills/{skill_id}/export`・画面のスキルの操作・MCP の `agent_skill_export`。`features/agent/skill_export.py`。#1874）。組み込みのスキルは `skills/` と同じ内容、画面のスキルは frontmatter を作り、`skills-ref` の検証を通す。
- Prompt / Workflow / Template は非実行・版管理 resource。独立 Workflow engine を作らない。
