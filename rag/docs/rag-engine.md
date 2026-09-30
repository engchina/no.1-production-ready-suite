# rag_poc から移植した機能のガイド（回答フロー・親子階層・Docling 解析）

sibling repo `../rag_poc` の、解析から回答生成までの実装を本リポジトリへ移植した機能の使い方と設定をまとめる。経緯と各段階の PR は Epic #117 を参照。

> 用語: 画面と本書では、回答フロー・親子階層（small-to-big）・回答の記録など RAG の標準の用語で呼ぶ（#598）。コードの識別子・設定・API・DB の名前も、#599 で rag_poc から移したときの名前から RAG の標準の名前にした（旧名と新しい名前の対照は [deployment.md の「既存環境の更新手順（#599）」](./deployment.md)）。

- Gradio UI は移植していない。
- 回答は #594 でこの回答フロー（`AnswerEngine`）だけにした（回答エンジンの選択は削除。標準の回答フローのコード・画面・設定は #595 で削除した）。文書分割の既定も親子階層（`small_to_big`）にした。既存環境の更新手順は [deployment.md の「既存環境の更新手順（#594）」](./deployment.md#既存環境の更新手順594-回答の方式を-1-つにする) と「既存環境の更新手順（#595）」。
- 解析・分割の rag_poc の処理は選択肢として追加している（解析の既定は Docling）。

## 構成

| 置き場所 | 内容 |
|---|---|
| `packages/rag_engine` | rag_poc の解析・分割・検索・回答の実装を、`entrypoints`（Gradio / HTTP）を除いてそのまま移したもの。package と import パスは `rag_engine`（#599）。backend と docling サービスが依存する。回帰テストは CI の `RAG / Engine` ジョブで実行する |
| `services/parsers/docling` | rag_poc から移した Docling 解析（読み順・段組補正、表セル補修、図内 OCR の集約）。`parser_artifacts.layout_records` に LayoutRecord を保持する。Vision は行わない（#497） |
| `backend/app/rag/vision.py` | 解析後の図・画像の読み取り（Vision）。Docling を含む全ての解析エンジンで、rag_engine の `describe_layout_pictures`（対象と周辺文脈の 2 枚の画像・装飾画像の skip・表内画像の検出・prompt の metadata）を既定の Vision モデルで実行する。Docling は結果を `layout_records` の record にも書き戻す（#497） |
| `backend/app/rag/chunking_small_to_big.py` | チャンク戦略 `small_to_big`（画面の表示名は「親子階層（small-to-big）」。rag_poc の Small-to-Big 親子分割） |
| `backend/app/rag/answer_engine.py` | 回答フロー（rag_poc の回答フローを backend の検索・rerank で駆動。#594 から回答はこれだけ） |
| `backend/app/rag/business_view_knowledge.py` | 業務ビュー単位の知識（ドメインキーワード / Approved FAQ / 用語・ルール） |
| `backend/app/rag/document_crop.py` | 解析に使ったファイルからの bbox の切り出し（プレビューと回答画像で共用） |

LLM と VLM は、プロジェクト全体で openai SDK（OCI OpenAI 互換の Responses API）だけを使う。接続先はモデル設定（`OCI_ENTERPRISE_AI_*`）。embedding と rerank は Cohere（OCI SDK）のままである。

## 3 層モデルでの責務

| 機能 | 層 | 設定する場所 |
|---|---|---|
| Docling 解析 | 文書レシピ | 検索・回答設定 > 文書解析、または文書のレシピ編集 |
| 図・画像を AI で読み取る（Vision） | 文書レシピ | 文書のレシピ編集（解析エンジンに関係なく選べる）。全体の既定は `backend/.env` の `RAG_VISION_ENABLED`。文書解析の画面の「解析後の処理」から保存できる（#497 / #528） |
| 親子階層（small-to-big） | 文書レシピ | 検索・回答設定 > 文書分割「親子階層（small-to-big）」（分割パラメータ 5 項目もここで設定）、または文書のレシピ編集 |
| ドメインキーワード / Approved FAQ / 用語・ルール | 業務ビュー | 業務ビューを編集 >「業務ビューの知識」 |
| 回答の設定（質問の拡張・回答の生成方式・根拠の前後から加える数・Rerank・画面目録で操作画面を探す） | 業務ビュー | 業務ビューを編集 > 検索・回答設定（#594 で回答エンジンの選択を削除し、常に表示する） |
| 回答の検索と生成の全体既定（質問の拡張・回答の生成方式・根拠の前後から加える数・Rerank・画面目録で操作画面を探す） | global | 検索・回答設定 > 検索方法「回答の検索と生成」（`GET` / `PATCH /api/settings/answering`。`backend/.env` の `RAG_*` に保存する。#593）。業務ビューで上書きできる |

| 文書の分類（大分類・中分類・小分類）と有効期間 | 文書のメタデータ | 文書詳細の「文書の分類と有効期間」（`PUT /api/documents/{id}/classification`） |
| 質問履歴（記録するか・保存期間・最小回数・件数・除外する語） | global | 検索・回答設定 > 検索方法「質問履歴」（既定は無効。#593 で回答スタイルの画面から移した） |
| 分類フィルタ・基準日 | 検索要求 | RAG 検索 > 詳細条件 >「文書の分類で絞り込む」（`filters` の `large_category` / `middle_category` / `small_category` / `as_of`） |

