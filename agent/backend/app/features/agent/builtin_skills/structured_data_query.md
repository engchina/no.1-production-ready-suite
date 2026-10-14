## いつ使うか

業務データ（表形式の結果）が要る質問に使う。NL2SQL（MCP 接続 `nl2sql`）へ質問を渡して表形式の結果を取得する。

## 手順

1. 業務プロファイルを決める。`nl2sql_query` には業務プロファイルの ID（`profile_id`）が必須。
   - 業務プロファイルが分からなければ `nl2sql_recommend_profile`（候補を絞るときは `profile_ids`）か `nl2sql_list_profiles` で選び、その `id` を `profile_id` に渡す（業務 Agent にデータの範囲があれば、その範囲の中から選ぶ）。
2. `nl2sql_query` を呼ぶ。
   - `nl2sql_query` は SQL を作る前に、足りない情報・明確でない情報を確かめる（AI要件確認）。確認が要るものが無くなると SQL を作って実行する。
   - `nl2sql_query` / `nl2sql_get_job` は、ジョブが終わるまでツールの中で待ってから返す。
3. 結果の `status` ごとに「結果ごとの対応」に従う。

## 結果ごとの対応

結果の `status`:

- `pending` / `running`（ツールの中で待っても終わらなかった）: `nl2sql_get_job` に `job_id` と `wait_seconds=40` を渡して続きを取る。
  - 結果が出ないまま答えるときは、実行中であることと `job_id` を伝え、結果を推測で補わない。
- `needs_clarification`: SQL はまだ作っていない。
  1. 結果の `next_step` の `questions` の問い（`requirement.question`）を 1 つだけ、候補があれば候補の名前を添えて利用者にそのまま聞き（表・列は物理名のまま。推測で選ばない・SQL や結果を作らない）、その回答では問いだけを返す。
  2. 利用者が答えたら、`nl2sql_query` に同じ `question` と `profile_id`、`requirement_session_id` と `answer` を渡して呼び直す（手順 2 へ）。`answer` には次を入れる:
     - `item_id`
     - 答えに当たる候補の id の `selected_candidate_ids`。候補に無い答えは `free_text` に利用者の言葉のまま（どちらか一方だけ）
- `error`: `error_message` と `error_code` を伝える。
- `unanswerable`: `error_message`（この業務プロファイルで答えられない理由）を伝える。

## してはいけないこと

- SQL は監査・説明用途として受け取り、この Runtime 内では実行しない。
