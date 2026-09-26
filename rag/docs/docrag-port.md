# DocRAG（rag_poc）移植機能ガイド

sibling repo `../rag_poc`（DocRAG）の、解析から回答生成までの実装を本リポジトリへ移植した機能の使い方と設定をまとめる。経緯と各段階の PR は Epic #117 を参照。

- Gradio UI は移植していない。
- 既定値はすべて移植前と同じなので、何も設定しなければ従来どおりに動く。
- rag_poc の処理は選択肢として追加している。

## 構成

| 置き場所 | 内容 |
|---|---|
| `packages/docrag_core` | rag_poc の `src/docrag` を `entrypoints`（Gradio / HTTP）を除いてそのまま移したもの。import パスは `docrag.*` のまま。backend と docling サービスが依存する。回帰テストは CI の「DocRAG core」ジョブで実行する |
| `services/parsers/docling` | DocRAG の Docling 解析（読み順・段組補正、表セル補修、図内 OCR の集約、装飾画像の判定、任意の Vision 図説明）。`parser_artifacts.docrag_layout` に LayoutRecord を保持する |
| `backend/app/rag/docrag_chunking.py` | チャンク戦略 `docrag_small_to_big`（rag_poc の Small-to-Big 親子分割） |
| `backend/app/rag/docrag_answer.py` | 回答エンジン `docrag`（rag_poc の回答フローを backend の検索・rerank で駆動） |
| `backend/app/rag/business_view_knowledge.py` | 業務ビュー単位の知識（ドメインキーワード / Approved FAQ / 用語・ルール） |
| `backend/app/rag/document_crop.py` | 解析に使ったファイルからの bbox の切り出し（プレビューと回答画像で共用） |

LLM と VLM は、プロジェクト全体で openai SDK（OCI OpenAI 互換の Responses API）だけを使う。接続先はモデル設定（`OCI_ENTERPRISE_AI_*`）。embedding と rerank は Cohere（OCI SDK）のままである。

## 3 層モデルでの責務

| 機能 | 層 | 設定する場所 |
|---|---|---|
| Docling 解析・Vision 図説明 | 文書レシピ | 検索・回答設定 > 文書解析（Docling 選択時の「図・画像を AI で読み取る」）、または文書のレシピ編集 |
| DocRAG 親子分割 | 文書レシピ | 検索・回答設定 > 文書分割「DocRAG 親子分割」、または文書のレシピ編集 |
| ドメインキーワード / Approved FAQ / 用語・ルール | 業務ビュー | 業務ビューを編集 >「業務ビューの知識」 |
| 回答エンジン / 全文検索の分割方式 / DocRAG の回答設定（質問拡張戦略・回答生成フロー・近傍 child 数・Rerank） | 業務ビュー | 業務ビューを編集 > 検索・回答設定 |

| 文書の分類（大分類・中分類・小分類）と有効期間 | 文書のメタデータ | 文書詳細の「文書の分類と有効期間」（`PUT /api/documents/{id}/classification`） |
| 分類フィルタ・基準日 | 検索要求 | RAG 検索 > 詳細条件 >「文書の分類で絞り込む」（`filters` の `large_category` / `middle_category` / `small_category` / `as_of`） |

KB（ナレッジベース）は検索対象の範囲を決めるだけで、上記のどれも持たない。

分類フィルタと有効期間は rag_poc の `_classification_filter_sql` と同じ意味で絞り込む。分類は指定した項目だけを完全一致で比べる。有効期間は基準日（未指定なら今日）で常に絞り、期間のない文書は除外しない。終了日は排他的（`effective_to` の当日は期間外）。分類と有効期間は文書単位で、レシピを切り替えても変わらない。

## 使い方（推奨の流れ）