| 回答生成のプロンプト（`vlm_answer`） | global | 検索・回答設定 > 回答プロンプト（各段のプロンプトは読み取り専用で表示） |
| 図・画像の読み取りプロンプト（`image_retrieval`） | global | 検索・回答設定 > 文書解析（常に表示。文書のレシピで Vision を有効にしたとき、解析エンジンに関係なく使う） |

KB（ナレッジベース）は検索対象の範囲を決めるだけで、上記のどれも持たない。

編集したプロンプトは `rag_answer_prompts` に保存する（#599 で旧名の表から改名した。migration `20260930_008_answer_prompts_table`）。回答では rag_engine の runtime の `prompt_overrides` で渡し、解析では backend の Vision の段（`app/rag/vision.py`）が読み取りの指示として使う（#497 以前は docling サービスへ `parser_options` で渡していた）。未編集なら rag_poc と同じコードの既定値を使う。画像の読み取りプロンプトの変更は、解析済みの文書には再解析するまで反映しない。

分類フィルタと有効期間は rag_poc の `_classification_filter_sql` と同じ意味で絞り込む。分類は指定した項目だけを比べる。保存・検索の入力の両方で表記をそろえ（NFKC・前後の空白・連続する空白。`app/schemas/classification.py` の `normalize_category_value`）、比較は先頭の番号の接頭辞（`10_` など）を除いた名前で行う（rag_engine の `_category_label` と同じ。保存値の接頭辞は残す。#547）。文書詳細の分類の入力は、保存済みの分類の値（`GET /api/documents/classification-options`）を候補に出す。既存の文書の分類の表記は `uv run python -m app.rag.classification_normalization --dry-run` で件数を確かめ、Oracle のバックアップ後に `--apply` でそろえる。アップロードはファイル名だけを送り、フォルダの相対パスを持たないため、rag_poc のパスからの分類の推定は移していない。有効期間は基準日（未指定なら今日）で常に絞り、期間のない文書は除外しない。終了日は排他的（`effective_to` の当日は期間外）。分類と有効期間は文書単位で、レシピを切り替えても変わらない。

回答の業務の絞り込み（#545 / #546 / #553）: 業務の範囲の正本は業務ビュー・ナレッジベース（検索範囲）で、その中で質問が大分類の名前を含むときだけ、さらにその業務の候補に絞る。大分類の語の一覧は、検索範囲（`filters` と同じ条件）の文書に保存済みの大分類の DISTINCT（`OracleClient.retrieval_large_categories`）で、1 回の回答で 1 回だけ読む。質問との比較は #547 の正規化（NFKC・空白・番号の接頭辞を外す `category_label`）と大文字・小文字の違いを無視して行い、長い名前から照合する（「業務A」の中の「業務」は拾わない）。一致した大分類は rag_engine の `AnswerDependencies.business_domains` で質問の理解（`inquiry_conditions.business_domains`）へ渡し、rag_engine の `_same_business_records`（一致する候補が無ければ絞らない）と business_match のチャネルが使う。名指しが無い、範囲外の大分類、一致する候補が無いときは絞らない。質問の業務名に domain profile の `business_patterns` は使わない（下の `RAG_ENGINE_DOMAIN_PROFILE_FILE`）。

## 使い方（推奨の流れ）

1. **解析**：文書解析を Docling にする。図や画面キャプチャが多い文書では、文書のレシピで Vision（図・画像を AI で読み取る）を有効にする（Docling 以外の解析エンジンでも使える）。有効にすると、画像 1 枚ごとに Vision モデルの呼び出しと時間がかかる。
2. **分割**：文書分割は既定で親子階層（`small_to_big`。#594 で既定にした）。親子の分割は Docling の解析結果（`parser_artifacts.layout_records`）を入力にする。解析結果が Docling でない文書（Unstructured・MinerU・dots.ocr・OCI の解析結果など）では、失敗させずに「構造認識」（`structure_aware`。chunk size・overlap・最小文字数は文書分割の設定値）で分割する（#300）。縮退したことは各 chunk の metadata（`chunk_strategy=structure_aware`、`chunk_strategy_requested=small_to_big`、`chunk_strategy_fallback_reason=layout_missing`）と取込の trace（`effective_chunk_strategy`）に残り、文書詳細の Chunk タブと分割プレビューに「構造認識で分割しました」と表示する。親子で分割するには、文書解析を Docling にして再解析し、Chunk を作り直す。子・親の大きさは同じ画面の「戦略別パラメータ」で変えられる（下の設定一覧）。
3. **確認**：文書詳細の抽出タブで、次を確認できる。
   - 要素の種別と bbox
   - 表のテキスト化
   - Vision の読み取り内容（画面名・ボタン・表の行・操作手順など）と切り出し画像
4. **業務ビュー**：
   - 必要なら質問の拡張・回答の生成方式・根拠の前後から加える数・Rerank・画面目録で操作画面を探すを上書きする（既定は自動ルーティング / CRAG / 3 / ON / OFF。回答エンジンの選択は #594 で削除した）。
   - 業務ビューの知識に、ドメインキーワード・Approved FAQ・用語・ルールを登録する。
