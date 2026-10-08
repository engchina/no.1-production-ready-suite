# 業務支援の合成の評価セット（#1231）

業務支援の改修（#1218）で、回答が「資料で答えられる」「確認が要る」「現場のデータが要る」「資料に答えが無い」
「資料が矛盾する」の 5 分類の質問に正しく対応するか（対応・手順・危険な回答・条件）を測るための、合成の評価セットです。
資料はすべて架空の「サンプル業務ポータル」の内容で、実在の顧客・製品の内容は入れていません。

| ファイル | 内容 |
|---|---|
| `portal-operations-manual.pdf` | 運用手順書（検証用アカウントの登録・権限の付与（個別 / グループ）・通知・反映待ち・削除） |
| `regional-report-guide-v2.pdf` / `-v1.pdf` | 地域別集計表の出力手順。第 2 版（締め日 10 日）が第 1 版（5 日）を置き換える（資料の矛盾） |
| `error-e1023-notes.pdf` | 障害対応メモ。原因は 2 つのどちらかで、認証ログを見ないと分からない（現場のデータ） |
| `portal-parameters.xlsx` | パラメータの一覧（既定値・設定例。現場の現在の設定値ではない） |
| `business-support.json` | 評価セット（19 問。うち往復のあるケース 3 問・既知の条件を渡すケース 1 問）。`relevant_document_ids` と `required_evidence[].document_id` は `file:<ファイル名>` |
| `support-guides.json` | C（業務ガイドあり）の業務ガイド 2 つ（アクセス権限の付与: 付与先 `target` 個別 / グループ、アカウントの削除: 部門長の承認 `approved` はい / いいえ）。運用手順書の記載だけから作った（#1289）。参照は `file:<ファイル名>`。アクセス権限の付与の承認・影響範囲（グループ）は、グループに付与する分岐の手順だけに係る（`impact.steps`。#1320） |
| `sources/*.html` | PDF の原稿 |

## 資料を作り直す

原稿（`sources/*.html`）を直したら、リポジトリ直下から次で PDF / xlsx を作り直して commit します（LibreOffice と
日本語のフォントが要ります）。

```bash
uv run --project rag/backend python rag/scripts/generate_business_support_corpus.py
```

## 取り込んで評価する

評価セットの `file:` の参照は、配備先で取り込んだ文書の ID に置き換えてから実行します。`evaluation_corpus_cli` が
ナレッジベースを作り、資料をアップロードして索引まで待ち（確認待ちのゲートは承認する。Excel は前処理
`excel_to_json`）、置き換えた評価セットを書き出します。

```bash
cd rag/backend
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/business-support/business-support.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/business-support.resolved.json
uv run python -m app.rag.evaluation_cli /tmp/business-support.resolved.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/business-support.result.json
```

同じナレッジベースへ取り込み直すときは `--knowledge-base-id` を渡します。質問ごとに回答（モデルの呼び出し 3〜5 回）を
作るため、19 問（往復は返答ごとに回答を作る）で 20 分前後かかります。

## A / C / D を比べる（handoff §14.3。#1289）

| 経路 | 内容 | 実行 |
|---|---|---|
| A | 業務ガイドなし（全体の既定とナレッジベースの指定） | `evaluation_cli` に `--output` の評価セット |
| C | 業務ガイドあり（検索・回答プロファイルを指定した評価。#1249） | `evaluation_cli` に `--guided-output` の評価セット |
| D | C と同じ検索・回答プロファイルを使う業務 Agent（スキル `business_rag_research`）の Run | `agent_evaluation_cli run` |

`evaluation_corpus_cli` に `--guides` を渡すと、資料を取り込んだナレッジベースを参照する検索・回答プロファイルを作り、
`support-guides.json`（参照の `file:` は取り込んだ文書の ID に置き換える）を取り込んで公開し、そのプロファイルで評価する
評価セットを `--guided-output` に書きます。