1. **解析**：文書解析を Docling にする。図や画面キャプチャが多い文書では、Vision（図・画像を AI で読み取る）を有効にする。有効にすると、画像 1 枚ごとに LLM の呼び出しと時間がかかる。
2. **分割**：文書分割を「DocRAG 親子分割」にする。Docling の解析結果が必要で、他のパーサーの結果に対して選ぶとエラーで止まる。
3. **確認**：文書詳細の抽出タブで、次を確認できる。
   - 要素の種別と bbox
   - 表のテキスト化
   - Vision の読み取り内容（画面名・ボタン・表の行・操作手順など）と切り出し画像
4. **業務ビュー**：
   - 回答エンジンを「DocRAG（根拠照合・監査付き）」にする。
   - 必要なら DocRAG の質問拡張戦略・回答生成フロー・近傍 child 数・Rerank を上書きする（既定は自動ルーティング / CRAG / 3 / ON）。
   - 必要なら全文検索の分割方式を Sudachi にする。
   - 業務ビューの知識に、ドメインキーワード・Approved FAQ・用語・ルールを登録する。
5. **検索**：
   - 業務ビューを選んで検索すると、先に類似する承認済み FAQ を照会する。候補があれば「この FAQ の回答を使う（LLM を使わない）」か「類似問を使用しない」を選ぶ。
   - DocRAG の回答には「回答の根拠と実行記録（DocRAG）」パネル（信頼度、人手確認、根拠の構成、実行記録）が付く。
   - 過去の DocRAG 回答は、検索画面の「DocRAG の回答履歴」から回答・根拠・実行記録ごと開き直せる。チャットでは各回答の「保存された根拠と実行記録を開く」から開く。どちらも「この回答を削除」で個別に削除できる。
6. **評価**：DocRAG の回答パネル（RAG 検索・回答履歴・チャット）の「標準回答による評価」に期待する回答を入れて「標準回答で評価」を押すと、rag_poc の 4 軸評価（`docrag.evaluation.answer_eval`、各 5 点・合計 20 点、16 点以上で合格）を実行し、結果を回答記録に保存する。rag_poc と違い、生成の後に評価する。この機能より前に保存した回答は評価の入力を持たないので評価できない。
7. **チャット**：DocRAG エンジンでも会話履歴を使う。直前までの会話から質問を単独で意味が通る形に書き換えてから検索・回答する（書き換え後の質問は回答パネルに表示する）。

## 設定一覧

| 設定（env） | 既定 | 内容 |
|---|---|---|
| `RAG_PARSER_DOCLING_VISION_ENABLED` | `false` | Docling 解析で図と画像入りの表を Vision で説明する（文書レシピで上書きできる） |
| `RAG_ANSWER_ENGINE` | `standard` | 回答エンジンの全体既定。`docrag` で rag_poc の回答フローを使う（業務ビューで上書きできる） |
| `RAG_TEXT_SEARCH_TOKENIZER` | `builtin` | Oracle Text クエリの分割方式。`sudachi` で rag_poc の Sudachi 分割を使う（検索・回答設定 > 検索方法で変更でき、業務ビューで上書きできる）。業務ビューにドメインキーワードがあれば、`builtin` でも DocRAG の分割でキーワードを 1 語として優先する |
| `RAG_DOCRAG_QUERY_STRATEGY` | `auto_routing` | DocRAG 回答の質問拡張戦略（`simple_retrieval` / `rag_fusion` / `query_decomposition` / `step_back_prompting` / `hyde`）。業務ビューで上書きできる |
| `RAG_DOCRAG_ANSWER_FLOW` | `crag` | DocRAG 回答の回答生成フロー。`standard_rag` は補正検索をしない。業務ビューで上書きできる |
| `RAG_DOCRAG_NEIGHBOR_CHILD_COUNT` | `3` | DocRAG 回答で根拠の child の前後から context へ足す近傍 child 数（0〜20）。業務ビューで上書きできる |
| `RAG_DOCRAG_RERANK_ENABLED` | `true` | DocRAG 回答で検索候補を rerank で並べ替える。業務ビューで上書きできる |
| `RAG_APPROVED_FAQ_SEMANTIC_ENABLED` | `true` | 類似問の照合に embedding の意味類似度を加える |
| `RAG_DOCRAG_ANSWER_VISION_ENABLED` | `false` | DocRAG 回答で根拠の図を切り出して回答モデルへ添付する。回答モデルが画像入力に対応する場合だけ有効にする |
| `RAG_DOCRAG_HISTORY_REWRITE_ENABLED` | `true` | チャットで DocRAG エンジンを使うとき、会話履歴から質問を書き換える |
| `RAG_ANSWER_RECORD_RETENTION_DAYS` | `90` | DocRAG 回答記録の保存日数（`0` は無期限）。検索・回答設定 > 回答スタイルの「DocRAG 回答の保存期間」で変更できる |
| `RAG_DOCRAG_PROFILE` | `generic` | `legacy` で業務固有の分類・日本語問い合わせ規則を有効にする。規則は `DOCRAG_DOMAIN_PROFILE_FILE` の JSON から読む（書式は rag_poc の `domain_profile.example.json`）。業務固有の profile は同梱していない |
| `DOCRAG_RENDER_DPI`（docling サービス） | `300` | 解析時のページ画像の解像度。bbox はこの画像の px 座標になる |