5. **検索**：
   - 業務ビューを選んで検索すると、先に類似する承認済み FAQ を照会する。候補があれば「この FAQ の回答を使う（LLM を使わない）」か「類似問を使用しない」を選ぶ。
   - 回答には「回答の根拠と実行記録」パネル（信頼度、人手確認、根拠の構成、実行記録）が付く。
   - 保存された回答（回答の記録）は、チャットの各回答の「この回答の根拠と実行記録」から回答・根拠・実行記録ごと開き直せる（会話の回答の trace_id で保存の有無を引き当てる。#304）。「この回答を削除」で個別に削除できる。検索画面の回答履歴の一覧は #444 で削除した（一覧の API `GET /api/search/answers` は、チャットの引き当て（`trace_id` の指定）が使うため残している）。
   - 扱えるのは自分の回答だけ（詳細・評価・削除も同じ）。SYSTEM_ADMIN と `rag.feedback.manage` を持つ利用者は、利用できる業務ビューのすべての利用者の回答を扱える（#304）。
   - 参照するナレッジベースが 0 件の業務ビューでは検索・チャットしない（利用者が使える全 KB を検索しない）。画面は選んだ時点で理由を示し、API は 409 と理由（「この業務ビューには参照するナレッジベースがありません。…」）を返す（#304）。
   - RAG 検索・チャットで選ぶ業務ビューは 1 つ（#635。同じ選択欄 `BusinessViewSelect`）。回答はその業務ビューの参照 KB と検索・回答設定・知識で作る。
6. **評価**：回答の根拠と実行記録のパネル（RAG 検索・チャット）の「標準回答による評価」に期待する回答を入れて「標準回答で評価」を押すと、rag_poc の 4 軸評価（`rag_engine.evaluation.answer_eval`、各 5 点・合計 20 点、16 点以上で合格）を実行し、結果を回答記録に保存する。rag_poc と違い、生成の後に評価する。この機能より前に保存した回答は評価の入力を持たないので評価できない。評価は LLM を複数回呼ぶため、この API だけ時間の上限を長くしている（#304）：backend は評価全体を LLM 1 回の timeout の設定の上限（600 秒）で打ち切って 504 と理由を返し、評価を保存しない。画面は 630 秒、Nginx（`init_script.sh` が生成する、評価と回答生成・MCP の `location ~ ^/api/(search|search/stream|search/answers/[^/]+/evaluation|evaluation/run|evaluation/compare|chat/conversations/[^/]+/messages/stream|mcp)$`）は 660 秒待つ（backend の理由が画面に届くよう、外側ほど長くする）。
7. **フィードバック**：回答を「役に立たなかった」と評価するときに、rag_poc の分類（ナレッジ不足・情報が古い・質問が曖昧を含む）と修正した回答を入力できる。管理者はフィードバック画面の詳細から、Approved FAQ への登録と品質評価のケースへの追加ができる。フィードバック画面の一覧・集計・詳細は、SYSTEM_ADMIN はすべての利用者の分、ほかのロールは自分が送った分だけで、画面の先頭に見える範囲を案内する（#408）。
8. **検証**：`uv run python -m app.rag.answer_verify_cli`（`answers` / `regression` / `crag-goldset`）で、QA の一括の標準回答評価、rag_poc の問い合わせ回帰、CRAG goldset の評価を実行できる（`docs/evaluation-observability-guardrails.md`）。
9. **チャット**：会話履歴を使う。直前までの会話から質問を単独で意味が通る形に書き換えてから検索・回答する（書き換え後の質問は回答パネルに表示する）。

## 設定一覧