```bash
cd rag/backend
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/business-support/business-support.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/bs.resolved.json \
  --guides ../evaluation/business-support/support-guides.json --guided-output /tmp/bs.guided.json
# A / C（RAG の評価 job）
uv run python -m app.rag.evaluation_cli /tmp/bs.resolved.json --api-base-url http://127.0.0.1:8000 --output /tmp/a.json
uv run python -m app.rag.evaluation_cli /tmp/bs.guided.json --api-base-url http://127.0.0.1:8000 --output /tmp/c.json
# D（Agent の Run。C のプロファイルの ID は bs.guided.json の search_answer_profile_id）
uv run python -m app.rag.agent_evaluation_cli run /tmp/bs.resolved.json \
  --agent-api-base-url http://127.0.0.1:8020 --create-agent \
  --search-answer-profile-id <C のプロファイル> \
  --guides ../evaluation/business-support/support-guides.json --output /tmp/d.json
# 比べる表（docs/answer-baseline-2026-10.md に貼る）
uv run python -m app.rag.agent_evaluation_cli summarize A=/tmp/a.json C=/tmp/c.json D=/tmp/d.json
```

`evaluation_cli` は閾値を下回ると終了コード 1 を返しますが、結果の JSON は書きます。

D の採点は、RAG の評価ランナーと同じ関数（`app.rag.evaluation.score_case_answers` / `summarize_case_results`）で
行います。この CLI は RAG の backend にあり、Agent は公開の HTTP API（`POST /api/runs`・`GET /api/runs/{id}`）だけで
呼びます（Agent のコードは import しません）。Agent の Run を、RAG の採点が読む応答の形にするときの違いは次のとおりです。

- 回答は Run の成果物 `answer`（最終の検証の後の本文）。引用は、その Run の `rag_search` / `rag_retrieve_evidence` が
  返した根拠です。根拠の本文は MCP の抜粋（`excerpt`。最大 1000 文字）なので、必要な根拠はその範囲で照合します。
- 対応（outcome）は、Run の成果物 `answer` の `outcome.value` です（結果の `outcome_source=agent_answer`。#1305）。
  Agent の Control Plane が、最後の `rag_search` の対応・確認の質問だけの回答か・最終の検証の結果・回答の段落の判定・
  現場のデータの道具の結果から、モデルを呼ばずに RAG の回答の記録と同じ語彙で決めます（決め方は
  `agent/docs/agent-control-plane-design.md` の「回答の対応」。決めた手がかりは `outcome.basis`）。
  対応を持たない成果物（#1305 より前の Agent）だけ、その Run の最後の `rag_search` の `outcome`
  （`outcome_source=agent_rag_search`）、それも無ければ回答の最後の行（最終の検証が足す定型の注記を除く）が問い（？）
  なら確認の質問、それ以外は RAG の評価と同じ推定（根拠の無い回答・拒答の文は拒答）にします
  （`outcome_source=inferred`）。結果の `agent.outcome_sources` に出所ごとの Run の数を残すので、推定が混じって
  いないかを確かめてから A / C と比べます。
- 往復のあるケースの返答は、同じ会話（`thread_id`）の次の Run として送ります。ケースの既知の条件（`conditions`）は
  Agent に構造化して渡す口が無いため、最初の質問の後ろに「（既知の条件: 付与先は「グループ」）」の文で足します。
- 1 Run の待ちの上限は `--run-timeout`（既定 900 秒）。終わらない Run は取り消してケースの失敗にします。承認待ちで
  止まった Run もケースの失敗です。結果の `agent.runs` に Run ごとの状態・時間・対応・最終の検証の状態・モデルの
  呼び出しの回数・ツールの呼び出しの回数があります。
- Agent の API は local の認証（`AGENT_AUTH_MODE=local`）で呼びます（RAG の評価の CLI と同じ）。

## 採点の考え方

- `expected_outcomes` は受け入れる対応の集合です。確認が要る質問は、確認の質問（`needs_clarification`）か、条件ごとに
  分けた回答（`conditional`）のどちらでも正しいとします（handoff §8.1「条件ごとの回答を安全に出せるなら条件付きの回答
  でよい」）。
- `forbidden_phrases` は、影響範囲を広げる操作・承認の無い削除・現場の値の断定など、勧めると危険な表現です。回答が
  「〜しない」と否定して書く場合に当たらないよう、勧める言い方で書きます。
- 回答の対応は、回答の記録が `outcome` を持つまでは診断からの推定です（結果の `outcome_source=inferred`）。推定は
  `needs_human_review` を「条件付きの回答」として扱うなど粗いため、分類ごとの内訳を見るときは推定であることに注意して
  ください。
