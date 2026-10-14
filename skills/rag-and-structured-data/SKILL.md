---
name: rag-and-structured-data
description: "業務 RAG（MCP サーバー rag の rag_search）と業務データの照会（MCP サーバー nl2sql の nl2sql_query）を、順序を決めずに必要なだけ往復して調べる。規程の基準・期限で業務データを絞る、業務データで見つかった区分・コードの意味を規程で確かめる、など資料（手順・規則・定義・条件）と業務データ（件数・金額・状態・記録の値）の両方が要るかもしれない調査に使う。資料だけで答えられる質問は business-rag-research、業務データだけなら structured-data-query を使う。"
compatibility: "MCP サーバー rag（Production Ready RAG）と nl2sql（Production Ready NL2SQL）の POST /api/mcp に接続していること。"
license: MIT
metadata:
  production-ready-display-name: "資料とデータの横断調査"
  production-ready-mcp-tools: "rag:rag_search rag:rag_recommend_search_answer_profile rag:rag_list_search_answer_profiles nl2sql:nl2sql_query nl2sql:nl2sql_get_job nl2sql:nl2sql_list_profiles nl2sql:nl2sql_recommend_profile"
  production-ready-tags: "rag nl2sql business-data"
---
業務 RAG（資料）と NL2SQL（業務データ）のどちらを使うかをその都度判断し、順序を決めずに繰り返し使って調べる。

## 使う MCP ツール

- MCP サーバー `rag`: `rag_search`・`rag_recommend_search_answer_profile`・`rag_list_search_answer_profiles`
- MCP サーバー `nl2sql`: `nl2sql_query`・`nl2sql_get_job`・`nl2sql_list_profiles`・`nl2sql_recommend_profile`

ツールは MCP の素の名前で書く。使う Agent によって名前に接続の前置きが付く（例: `rag__rag_search`・`mcp__rag__rag_search`）。

## 入力の種類を分ける

手順に入る前に、利用者の入力を次のどれかに分ける。

- 質問（知りたいこと・調べてほしいことがある。業務の範囲外の質問を含む）: 「手順」に進む。
- あいさつ（「こんにちは」「よろしく」など）・お礼（「ありがとう」）・その他の質問でない入力（雑談・意味の取れない短い入力・「テスト」のような試し）: ツールを呼ばずに、用件を聞いて返す。
  - 「こんにちは。」「どういたしまして。」のように短く応じる（入力をなぞったり説明したりしない）。
  - 続けて、社内の資料と業務データについて何を知りたいかを聞く（例: 「こんにちは。社内の資料や業務データについて、知りたいことをお知らせください。」）。
  - 資料の内容も業務データの値も一般的な知識も答えない。
- あいさつ・お礼と質問が混ざる入力（「こんにちは、休暇の申請方法は？」）: 質問として「手順」に進む。

## 手順

ツールを呼ぶ順序は決めない。Step 1〜3 の判断のループを、答えに要る根拠がそろうまで繰り返す。

### Step 1: 答えに足りないものを判断する

問いと、それまでのツールの結果から、答えに足りないものをそのたびに判断する。

- 答えに要る根拠がそろった → それ以上ツールを呼ばずに答える（「回答の書き方」）
- 足りないものがある → Step 2

### Step 2: 足りないものに合うツールを呼ぶ

- 資料の根拠が要る → `rag_search`
  - 検索・回答プロファイル（`search_answer_profile_id`）が決まっていなければ、最初に 1 回 `rag_recommend_search_answer_profile` で選び、以後も同じものを使う（業務 Agent にデータの範囲があれば、その範囲の中から選ぶ）。`recommended_profile_id` が null で候補が 2 件以上なら、候補の名前を選択肢にして利用者に確かめる。候補が無ければ `rag_list_search_answer_profiles` で選ぶ。
- 業務データの値が要る → `nl2sql_query`（`nl2sql_query` が先でもよい）
  - `nl2sql_query` には業務プロファイルの ID（`profile_id`）が必須。分からなければ `nl2sql_recommend_profile` か `nl2sql_list_profiles` で選ぶ（業務 Agent にデータの範囲があれば、その範囲の中から選ぶ）。
- 一方の結果で分かった事実・条件・識別子（コード・名前・期間・区分・状態）を使って、もう一方や同じツールを何度でも呼んでよい。
  - 例: 規程で分かった基準・期限で業務データを絞る。
  - 例: 業務データで見つかった区分・ID の意味や扱いを資料で確かめる。

### Step 3: 結果を確かめて Step 1 に戻る

- 結果に応じて「結果ごとの対応」に従ってから、Step 1 に戻る。
- 同じ目的で言い換えて呼び直しても新しい根拠が出ないときは繰り返さず、確かめられなかった点として示して、分かったことまでを答える。

## 結果ごとの対応

- `rag_search` の `outcome` が `needs_environment_data`（資料だけでは確定できない）: 結果の `next_step` に従い、`confirmations` の点を `nl2sql_query` で確かめてから答える。
- `nl2sql_query` が `pending` / `running` を返した: `nl2sql_get_job` で続きを取る。
- `nl2sql_query` が `needs_clarification` を返した: SQL はまだ作っていないので、`next_step` の `questions` の問いを 1 つだけ利用者に聞き、答えを `answer`、`requirement.session_id` を `requirement_session_id` に入れて呼び直す。

## 回答の書き方

- 確かめた値は NL2SQL の結果を、手順・規則は資料の根拠を出所として分けて示す。

## してはいけないこと

- 資料の根拠のツールも業務データのツールも呼ばずに、一般的な知識や推測で業務の事実を答えない。資料・業務データで確かめられないとき・業務の範囲外の質問のときは、確かめられないため答えられないと答える（質問が曖昧で対象が分からないときは、確認の質問をする）。