| 設定（env） | 既定 | 内容 |
|---|---|---|
| `RAG_CHUNKING_STRATEGY` | `small_to_big` | 文書分割の方式（#594 で既定を `structure_aware` から親子階層に変えた。文書レシピで上書きできる） |
| `RAG_CHUNK_CHILD_TARGET_CHARS` | `1000` | 親子階層の子チャンク目標文字数（300〜1,600）。超える `Text` / `List-item` は文末で分ける |
| `RAG_CHUNK_TABLE_CHILD_TARGET_CHARS` | `3000` | 表の子チャンク目標文字数（300〜8,000）。超える表だけ行グループに分け、列見出しを繰り返し付ける |
| `RAG_CHUNK_PARENT_TARGET_CHARS` | `6000` | 親チャンク目標文字数（1,200〜10,000） |
| `RAG_CHUNK_PARENT_MAX_PAGES` | `3` | 親チャンク最大ページ数（1〜5） |
| `RAG_CHUNK_PARENT_MAX_CHILDREN` | `12` | 親チャンク最大 child 数（3〜20） |
| `RAG_VISION_ENABLED` | `false` | 解析の後に図と画像入りの表を既定の Vision モデルで説明し、図の要素の本文にする。全ての解析エンジンで使える（文書レシピで上書きできる。#497。旧 `RAG_PARSER_DOCLING_VISION_ENABLED` は読まない） |
| `RAG_QUERY_STRATEGY` | `auto_routing` | 質問の拡張（query rewriting / expansion）の方式（`simple_retrieval` / `rag_fusion` / `query_decomposition` / `step_back_prompting` / `hyde`）。検索・回答設定 > 検索方法「回答の検索と生成」で変更でき、業務ビューで上書きできる |
| `RAG_ANSWER_FLOW` | `crag` | 回答の生成方式。`crag`（CRAG）は検索結果を評価して必要なら補正検索し、`standard_rag`（標準 RAG）は補正検索をしない。検索・回答設定 > 検索方法「回答の検索と生成」で変更でき、業務ビューで上書きできる |
| `RAG_NEIGHBOR_CHILD_COUNT` | `3` | 回答で根拠の child の前後から context へ足す近傍 child 数（0〜20）。検索・回答設定 > 検索方法「回答の検索と生成」で変更でき、業務ビューで上書きできる |
| `RAG_RERANK_ENABLED` | `true` | 回答で検索候補を rerank で並べ替える。検索・回答設定 > 検索方法「回答の検索と生成」で変更でき、業務ビューで上書きできる |
| `RAG_SCREEN_LINKING_ENABLED` | `false` | 画面目録で操作画面を探す（rag_poc の画面目録の連携、#554）。検索範囲の文書の番号付きの見出し（「（２）帳票印字設定」など）の目録から、質問を解決する画面を LLM で選び、その画面の child chunk（画面ごとに最大 10 件）と親を検索候補に加える。候補は足すだけで減らさず、順位は rerank が決める。回答ごとに LLM の呼び出しが 1 回増える。目録は検索範囲（`filters` と同じ条件）の全文書の `section_path` を DB で集計して作り（`OracleClient.retrieval_screen_sections`）、検索範囲と索引の状態（chunk の件数と chunk_id・文書名の hash。文書の追加・削除・再索引で変わる）ごとに process 内で cache する。選んだ画面の chunk も DB から読む（`retrieval_screen_chunks`）ので、検索で出なかった画面も候補に加わる。rag_engine の `AnswerDependencies.screen_catalog` / `screen_chunks` で注入する。検索・回答設定 > 検索方法「回答の検索と生成」で変更でき、業務ビューで上書きできる（業務ビューを編集 > 検索・回答設定 >「回答の検索のオプション」） |
| `RAG_APPROVED_FAQ_SEMANTIC_ENABLED` | `true` | 類似問の照合に embedding の意味類似度を加える |
| `RAG_ANSWER_VISION_ENABLED` | `true` | 画像を見て答える必要がある質問（rag_engine の `should_include_image_evidence`）では、根拠の図を切り出して既定の Vision モデル（システム設定 > モデル）へ添付して回答する。それ以外の回答・質問の拡張・監査は既定のテキストモデル（チャットの比較ではその列のモデル）で行う。Vision モデルが未設定なら添付しない（#649） |
| `RAG_HISTORY_REWRITE_ENABLED` | `true` | チャットで、会話履歴から質問を書き換える |
| `RAG_ANSWER_RECORD_RETENTION_DAYS` | `90` | 回答の記録の保存日数（`0` は無期限）。検索・回答設定 > 検索方法の「回答の記録の保存期間」で変更できる（#593 で回答スタイルの画面から移した） |
| `RAG_ANSWER_PROFILE` | `legacy` | 回答フローの業務 profile。回答フローは rag_engine の `current_profile()`（runtime を渡さないときの既定 = `legacy`）で動くため、rag_poc と同じく日本語問い合わせ規則が有効で、業務分類・別名は `RAG_ENGINE_DOMAIN_PROFILE_FILE` の JSON（未指定なら作業ディレクトリの `domain_profile.json`、なければ分類・別名なし）から読む（書式は rag_poc の `domain_profile.example.json`。業務固有の profile は同梱していない）。既定はこの実際の挙動に合わせて `legacy`（#300。以前の既定 `generic` は回答フローに届いていなかった）。`generic` は既存の `.env` との互換のため受け付けるが、回答フローには反映されない |
| `RAG_ENGINE_DOMAIN_PROFILE_FILE`（backend の process の環境変数） | 未指定 | legacy profile の JSON（業務固有の語。書式は rag_poc の `domain_profile.example.json`。業務固有の profile は同梱していない）。rag_engine の `rag_engine.profiles` が process の環境変数（`os.environ`）を直接読み、未指定なら作業ディレクトリ（backend は `rag/backend/`）の `domain_profile.json` を読む。どちらのファイルもなければ分類・別名・判定語なしで動く。`backend/.env` / 共通 `.env` に書いても process の環境変数にはならないため効かない。指定するときは systemd の unit の `Environment=` か、起動する shell の `export` で渡す。読んだ内容は process 内で cache するため、変えたら backend を再起動する。rag_engine の設定（`build_engine_settings`）には読み先を渡していない（#569）。質問の業務名（`business_patterns`）には使わない（検索範囲の大分類の語の一覧から照合する。#553）。今も使う項目は、質問の検索語の別名（`aliases`）、ファイル・データの確認と外部連携の判定語（`file_data_terms` / `external_context_terms`）、操作手順の節ラベル（`operation_section_pattern`）、問い合わせ元の語（`requester_terms`）、大分類・中分類の候補（`categories`。business_match の番号付きの分類名の照合）と、取込時の chunk の `retrieval_profile.business_domains`（`business_patterns`。business_match の照合先の 1 つ）。画面からは管理できない |
| `RAG_ENGINE_RENDER_DPI`（docling サービス） | `300` | 解析時のページ画像の解像度。bbox はこの画像の px 座標になる |

Vision は backend がモデル設定の既定の Vision モデル（OCI Enterprise AI）で呼ぶ。docling サービスは LLM を呼ばないため、OCI Enterprise AI の設定は渡さない（#497）。

Docling 以外の解析エンジンの bbox は単位が違うため、Vision の段がページ画像の px へそろえる。確かでない bbox は切り出さず、要素の metadata に `vision_status=skipped` / `vision_skip_reason=bbox_unit_unknown` と warning `vision_bbox_unit_unknown` を残す。