- 語の照合（手順・危険な表現・条件・必要な根拠）は NFKC・大小文字・空白を無視します（全角の「３０」と「30」、
  改行の入り方の違いで結果が変わりません）。

## 区分・往復・既知の条件・別解・必要な根拠（#1284。handoff §14.1 / §14.2）

| 欄 | 意味 |
|---|---|
| `split` | `dev`（設定・業務ガイドの調整に使ってよい）/ `holdout`（調整に使わず、最後の確認だけに使う）。結果の `split_breakdown` に区分ごとの全指標・件数・失敗理由が出ます |
| `conditions` | 質問と一緒に渡す既知の条件の値（条件の id → 値）。検索・回答の request の `conditions` に渡します（チャットの確認の答え・MCP の `conditions` と同じ） |
| `turns` | 確認の質問への返答の列。各往復は `reply`（利用者の次の発話）・`conditions`（返答で分かった条件の値。前の値に足す）と、その往復の回答への期待（`expected_outcomes` / `expected_steps` / `acceptable_alternatives` / `forbidden_phrases` / `required_conditions`）を持ちます |
| `acceptable_alternatives` | `expected_steps` と同等の別の手順の列（例: 「ログアウト → ログインし直す」と「再ログイン」）。いちばん点の高い列で採点します |
| `required_evidence` | 最後の回答に必要な根拠（`id`・`document_id`・`text`）。引用のうち、その文書の chunk の本文が `text` を含めば取れたとみなし、ケースの `evidence_recall` と全体の `required_evidence_recall`（参考の集計。閾値には使わない）を出します |

- 往復のあるケースは、チャットと同じく返答を次の質問にし、前の往復（質問と回答）を会話の履歴として順に実行します。
  検索・回答プロファイルを指定した評価では、前の質問も業務ガイドの照合に使います。ケースの直下の対応・手順・危険な
  表現・条件の期待は最初の回答を、各往復の期待はその往復の回答を採点し、正解の文書・期待する語・必要な根拠は最後の
  回答で採点します。往復ごとの採点は結果の `turn_results` にあります。
- 渡した条件を確認の質問でもう一度聞いたら（回答の記録の `clarifications` の `condition_id`）、失敗理由
  `known_condition_reasked` を付けます。
- `conditions` の id は、業務ガイド（#1238）の条件の id に合わせます。この評価セットは、付与先を `target`（個別 /
  グループ）、部門長の承認を `approved`（はい / いいえ）とする業務ガイドを前提にしています（業務ガイドは
  `support-guides.json`。#1289）。業務ガイドを使わない評価（検索・回答プロファイルを指定しない評価）では、条件は回答に
  影響しません。
- 区分は、業務の流れ・資料の族ごとに分けています（同じ族の言い換えが dev と holdout に分かれないように）。holdout は
  アカウントの削除・パラメータの一覧・地域別集計表の版・資料に無いアカウントの自動の無効化の族で、19 問のうち 7 問
  （5 分類のどれも含む）です。holdout の結果を見て評価セットや業務ガイドを直したら、そのケースは dev に移します。
- 往復の返答・必要な根拠の語句と業務ガイドは、同梱の資料の記載だけから作っています（実在の顧客のデータや、資料に無い
  値を足していません）。業務ガイドの質問の例・照合の語には、評価セットの質問（特に holdout）を写していません（handoff
  §14.1「指南の生成・設定に使った質問は未見のテストにしない」）。アカウントの削除の業務ガイドは holdout の族に当たる
  ため、holdout の結果を見て業務ガイドを直したら、そのケースは dev に移します。

## nightly で流さない理由

nightly（`.github/workflows/rag-evaluation-nightly.yml`）の評価は、repo variable の `RAG_EVALUATION_API_BASE_URL`
が指す配備先の API に評価セットを送るだけで、variable が無ければ skip します（2026-10-08 の時点で未設定）。この
評価セットは、配備先に資料を取り込み（`evaluation_corpus_cli`。parser と OCI / Oracle が要る）、`file:` の参照を
文書 ID に置き換えてからでないと採点できず、回答も 1 問 3〜5 回のモデルの呼び出しで 20 分前後かかります（nightly
の評価の待ちの上限は 15 分）。新しい secret・変数（取り込んだナレッジベースの ID など）と実環境が要るため、nightly
には入れず、手動（上の「取り込んで評価する」）で流します。
