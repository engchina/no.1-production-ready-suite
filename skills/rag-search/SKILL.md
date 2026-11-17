---
name: rag-search
description: "業務 RAG（MCP サーバー rag。Production Ready RAG）の検索（rag_search）で、社内の規程・手順書・台帳などの資料を検索し、RAG が根拠（文書名と節・頁）から作った回答をそのまま返す。登録済みQ&A・業務ガイド・根拠の拡張を使わない基本の検索と回答で、資料の内容を早く確かめたいときに使う。登録済みQ&A・業務ガイドの確認を含むチャットと同じ回答は rag-chat、業務データの値も要る調査は rag-search-and-data を使う。"
compatibility: "MCP サーバー rag（Production Ready RAG の POST /api/mcp）に接続していること。"
license: MIT
metadata:
  production-ready-display-name: "RAG 検索"
  production-ready-mcp-tools: "rag:rag_search rag:rag_recommend_search_answer_profile rag:rag_list_search_answer_profiles"
  production-ready-final-answer-tools: "rag:rag_search"
  production-ready-tags: "rag search"
---
資料の質問を `rag_search` で検索し、RAG が作った回答をそのまま返す（MCP サーバー `rag`）。回答の書き方は RAG の検索・回答プロファイルが決める。ツールは MCP の素の名前で書く（Agent によって `mcp__rag__` などの前置きが付く）。

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

## Step 3: 検索する

- `rag_search` を 1 回呼ぶ。`query` は利用者の質問のまま（指示語・省略は前の質問で補い、それだけで意味の通る文にする）。質問を分けたり言い換えたりしない。

## Step 4: 結果ごとの対応

- `answer` がある: 言い換え・要約・追記をせずに、そのまま回答にする。根拠は `evidence`（`used_in_answer` が回答で引用した箇所）にある。

## Step 5: してはいけないこと

- `rag_search` を呼ばずに、一般的な知識や推測で業務の事実を答えない。範囲外に見える質問も、先に `rag_search` で調べる。
- `answer` に無いこと（自分の作業の経過・ツールの名前・推測）を回答に足さない。
