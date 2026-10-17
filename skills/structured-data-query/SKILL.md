---
name: structured-data-query
description: "業務データの照会（MCP サーバー nl2sql。Production Ready NL2SQL）で、自然言語の質問から SQL を作って実行し、表形式の結果（件数・金額・一覧・推移・集計）を返す。売上・受注・顧客・在庫など業務データの数値や一覧を尋ねられたら、SQL という言葉が無くてもこのスキルを使う。足りない条件（期間・対象など）は NL2SQL が利用者への問いとして返す。規程・手順など資料の内容は business-rag-research、資料と業務データの両方が要る調査は rag-and-structured-data を使う。"
compatibility: "MCP サーバー nl2sql（Production Ready NL2SQL の POST /api/mcp）に接続していること。"
license: MIT
metadata:
  production-ready-display-name: "構造化データ照会"
  production-ready-mcp-tools: "nl2sql:nl2sql_query nl2sql:nl2sql_get_job nl2sql:nl2sql_list_profiles nl2sql:nl2sql_recommend_profile"
  production-ready-tags: "nl2sql structured-data audit-sql"
---
NL2SQL（MCP サーバー `nl2sql`）へ質問を渡して表形式の結果を取得する。

## 使う MCP ツール

- MCP サーバー `nl2sql`: `nl2sql_query`・`nl2sql_get_job`・`nl2sql_list_profiles`・`nl2sql_recommend_profile`

ツールは MCP の素の名前で書く。使う Agent によって名前に接続の前置きが付く（例: `rag__rag_search`・`mcp__rag__rag_search`）。

## 入力の種類を分ける

手順に入る前に、利用者の入力を次のどれかに分ける。

- 質問（知りたいこと・調べてほしいことがある。業務の範囲外の質問を含む）: 「手順」に進む。
- あいさつ（「こんにちは」「よろしく」など）・お礼（「ありがとう」）・その他の質問でない入力（雑談・意味の取れない短い入力・「テスト」のような試し）: ツールを呼ばずに、用件を聞いて返す。
  - 「こんにちは。」「どういたしまして。」のように短く応じる（入力をなぞったり説明したりしない）。
  - 続けて、業務データ（件数・金額・一覧など）について何を知りたいかを聞く（例: 「こんにちは。業務データについて、知りたいことをお知らせください。」）。
  - 業務データの値も一般的な知識も答えない。
- あいさつ・お礼と質問が混ざる入力（「こんにちは、今月の受注の件数は？」）: 質問として「手順」に進む。

## 手順

1. 業務プロファイルを決める。`nl2sql_query` には業務プロファイルの ID（`profile_id`）が必須。
   - 業務プロファイルが分からなければ `nl2sql_recommend_profile`（候補を絞るときは `profile_ids`）か `nl2sql_list_profiles` で選び、その `id` を `profile_id` に渡す（業務 Agent にデータの範囲があれば、その範囲の中から選ぶ）。
2. `nl2sql_query` を呼ぶ。
   - `nl2sql_query` は SQL を作る前に、足りない情報・明確でない情報を確かめる（SQL の生成前の確認）。確認が要るものが無くなると SQL を作って実行する。
   - `nl2sql_query` / `nl2sql_get_job` は、ジョブが終わるまでツールの中で待ってから返す。
3. 結果の `status` ごとに「結果ごとの対応」に従う。

## 結果ごとの対応

結果の `status`:

- `pending` / `running`（ツールの中で待っても終わらなかった）: `nl2sql_get_job` に `job_id` と `wait_seconds=40` を渡して続きを取る。
  - 結果が出ないまま答えるときは、実行中であることと `job_id` を伝え、結果を推測で補わない。
- `needs_clarification`: SQL はまだ作っていない。
  1. 利用者がこの質問の中で既に答えている内容（前の確認への答え・質問の言葉。`next_step` の `user_answers`）で問いに答えられるなら、利用者に聞き直さずに 3. の形で呼び直す。
  2. 答えられなければ、結果の `next_step` の `questions` の問い（`requirement.question`）を 1 つだけ、候補があれば候補の名前を添えて利用者にそのまま聞き（表・列は物理名のまま。推測で選ばない・SQL や結果を作らない）、その回答では問いだけを返す。
  3. 答えが分かったら、`nl2sql_query` に同じ `question` と `profile_id`、`requirement_session_id` と `answer` を渡して呼び直す（手順 2 へ）。`answer` には次を入れる:
     - `item_id`
     - 答えに当たる候補の id の `selected_candidate_ids`。候補に無い答えは `free_text` に利用者の言葉のまま（どちらか一方だけ）
- `error`: `error_message` と `error_code` を伝える。
- `unanswerable`: `error_message`（この業務プロファイルで答えられない理由）を伝える。

## してはいけないこと

- 業務データの表名・列名を利用者に聞かない。表・列は `nl2sql_query` が業務プロファイルの schema で確かめ、決まらなければ `needs_clarification` で問いを返す。
- `needs_clarification` の問いを、別の業務プロファイルや言い換えた `question` で `nl2sql_query` を呼び直して避けない。
- 業務データのツールを呼ばずに、一般的な知識や推測で件数・金額・状態などの値を答えない。業務データで確かめられないとき・業務の範囲外の質問のときは、確かめられないため答えられないと答える（質問が曖昧で対象が分からないときは、確認の質問をする）。
- SQL は監査・説明用途として受け取り、この Runtime 内では実行しない。