| 解析エンジン | bbox の単位 | 根拠 |
|---|---|---|
| Docling | サービスが描いたページ画像の px（`RAG_ENGINE_RENDER_DPI`） | `layout_records.pages` の寸法から同じ解像度で描き直す。実サービス（docling 2.129.0）で確認済み（A4 の PDF は 2480x3509、画像は元画像の px）。#502 |
| Unstructured | coordinates の座標系（PixelSpace 等）の px | registry が `layout_width` / `layout_height` を要素と asset の `page_width` / `page_height` に写す。無ければ不明。実サービス（unstructured 0.27.8、hi_res）で確認済み（PDF は PixelSpace で 350 dpi 相当のページ画像の px。A4 で 2893x4094。画像は元画像の px）。#502。座標系が左下原点（`PointSpace` / `RelativeCoordinateSystem`）なら y を反転する（#512） |
| Dots.OCR | 描いたページ画像の px（`RAG_PARSER_DOTS_OCR_DPI`） | `pages` の寸法。画像ファイルは元画像の px（寸法を `pages` に入れる）。モデルが返す bbox は、入力画像を smart_resize（28 の倍数、画素数 3136〜11289600）した寸法の px なので、backend（`app/clients/external_parser.py`）が受け取ったときに送った画像の px へ戻す（公式の parser の `post_process_cells` と同じ換算）。根拠は下の「座標系の根拠」 |
| MinerU | ページに対して 0-1000 に正規化した座標（左上原点） | content_list の bbox。1000 を超えれば不明。根拠は下の「座標系の根拠」（この repo の実サービスでは未確認。ai-foundations-lab の検証と一致。#512） |
| OCI Enterprise AI の VLM 解析 | 0-1 または 0-100 | 構造化抽出の prompt の指定。100 を超えれば不明 |
| 全ての解析エンジン | 値が全て 1 以下なら 0-1 | 画像ファイル全体の `source_image` の asset も含む |

座標系の根拠（#502。ローカルに接続先がない MinerU と Dots.OCR は公式のソースで確認した）:

- MinerU: 2.5.4（tag `mineru-2.5.4-released`）の `mineru/backend/pipeline/pipeline_middle_json_mkcontent.py` の `make_blocks_to_content_list` が `int(x0 * 1000 / page_width)` などで 0-1000 にする。4.0.10（tag `mineru-4.0.10-released`）の `mineru/render/_internal/content_list/common.py` の `normalize_bbox` も「MiddleJson の 0-1 の bbox を Content List の 0-1000 の整数にする」（`int(value * 1000)`）。原点（左上）は、backend の変換と同じ前提のまま（ソースでは未確認）。
- Dots.OCR: github.com/rednote-hilab/dots.ocr（commit `36d7248`、2026-03-24）の `dots_ocr/utils/layout_utils.py` の `post_process_cells` が、bbox を smart_resize した入力画像の寸法（コメント: 「server input width, also has smart_resize in server」）から元画像の寸法へ戻している。`dots_ocr/utils/image_utils.py` の `smart_resize` と `dots_ocr/utils/consts.py`（`IMAGE_FACTOR=28`・`MIN_PIXELS=3136`・`MAX_PIXELS=11289600`）、Hugging Face の `rednote-hilab/dots.ocr` の `preprocessor_config.json`（`Qwen2VLImageProcessor`、`patch_size` 14・`merge_size` 2・同じ min / max pixels）。backend は OpenAI 互換 API を直接呼ぶので、この換算を backend で行う。換算しないと、A4 を 200 dpi で描いたページで縦に約 0.5%（最大 13px）、400 dpi など 11289600 px を超えるページでは約 15% ずれていた。
- Dots.OCR の Picture（本文のない図）は asset の kind が `picture` になる。#502 までは Vision の対象の種類に入っておらず、読み取っていなかった。

ai-foundations-lab の検証との照合（#512）:

