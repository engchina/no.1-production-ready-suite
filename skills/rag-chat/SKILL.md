---
name: rag-chat
description: "業務 RAG（MCP サーバー rag。Production Ready RAG）のチャットの流れ（rag_chat）で、社内の規程・手順書・台帳・業務ガイドなどの資料から、RAG が登録済みQ&A・業務ガイド・根拠の拡張を使って作った回答をそのまま返す。社内の規則・手続き・業務の進め方を尋ねられたら、資料という言葉が無くてもこのスキルを使う。登録済みQ&Aの選択と、業務ガイドの条件の確認も RAG が返す。基本の検索で早く答えるなら rag-search、業務データの値も要る調査は rag-chat-and-data を使う。"
compatibility: "MCP サーバー rag（Production Ready RAG の POST /api/mcp）に接続していること。"
license: MIT
metadata:
  production-ready-display-name: "RAG チャット"
  production-ready-mcp-tools: "rag:rag_chat rag:rag_recommend_search_answer_profile rag:rag_list_search_answer_profiles"
  production-ready-final-answer-tools: "rag:rag_chat"
  production-ready-tags: "rag chat business-guide"
---
資料の質問を `rag_chat` に渡し、RAG が作った回答をそのまま返す（MCP サーバー `rag`）。回答の書き方は RAG の検索・回答プロファイルが決める。ツールは MCP の素の名前で書く（Agent によって `mcp__rag__` などの前置きが付く）。

## Step 1: 入力の種類を分ける

- 質問（業務の範囲外を含む）と、あいさつ・お礼に質問が混ざる入力（「こんにちは、休暇の申請方法は？」）: Step 2 に進む。
- あいさつ・お礼・その他の質問でない入力（雑談・意味の取れない短い入力・「テスト」のような試し）: ツールを呼ばず、短く応じてから、社内の資料（規程・手順書・台帳など）について何を知りたいかを聞く（例: 「こんにちは。社内の資料や手順について、知りたいことをお知らせください。」）。入力をなぞらず、資料の内容も一般的な知識も答えない。

## Step 2: 検索・回答プロファイルを決める

- 業務 Agent のデータの範囲か質問で決まっていれば、それを使う。
- 質問や利用者がプロファイルの名前を示したら、`rag_list_search_answer_profiles` の `query` で名前から id を引く。
- 決まっていなければ `rag_recommend_search_answer_profile` に質問を渡す。
  - `recommended_profile_id` があればそれを使う。ほかのプロファイルを順に試さない。
  - `recommended_profile_id` が null で `candidates` が 2 件以上なら、候補の名前（と `documents` の資料名）を選択肢にして、どれで調べるかを利用者に確かめる。
  - 候補が無ければ `rag_list_search_answer_profiles` の名前・説明で選び、それでも決まらなければ利用者に確かめる。

## Step 3: 質問する

- `rag_chat` を 1 回呼ぶ。`query` は利用者の質問のまま。質問を分けたり言い換えたりしない。

## Step 4: 結果ごとの対応

- `status` が `approved_faq_candidates`（登録済みQ&Aの候補）か、`outcome` が `needs_clarification`（業務ガイドの条件の確認）: 利用者に選んでもらうか答えてもらってから、同じ `query` と `search_answer_profile_id` で呼び直す（渡す引数は `approved_faq_id`・`skip_approved_faq`・`conditions`・`skip_clarification` の説明のとおり）。
- それ以外で `answer` がある: 言い換え・要約・追記をせずに、そのまま回答にする。根拠は `evidence`（`used_in_answer` が回答で引用した箇所）にある。

## Step 5: してはいけないこと

- `rag_chat` を呼ばずに、一般的な知識や推測で業務の事実を答えない。範囲外に見える質問も、先に `rag_chat` で調べる。
- 登録済みQ&Aの候補・確認の選択肢を自分で選ばない。
- `answer` に無いこと（自分の作業の経過・ツールの名前・推測）を回答に足さない。