docling サービスの Vision は、backend のサービス管理が橋渡しする `OCI_ENTERPRISE_AI_*`（endpoint / API キー / project / VLM モデル）を使う。モデル設定を変更したら docling サービスを再起動する。

## 保存先

- **文書の分類と有効期間**：`rag_documents.classification`（JSON）。migration `20260926_001_documents_classification` で列を追加する。ACL に使う `category_name` とは別に持つ。
- **業務ビューの知識**：`rag_business_view_knowledge`（業務ビュー × 種別、rag_poc の JSON payload のまま）。表は「システム設定 > データベース」のシステムテーブルから、migration `20260925_001_business_view_knowledge` で作成する。
- **親子チャンク**：子を `rag_chunks` に保存する。親の本文（`docrag_parent_text`）、検索用テキスト（`docrag_search_text`）、metadata v4（`docrag_metadata_json`）は子の metadata に持つ。
- **DocRAG の回答**：`rag_answer_records`（trace_id 単位で質問・書き換え後の質問・回答・引用・DocRAG の診断情報）。標準回答での評価の入力（`evaluation_input_json`、根拠の本文を含む）と評価結果（`evaluation_json`）も同じ行に持つ（migration `20260926_002_answer_record_evaluation`）。回答を同じ trace_id で保存し直すと評価結果は消える。migration `20260925_002_answer_records` で作成する。保存に失敗しても回答は返す。保存期間を過ぎた記録は、回答の保存時と保存期間の設定変更時に削除する。
- **切り出し画像**：保存しない。プレビューは `GET /api/documents/{id}/crop` で、回答時は一時ディレクトリで都度作る。

## rag_poc との差分

- 解析結果、チャンク、回答はファイル（`.runs/`）ではなく、本リポジトリの Oracle と Object Storage を使う。
- ADB の独自スキーマ（`rag_chunk_runs` / `rag_chunk_embeddings` など）は使わない。検索は backend の hybrid 検索（vector と Oracle Text の RRF）に、rag_poc の「原質問を主軸にした重み付き融合」と Sudachi 分割を組み合わせる。
- chicago / osaka の 2 系統 LLM 設定と、OCI SDK の LLM 経路は廃止した。
- 移植していないもの：
  - Gradio UI、PPT 資料、`verify/` の問題セット、評価スクリプト（`run_answer_eval.py` などの一括評価。1 件ずつの標準回答での評価は画面から使える）、質問履歴
  - フィードバックから FAQ への自動昇格

## 既知の制約

- 業務ビューを複数指定した場合、用語・ルールは先頭の業務ビューのものだけを使う。ドメインキーワードは和集合を使う。
- 業務ビューの知識の同時編集は後勝ちになる。