利用者が別のリポジトリ [engchina/ai-foundations-lab](https://github.com/engchina/ai-foundations-lab)（commit `572e9fa66116b69f71054b6ee9cbbc0e50a6366b`、2026-08-19）の `20260819/`（PDF レイアウト比較の Gradio アプリ）で、各エンジンの bbox を同じページ画像（pymupdf で 300 dpi。`pdf_layout_lab/rendering.py`）の左上原点の px（`image_top_left`）へ直し、React ビューアで重ねて確かめている。換算は次のとおりで、上の表と一致する。この repo で変えたのは、Unstructured の左下原点の座標系の反転だけ。

| 解析エンジン | lab の換算（ファイル） | この repo | 判断 |
|---|---|---|---|
| MinerU | content_list / content_list_v2 の bbox は 1000x1000 で割り、ページ画像の寸法を掛ける。y は反転しない（左上原点）。middle.json（`pdf_info[].page_size` の pt）と model.json（`page_info.width/height`）は、その寸法で割る（`adapters/mineru.py` の `_bbox_to_page_image`・`_records_from_flat_content_list`、`adapters/mineru_api.py`、`tests/test_mineru_adapter.py`・`tests/test_mineru_api_adapter.py`） | content_list だけを使い、1000x1000 で割る（`vision.py` の `_bbox_scale`） | 一致。#502 で未確認だった原点（左上）を lab の重ね合わせで確認とする。lab の 2 例（A4 の 2480x3500 のページへの換算）をテストにした（`test_mineru_bbox_matches_ai_foundations_lab`） |
| Dots.OCR | 後継の dots.mocr の bbox を、300 dpi のページ画像の px としてそのまま使い、ページ内へ収めるだけ（`adapters/dots_mocr.py` の `analyze`）。README は、300 dpi の A4 の画像パッチを約 44,000 とし、`prompt_grounding_ocr` の bbox は「processor 側の resize と一致しない」とする | smart_resize 後の入力画像の px から、送った画像の px へ戻す（`external_parser.py` の `_dots_bboxes_to_page_px`） | #502 が正しい。lab の記録（44,000 ≒ 2492x3500 / 14²、resize と合わない）は、bbox が smart_resize 後の寸法の px であることと合う。300 dpi の A4 は縮小されず 28 の倍数への丸め（x 12px・y 9px、0.5% 未満）しか差がないため、lab ではそのままでも重なって見えた。この repo の既定の 200 dpi や、11289600 px を超えるページでは差が出るので換算を残す。dots.mocr の `preprocessor_config.json`（Hugging Face `rednote-hilab/dots.mocr`、revision `e539fbb`）も dots.ocr と同じ値（14・2・3136・11289600）。lab の値をテストにした（`test_dots_300dpi_a4_matches_ai_foundations_lab_observation`） |
| Unstructured | `metadata.coordinates.points` の最小・最大を、`system.width / height` で割ってページ画像の寸法を掛ける（`adapters/unstructured_adapter.py`、`tests/test_unstructured_adapter.py`: 350 dpi 相当の 2894x1930 から 300 dpi の 2481x1654 へ） | registry が points を最小・最大の bbox にし、座標系の寸法を `page_width` / `page_height` に写す。`vision.py` がその寸法で割る | 一致。lab の例をテストにした（`test_unstructured_bbox_matches_ai_foundations_lab`） |
| 共通 | 左下原点の bbox は `coordinates.py` の `pdf_bottom_left_to_image_top_left`（`top = (page_height - max(y)) * scale`）で左上原点へ直す（`tests/test_coordinates.py` で往復を確認） | #512 まで、座標系の原点を見ていなかった | 取り込んだ。Unstructured の `PointSpace` と `RelativeCoordinateSystem` は `Orientation.CARTESIAN`（左下原点。`unstructured/documents/coordinates.py`）なので、`bbox_coordinate_system` がこの 2 つなら `vision.py` の `_page_px_bbox` が同じ式で y を反転する。unstructured 0.27.8 の PDF はどの戦略でも `PixelSpace`（pdfminer の左下原点は `rect_to_bbox` で左上へ直してある）なので、今の parser サービスでは通らない（防御）。`test_bottom_left_coordinate_system_is_flipped` |

- lab と #502 の食い違い: MinerU の middle.json の単位。lab（MinerU 2.x の `mineru[core]>=2.0` と mineru-api の hybrid-engine）は pt（`page_size` で割る）、#502 で読んだ 4.0.10 の `normalize_bbox` は「MiddleJson の 0-1」。版で違う可能性があるが、この repo は `/file_parse` の content_list（どちらの版も 0-1000）だけを使うので影響しない。middle.json を使うときは、値の範囲で決めず `page_size` と版を確かめる。
- 取り込んでいないもの: lab の Dots.OCR の応答の寛容な読み取り（`{"elements": [...]}` などの包み・切れた JSON）、Picture の切り出しの再 OCR と Mermaid 化は、座標の変換ではないので #512 の対象外。
- 未確認のまま: /Rotate のあるページで、MinerU の `page_width` / `page_height` が回転後の寸法か（lab も回転したページは確かめていない。ビューアの回転は表示だけ）。Dots.OCR と Vision の段は pymupdf が回転を反映して描くので、送った画像と bbox は合う。
- この確認では Unstructured の parser サービス（127.0.0.1:18022）が起動していなかったので、実サービスでは確かめ直していない（#502 の実サービスの確認と、parser サービスの venv の unstructured 0.27.8 のソースで確認した）。

## 保存先

- **質問履歴**：`rag_query_history`（業務ビュー単位。安全チェックでマスクした後の質問・正規化した質問・分類条件）。migration `20260926_005_query_history` で作成する。設定が有効なときだけ、回答に成功した質問（検索とチャット）を記録し、保存期間を過ぎたものを削除する。候補は rag_poc の `suggest_query_history_questions`（最小回数・類似度・分類・除外する語）で出す。
- **文書の分類と有効期間**：`rag_documents.classification`（JSON）。migration `20260926_001_documents_classification` で列を追加する。ACL に使う `category_name` とは別に持つ。
- **業務ビューの知識**：`rag_business_view_knowledge`（業務ビュー × 種別、rag_poc の JSON payload のまま）。表は「システム設定 > データベース」のシステムテーブルから、migration `20260925_001_business_view_knowledge` で作成する。
- **親子チャンク**：子を `rag_chunks` に保存する。親の本文（`parent_text`）、検索用テキスト（`engine_search_text`）、metadata v4（`engine_metadata_json`）は子の metadata に持つ。
- **回答の記録**：`rag_answer_records`（trace_id 単位で質問・書き換え後の質問・回答・引用・回答フローの診断情報・持ち主 `user_id_hash`）。持ち主は回答を生成した利用者（監査 context の `user_id_hash`）で、migration `20260928_002_answer_record_owner` が列と index（`rag_answer_records_owner_idx`）を足す。既存の行（持ち主なし）は、同じ trace_id のチャットの回答（`rag_messages`）か検索の監査（`rag_search_audit`。監査を Oracle に保存している環境だけ）から利用者が 1 人に決まるものだけ持ち主を補い、補えなかった行は持ち主なしのまま SYSTEM_ADMIN と `rag.feedback.manage` を持つ利用者だけが扱える（一般の利用者のチャットからは開けない）。標準回答での評価の入力（`evaluation_input_json`、根拠の本文を含む）と評価結果（`evaluation_json`）も同じ行に持つ（migration `20260926_002_answer_record_evaluation`）。回答を同じ trace_id で保存し直すと評価結果は消える。migration `20260925_002_answer_records` で作成する。保存に失敗しても回答は返す。保存期間を過ぎた記録は、回答の保存時と保存期間の設定変更時に削除する。
- **切り出し画像**：保存しない。プレビューは `GET /api/documents/{id}/crop` で、回答時は一時ディレクトリで都度作る。
- **プレビューのページ画像と bbox の強調（#349）**：PDF（Office は変換済み PDF）は `GET /api/documents/{id}[/recipes/{recipe_id}]/preview-pages`（ページ数と、/Rotate を反映した向きの寸法 pt）と `…/preview-pages/{page}?variant=&dpi=`（1 ページの PNG。pymupdf で都度描き、保存しない。dpi は 48〜288、1 枚 16M px まで）でページ画像として表示し、描けないときは従来のブラウザの PDF 表示に戻す。強調は rag_poc の viewer と同じく、子 chunk の `engine_metadata_json.layout.display_regions`（要素ごとの bbox。解析時のページ画像 px・左上原点・`[x1, y1, x2, y2]`）を、そのページの解析時の寸法（抽出結果の `pages[].width/height`、無ければ chunk の `page_width` / `page_height`）に対する % に直して重ねる。表示領域の無い chunk・要素・表セルは従来どおり包含 bbox 1 つを重ねる。画像と強調を同じ層に置いて回転・拡大するので、表示の倍率や回転に関係なく位置が合う。保存するデータは増やしていない（既存の chunk がそのまま強調できる）。

親子階層（small-to-big）の 5 項目は rag_poc の「チャンキング」tab と同じ名前・既定値・範囲（`rag_engine.chunking.constants` の `DEFAULT_*` / `*_RANGE`）。検索用テキストの 3 項目（`contextual_search_text_enabled` / `search_text_context_max_chars` / `child_search_text_max_chars`）は rag_poc でも画面に出していないので、既定値のまま使う。親子階層は検索用テキストを自分で組み立てるため、文書分割の「文脈ヘッダを検索対象へ追加」は効かない（画面でも親子階層を選んだときは出さない）。5 項目を既定から変えた文書だけ chunk_set_id が変わる（既定のままなら変わらない）。

## 標準の回答フローの設定の削除（#300 / #594 / #595）

回答フロー（`AnswerEngine`）は回答の設定（`rag_query_strategy` など）と回答生成のプロンプトで回答し、標準の回答フロー向けの設定は読まなかった（#300）。#594 で回答をこのフローだけにし、#595 で次の画面・欄・API を削除した。

| 削除した設定 | あった場所 | 今の回答フローでの扱い |
|---|---|---|
| 検索モード・検索オプション（クエリ拡張・LLM マルチクエリ生成・gap-stop・業務適合加重・補正検索） | 検索・回答設定 > 検索方法、業務ビュー「検索方法」 | 「質問の拡張」で作った検索文ごとにハイブリッド検索し、補正は CRAG（回答の生成方式）が行う。検索方法の画面は「回答の検索と生成」「回答の記録の保存期間」「質問履歴」の 3 カードになった |
| 処理方式・補正検索（CRAG）のしきい値・再検索の上限回数・低 grade で回答を保留する | 検索・回答設定 > 根拠確認（`/settings/grounding`）、業務ビュー「根拠確認」 | 根拠の確認は「回答の生成方式」（CRAG / 標準 RAG）で選ぶ |
| 回答スタイル | 検索・回答設定 > 回答スタイル（`/settings/generation`）、業務ビュー「回答スタイル」 | 回答は回答生成のプロンプトの形で書く（回答の記録の保存期間と質問履歴は、#593 で検索方法の画面へ移した） |
| 高度な検索（検索の計画: 書き換え / HyDE / 分解） | 検索・回答設定 > 高度な検索（`/settings/agentic`） | 「質問の拡張」が同じ役割を持つ |
| system prompt の版（カスタム回答スタイル） | 検索・回答設定 > 回答プロンプト | 回答プロンプトの画面は回答生成のプロンプトだけになった |
| 回答の役割・口調・既定の回答言語 | 業務ビュー「回答プロンプト」 | 削除した（保存済みの値は読み込み時に捨て、次に保存すると消える） |

- 削除した画面の URL（`/settings/grounding`・`/settings/generation`・`/settings/agentic`）は検索方法（`/settings/retrieval`）へ移す。
- 削除した API は `GET/PATCH /api/settings/retrieval`・`/grounding`・`/generation`・`/agentic`、`GET/POST /api/settings/prompts`・`POST /api/settings/prompts/{version_id}/activate`。回答の設定は `/api/settings/answering`・`/answer-records`・`/query-history`・`/answer-prompts` だけになった。
- 保存済みのロールのメニュー権限 `menu.settings_grounding` / `menu.settings_generation` / `menu.settings_agentic` は読み捨てる。DB の行は #596 のシステムテーブルの更新で削除した（[deployment.md の「既存環境の更新手順（#596）」](./deployment.md)）。

安全チェックは質問と回答の両方に適用する。

## 回答エンジンを 1 つにする前の取り込み（#593）

standard の回答エンジンを消す（#592）前に、standard だけが持っていた次の機能を用意した。standard の挙動と既定は変えていない。

- **チャットのモデル比較**: チャットは比較の列ごとのモデルを `RagPipeline(answer_model_id=...)` で渡し、`AnswerEngine` → `build_engine_settings(answer_model_id=...)` がそのモデル（とその接続）で回答する。以前は列がすべて既定のモデルで答えていた。Vision のモデルは比較の対象ではなく、常に既定の Vision モデルを使う。
- **検索だけの経路**: `SearchRequest.retrieval_only`（既定 `false`）が `true` のとき、`AnswerEngine.retrieve` が回答の検索（`_search`）を原質問 1 本で呼び、候補を引用として返す（回答は空）。質問の理解・質問の拡張・rerank・CRAG・回答の生成は行わず、LLM を呼ばない。回答の記録・質問履歴も保存しない（検索の監査は残す）。進捗は `retrieval` の 1 工程。KB の検索テストとレシピの検索比較が使う。レシピの比較の `filters.chunk_set_id` は Oracle の検索条件（`_oracle_retrieval_where`）でそのまま効く。回答エンジンが standard のときは今までどおり回答していた（`retrieval_only` を無視する。#594 で standard は呼ばれなくなった）。
- **全体既定の画面**: 質問の拡張・回答の生成方式・根拠の前後から加える数・Rerank・画面目録で操作画面を探すの全体既定を、検索・回答設定 > 検索方法「回答の検索と生成」で変えられる（`GET` / `PATCH /api/settings/answering`。権限は `menu.settings_retrieval`）。回答の記録の保存期間と質問履歴のカードも同じ画面へ移した（API の権限も `menu.settings_retrieval` に変えた）。
- **回答フローの進捗**: 回答フローの各工程（`rag_engine.generation.execution_record._execution_step`。質問の理解・文書検索（1回目）など）の開始と終了を、`answer` の中の入れ子の工程として進捗（SSE の `stage`）へ流す。工程の名前は `answer_step:<工程名>` で、画面の進捗と時間切れの文言は工程名をそのまま出す（`ANSWER_STEP_STAGE_PREFIX`。frontend の `answer-progress.ts` と同じ）。
- **固定の同義語 14 組**（`query_transform.SYNONYM_GROUPS`）は既定の別名（aliases）へ移さない。会計・文書管理の一般語と英訳の組で、業務ごとの別名（業務ビューの用語・ルール、domain profile の `aliases`）と重なり、移すと回答の検索語が今の挙動から変わるため。#595 で `query_transform.py` ごと削除した。

## 親子階層（`hierarchical_parent_child`）の削除（#271）

LlamaIndex AutoMerging 風の分割方式「親子階層」は削除し、一覧の同じ位置（再帰文字分割の次）に親子階層（small-to-big）を置いた。

- 保存済みの `hierarchical_parent_child`（`backend/.env` の `RAG_CHUNKING_STRATEGY`、文書・レシピの処理設定、KB の構築設定）は、読み込み時に `small_to_big` として扱う。子サイズ `chunk_child_size` / `RAG_CHUNK_CHILD_SIZE` は読まない。DB の移行は不要で、次に保存すると新しい値だけが残る（文書分割の設定を保存すると `.env` から `RAG_CHUNK_CHILD_SIZE` の行も消える）。
- 親子階層で作った配信中の chunk はそのまま検索対象に残り、自動では作り直さない。レシピ一覧の「再処理が必要」は設定の revision で判定するため、読み替えだけでは表示しない（文書単位の処理設定の保存 API と、その応答の差分（drift）は #488 で削除した）。親子階層（small-to-big）で作り直すには、その文書の Chunk を再作成する。
- 親子階層（small-to-big）は Docling の解析結果で親子に分割する。旧「親子階層」は Docling 以外の解析結果でも動いていたため、そうした文書の Chunk を作り直すと、失敗させずに構造認識で分割する（上の「使い方」2.。#300 より前は失敗していた）。親子で分割したい文書は、文書解析を Docling にして再解析する。
- 既存環境の更新手順：`backend/.env` の `RAG_CHUNKING_STRATEGY=hierarchical_parent_child` と `RAG_CHUNK_CHILD_SIZE` は、そのままでも起動する（前者は親子階層（small-to-big）として読み、後者は無視する）。Docling を使わない環境では、Chunk は構造認識で作られる。最初から構造認識として扱いたい場合は `RAG_CHUNKING_STRATEGY` を `structure_aware` へ変えておく。

## rag_poc との差分

- 解析結果、チャンク、回答はファイル（`.runs/`）ではなく、本リポジトリの Oracle と Object Storage を使う。
- ADB の独自スキーマ（`rag_chunk_runs` / `rag_chunk_embeddings` など）は使わない。検索は backend の hybrid 検索（vector と Oracle Text の RRF）に、rag_poc の「原質問を主軸にした重み付き融合」と Sudachi 分割を組み合わせる。
- chicago / osaka の 2 系統 LLM 設定と、OCI SDK の LLM 経路は廃止した。
- 移植していないもの：
  - Gradio UI、PPT 資料、`verify/` の問題セット（データは移さない。検証 CLI `app.rag.answer_verify_cli` で rag_poc の `cases.json` / `crag_goldset.json` をそのまま使える）
  - `evaluate_crag_grader.py`（rag_poc 独自の ADB の保存先から候補を組み立てる設計のため）と、`run_answer_eval.py` の Excel 出力・LLM 呼び出し回数の集計
  - フィードバックから FAQ・評価データセットへの自動昇格（管理者がフィードバック画面の詳細から 1 件ずつ「Approved FAQ に登録」「品質評価のケースに追加」する。変換と除外の規則は rag_poc の `approved_faq_import_row_from_answer_feedback` / `_expected_terms` を使う）

## 既知の制約

- 業務ビューの知識の同時編集は後勝ちになる。
