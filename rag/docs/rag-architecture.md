# RAG アーキテクチャ

このリポジトリは、data ingestion、chunking、indexing、hybrid retrieval、reranking、evaluation、observability、guardrails、deployment best practices をカバーする production-ready RAG reference implementation です。Backend は OCI Enterprise AI / OCI Generative AI / Oracle AI Database を直接使い、local / oci の実行モード切り替えは持ちません。

回答は回答フロー（`app/rag/answer_engine.py`・`packages/rag_engine`。#594）だけが行う。旧 standard の回答エンジン（検索モード・根拠確認・回答スタイル・高度な検索・Agent Memory）は #595 で削除した。Oracle Developer Day 2026 の AIDB RAG / Memory Engineering 手法と回答フローの対応は [AIDB Memory Engineering](./aidb-memory-engineering.md) を正とする。回答は `依頼 → 安全チェック → 検索範囲の確定 → 質問の理解と質問拡張 → hybrid 検索（RRF）→ Rerank → 根拠の評価と補正検索（CRAG）→ small-to-big → 回答の生成と監査 → 回答側の安全チェック` の順に扱い、単純な vector 類似 chunk 投入に戻さない。

## パイプライン

1. ドキュメントアップロード
   - API: `POST /api/documents/upload`
   - 原本は Object Storage 境界へ保存する。`PLATFORM_UPLOAD_STORAGE_BACKEND=local` では `local://...` として `PLATFORM_LOCAL_STORAGE_DIR` 配下に保存し、`PLATFORM_UPLOAD_STORAGE_BACKEND=oci` では Object Storage SDK で `oci://namespace/bucket/key` として保存する。
   - `RAG_MAX_UPLOAD_BYTES` と `RAG_ALLOWED_UPLOAD_CONTENT_TYPES` でサイズ・MIME type を制限する。
   - 原本 bytes から SHA-256 とサイズを計算し、`content_sha256` / `file_size_bytes` として文書行へ保存する。
   - 同一 `content_sha256` の既存文書がある場合は `duplicate_of_document_id` に最初の原本文書 ID を保存する。
   - 重複の文書は、自前の索引（active な chunk_set）が無い間（初回の取込を重複として省いたとき）だけ、ナレッジベースの範囲の検索で正本の chunk を使う。取込を進めて自前の索引を持つ重複は自分の chunk だけで検索し、正本（別のナレッジベースの同じ内容の文書）を範囲に入れない。旧版の除外は KB に属する重複の側の登録で判定し、旧版として登録した重複からは正本へ届かない（`include_superseded=true` のときは届く。#1381）。
   - upload レスポンスには `source_profile` を含める。`source_profile` は原本ファイル名、正規化後ファイル名、拡張子、保存 MIME type、拡張子から推定した MIME type、サイズ、SHA-256、重複元、原本 modality、推奨 parser profile、テキスト charset、品質警告を返す。
   - Dify Knowledge Pipeline / RAGFlow / R2R の「取込前にデータソース品質・処理方針・重複を明示する」ベストプラクティスは、外部 parser や別 storage を追加せず、この `source_profile` と既存の Oracle document metadata に再マップする。
   - アップロードは原本保存と KB 所属確定だけを行い、取込 job は作らない。後続処理は文書ごとに永続取込 job として明示投入する（取込開始方針の `ingestion_mode` は #306 で廃止。送っても無視する）。
   - 取込 job の一覧・取得（`GET /api/documents/ingestion-jobs` ほか）は、文書のファイル名を `document_file_name` として返す（#306）。

2. OCR・本文抽出と索引
   - API: `POST /api/documents/{document_id}/ingestion-jobs`（文書の既定レシピ）/ `POST /api/documents/{document_id}/recipes/{recipe_id}/ingestion-jobs`（レシピ単位）で取込 job を投入する。確認待ちの工程は `POST /api/documents/{document_id}/recipes/{recipe_id}/approve` で次の工程へ進める。
   - LLM/VLM は **OCI Enterprise AI** のみを使う。OCI Generative AI chat API は使わない。
   - Object Storage から取得した原本 bytes は、保存済み `file_size_bytes` / `content_sha256` と照合してから OCR へ渡す。
   - アップロード時の MIME type を VLM payload へ渡し、PDF / 画像 / text の real endpoint 解析条件を維持する。
   - サイズまたは SHA-256 が一致しない場合は `ERROR` にし、409 で拒否する。
   - VLM 出力は `StructuredExtraction` で Pydantic 検証してから保存する。`raw_text` に加えて `elements`（`title` / `text` / `list` / `table` / `figure` / `header` / `footer` 等）を持ち、page number、bbox、section path、confidence、parser metadata を保存できる。
   - `elements` が欠落した旧形式の抽出結果は `raw_text` から軽量推定し、`raw_text` が欠落した構造化結果は検索可能 element から本文を合成する。
   - Docling / Marker / Unstructured / RAGFlow DeepDoc の「ページ・読み順・表・章節を要素として残す」ベストプラクティスは、外部 parser 依存を追加せず OCI Enterprise AI の structured output schema と軽量な raw text element 推定に再実装する。
   - Enterprise AI gateway の request shape が標準 payload と異なる場合は、`PLATFORM_OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` で JSON object template を設定する。
   - `python -m app.rag.enterprise_ai_probe` で LLM/VLM endpoint の request preview と実 response parsing を Oracle / Object Storage から切り離して確認できる。probe 出力には raw prompt、context、OCR 本文、回答本文を含めず、payload shape と parse summary だけを残す。
   - `GET /api/documents/{document_id}/recipes/{recipe_id}/extraction-export?format=json|markdown|html|chunks` で、レシピの保存済み `StructuredExtraction` を JSON / Markdown / escaped HTML / 非 embedding chunk view として監査できる。文書の詳細の「抽出エクスポート」タブで Markdown / HTML / JSON をダウンロード・コピーでき、chunk の JSON は「Chunk / Citation」タブからダウンロードできる（`download=true` で添付として返す。内容の確認は「本文テキスト」「構造化要素」「Chunk / Citation」タブが担う。#561）。HTML は `tables[].cells` を safe `<table>` として再構成し、row / col / bbox lineage を保持する。Marker / Docling 的な多形式出力はここで本プロジェクト schema へ再マップし、再解析や外部 LLM / vector DB 呼び出しは行わない。
   - `UPLOADED` / `ERROR` を取込対象にし、`INGESTING` は二重実行防止で 409 にする。
   - `INDEXED` は force なしなら既存結果を返す。`force=true` は `INDEXED` の再取込に使える。
   - **方針(2 段階処理): parse → 人がプレビュー確認 → index。** parse/抽出の完了後はいったん `REVIEW`(プレビュー確認待ち)で停止し、`DocumentPreviewWorkspace` で抽出結果を人手で確認・承認(必要なら帳票項目を修正)してから後段の chunk/embed/index を実行する。抽出 artifact は再利用し、`INDEX` job は再 OCR せず保存済み `StructuredExtraction` から chunk / embedding / Oracle index を作る。
   - `INDEXED` は chunk、embedding、Oracle 保存、chunk_set、KB binding、extraction artifact が揃った文書だけに付与する。KB binding まで公開できない場合は `ERROR` とし、RAG 検索対象にしない。

3. チャンク分割
   - 実装: `backend/app/rag/chunking.py`
   - 取込では `chunk_extraction()` を使い、`StructuredExtraction.elements` を優先して `structure_v1` chunk を作る。`chunk_text()` は旧 raw text fallback と単体テスト用に残す。
   - Unstructured の `by_title` 風に章節境界を跨がず、RAGFlow / DeepDoc 風に表は他の本文と混ぜず独立 chunk にする。図・画像説明と図注は `content_kind=figure` として同一 chunk にまとめ、リストは連続性を保ち、通常本文だけ同一章節内で overlap を使う。
   - `header` / `footer` は繰り返しノイズとして主索引から除外する。表が長すぎる場合は行境界優先で分割する。
   - `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` で制御する。重複 chunk でも元章節・ページ・要素 metadata は維持する。
   - chunk metadata には `chunk_profile`、`chunk_group_id`、`chunk_group_kind`、`chunk_part_index`、`chunk_part_count`、`section_title`、`section_path`、`section_level`、`content_kind`、`page_start`、`page_end`、`element_kinds`、`element_ids`、`text_sha256`、`text_chars` を保存し、複雑文書 RAG で必要になる引用トレーサビリティと parent/child lineage を軽量に実現する。
   - `RAG_CHUNK_OVERLAP >= RAG_CHUNK_SIZE` は設定検証で拒否する。
   - 大きな文書でも chunk 総数では拒否せず、生成された全 chunk を embedding / indexing 対象にする。

4. 埋め込み
   - 実装: `backend/app/clients/oci_genai.py`
   - 本番は OCI Generative AI Inference の `embed_text` で Cohere Embed v4、1536 次元を使う。
   - query embedding は `SEARCH_QUERY`、文書 chunk embedding は `SEARCH_DOCUMENT` の input type を使う。
   - OCI embedding の返却件数と 1536 次元幅を検証し、不一致なら Oracle へ渡す前に fail fast する。

5. 索引
   - 実装: `backend/app/clients/oracle.py`
   - 本番は Oracle AI Vector Search。ベクトル列は `VECTOR(1536, FLOAT32)`。
   - スキーマ成果物は HNSW 索引(`COSINE`、目標精度 `95`、neighbors `32`、efconstruction `500`)を作成する。
   - ベクトル検索は `FETCH APPROX ... WITH TARGET ACCURACY` を使い、問い合わせ側の精度は `RAG_ORACLE_VECTOR_TARGET_ACCURACY` で調整する。
   - 検索精度は **Vector Index アダプター(`rag_vector_index_profile`)** で手動選択する。`app/rag/vector_index_adapter.py` が profile を解決し、`balanced`(`RAG_ORACLE_VECTOR_TARGET_ACCURACY` をそのまま使用)/ `accurate`(98、既定。#272)/ `fast`(85)で検索時 target accuracy を runtime 即時に切り替える([oracle.py](backend/app/clients/oracle.py) の vector fetch clause へ反映)。推奨 HNSW ビルドパラメータ(neighbors/efconstruction/distance)は `GET/PATCH /api/settings/vector-index` と専用設定画面に参考表示し、適用には索引再作成(`requires_reprovision`)が必要。版管理された schema DDL artifact は自動変更しない。profile は `SearchDiagnostics.config_fingerprint` に反映する（`SearchDiagnostics.vector_index_profile` は #595 で削除した）。
   - python-oracledb の共有 pool を遅延初期化し、document/chunk の永続化、集計、状態更新を Oracle table に対して実行する。
   - chunk 保存と vector search の入口でも embedding 幅を再検証する。
   - 検索対象の chunk は `INDEXED` の文書に限定する。
   - `OracleClient.count_document_chunks()` で document 単位の索引済み chunk 数を確認できる。
   - 文書が `INGESTING` / `ERROR` へ移る場合は、その文書の既存 chunk/index 行と古い抽出結果を削除して古い根拠や OCR 結果を残さない。
   - 外部ベクトル DB は使わない。

6. ハイブリッド検索
   - API: `POST /api/search`（回答）。検索は回答フローの中で行う（`AnswerEngine._search`）。`generate_answer=false` は質問の理解・拡張・検索・rerank までをチャットと同じ工程で行い、CRAG と回答の生成をしない（RAG 検索の画面の既定。画面の「LLM で回答を生成する」をオンにすると `true`。#649）。
   - Oracle AI Vector Search と Oracle Text（keyword）を Reciprocal Rank Fusion で統合する（`OracleClient.hybrid_search`）。RRF 定数は `RAG_RRF_K` で調整する。HyDE の仮説文書など、質問の拡張が vector だけで引く検索文は vector 検索にする。
   - 質問の拡張（`RAG_QUERY_STRATEGY`。既定は自動ルーティング。ほかに `simple_retrieval` / `rag_fusion` / `query_decomposition` / `step_back_prompting` / `hyde`）が作った複数の検索文は、原質問を主軸にした重み付き RRF で融合する（派生の検索文は合計で原質問 1 本分の重みに抑える）。質問の理解（`inquiry_conditions`）が名指しした文書名・ページ・業務（大分類）は検索条件と profile / business_match のチャネルに加える（`docs/rag-engine.md`）。
   - `SearchRequest` の `mode`・`strategy`・`rerank_top_n`・`generation_profile` は #595 で削除した。旧クライアントが送っても 422 にせず読み捨てる。
   - `filters` は `document_id`、`file_name`、`category_name`、`status` に加え、chunk metadata の `content_kind`、`section_title`、`section_path`、`source_acl`、`document_version` に対応し、retrieval 前に適用する。
   - `content_kind` は `text` / `list` / `table` / `figure` の完全一致、`section_title` / `section_path` は部分一致で使い、複雑文書の章節、表、図・画像説明だけに検索候補を絞れるようにする。RAG 検索の画面からは #649 で入力欄を外した（API の条件としては残す）。
   - 抽出項目の値の条件（`filters.extraction_fields`。#549）は、利用者が手で指定するほか、`RAG_AUTO_FIELD_FILTER_ENABLED` が有効な検索・回答プロファイルでは質問から LLM で読み取って足す（self-query。#652。読み取った条件で 0 件なら外して検索し直す。詳細は `docs/rag-engine.md`）。
   - `source_acl` と `document_version` は Oracle chunk metadata に対する完全一致 filter として使い、AIDB RAG の Business Context Pack で tenant / ACL / dataset / version を検索前に固定する。
   - keyword score は重複を除いた query token coverage として 0.0-1.0 に正規化する。
   - keyword（Oracle Text）の検索語の分割は 1 つだけで、設定では選ばない（#588。`rag_engine` の `retrieval/text_search_tokenizer.py`）。Sudachi の長い単位（C）と短い単位（A）を主の語にし、文字種の区切り（漢字・カタカナ・英数字の連続）・漢字の複合語の先頭 2 字と末尾 2 字・送り仮名を除いた形（「取り消し」→「取消」）を補う語として足す。検索・回答プロファイルのドメインキーワードは 1 語として優先する。語は `ACCUM` で結び、重みはキーワード 2・主の語 1・補う語と 1 字の語 0.5（多くの語に当たる chunk ほど上にし、補った語だけの一致は軽くする）。語は最大 24 語、query は最大 3,800 文字。Sudachi の辞書が使えない環境では、自動で文字種の区切りだけにする（文字種の連続を主の語にする）。索引の側の lexer（`RAG_TEXT_WORLD_LEXER` = `WORLD_LEXER`）は変えない。回答の検索とフィードバックの検索は同じ分割を使う。
   - 索引する文字列（`rag_chunks.search_text`・`rag_feedback_details.search_text`）は、保存の時点で質問の語と同じ文字の形（NFKC と波線・ダッシュの同一視。`normalize_text_search_index_text`）にする（#1336）。`WORLD_LEXER` は全角 / 半角の違い（`Ｅ１０２３` と `E1023`、半角のカタカナ）は同一視するが、互換文字（`①` `Ⅴ` `㈱` `㌔` `℃`）は同一視しない。質問の側は NFKC でこれらを `1` `V` `(株)` `キロ` `°C` にするため、索引の側も同じ形にしないと `手順①` などが当たらない（実 ADB で確認。`tests/test_text_search_tokenizer_oracle.py`）。改行と大文字小文字は残す（語の区切りと大文字小文字の無視は lexer が行う）。表示の本文（`chunk_text`）・親の本文・引用の原文・embedding の入力は正規化しない。
   - vector / keyword / hybrid の同点は document id、chunk index、chunk id で安定順にし、評価の再現性を保つ。
   - citation metadata には章節 metadata に加えて `retrieval_mode`、vector/keyword の rank/score、`rrf_k`、RRF score を含め、hybrid 召回の由来を query 本文なしで追跡できるようにする。

7. 構造化・関係検索境界
   - RAG repo では SQL 生成 endpoint を公開しない。SQL 専用の自然言語問い合わせは 同じ monorepo の `nl2sql/` の責務とする。
   - 集計・関係・横断要約のような query も、回答フローの hybrid retrieval の範囲で扱う（GraphRAG-lite を回答の検索で使う経路は #595 で削除した。構築と KB のグラフ表示は残す）。
   - GraphRAG/navigation/metadata layer が未 materialize の場合は diagnostics と KB 詳細で `planned_only` / `needs_reingest` を表示し、構築済みと見せない。

8. リランク
   - 本番は OCI Generative AI Cohere Rerank v4 fast。回答フローは融合した検索候補を rerank で並べ替える（`RAG_RERANK_ENABLED`。既定は有効）。
   - OCI rerank の返却 index は候補範囲内・重複なし、返却件数は `top_n` 以内、score は finite number であることを検証し、不正な rerank 結果は fail fast する。
   - 根拠の child と同じ `chunk_group_id` の兄弟 chunk（上限 `RAG_CONTEXT_GROUP_MAX_CHUNKS`）と親本文（`parent_text`）で親子を復元し（small-to-big）、根拠の child の前後の child（`RAG_NEIGHBOR_CHILD_COUNT`、既定 3）を文脈に足す。
   - 上位の候補の本文が参照する節（「第3章を参照」など。取込で解決して chunk の metadata に保存）の chunk を、上限付き（`RAG_REFERENCE_EXPANSION_MAX_CHUNKS`、既定 4）で参照元の直後に rerank の候補として足す（#1280。[rag-engine.md の設定一覧](./rag-engine.md#設定一覧)）。
   - 実体の層（#1362）: 文書レシピで実体の抽出を選んだ文書では、質問と上位の候補の実体（システム・部署の略号など）から、Oracle の実体の表（`rag_entities` / `rag_entity_aliases` / `rag_entity_chunks`）との SQL の join で、台帳の行（名寄せ）とその属性の実体を定義する chunk（1 段）を上限付き（既定 6）で足す。拡張の開閉（既定 off）と上限は検索・回答プロファイルが持ち（3 層: 文書レシピ = 実体の索引を作るか、KB = 対象の文書、プロファイル = 検索と回答の振る舞い。全体の環境変数は持たない。#1388）、実体の索引は文書レシピの「実体の索引」で選ぶ。外部のグラフ DB・LLM の抽出は使わない（[rag-engine.md の設定一覧](./rag-engine.md#設定一覧)）。
   - 旧 standard の検索後処理（本文 hash の重複除去・MMR の多様化・近傍 / 依存 chunk の追加・圧縮と、`RAG_CONTEXT_WINDOW_CHARS` などの設定）は #595 で削除した。

9. AIDB Memory Engineering
   - 検索 request ごとに Business Context Pack（検索範囲）を作る。tenant/user/role は raw 値を保存せず hash だけを持ち、document/category/knowledge base scope、`source_acl`、`document_version` を検索条件に固定する。
   - 回答フローの根拠確認（CRAG）は、検索結果の根拠を評価し、足りなければ質問を補正して再検索する（`RAG_ANSWER_FLOW=crag`。`standard_rag` は補正検索をしない）。回答文の生成後にも根拠との整合を確かめ（監査）、根拠が足りないときは理由（`insufficient_reason`）と人手確認の要否を付ける。
   - 関係情報の構築は `rag_graph_profile`(`app/rag/graph_adapter.py`)で選ぶ。`off`(既定。構築しない)/ `entities`(構築する)の 2 つで、`entities` は文書全体と章・節の見出し(表・図を含む)を「含む」でつないだ entity / relationship を LLM を使わずに作る(`app/rag/graph_index.py`)。claims / community summary まで作る `full` と legacy の `RAG_GRAPH_ENABLED` は、読む経路が無かったため #621 で削除した(表 `rag_graph_claims`・`rag_graph_community_summaries` は migration で削除)。ingest の graph gate は `resolve_graph_adapter(...).enabled`。構築判定・文書の実効設定の表示・文書レシピの上書きの正本は `rag_graph_profile` 1 つ。graph の行の `knowledge_base_id` は取込時の所属のスナップショットなので、KB のグラフ表示は今もその KB に所属している文書の行だけを返す。回答の検索は graph を使わない（検索時の graph の検索は #595 で削除した）。設定 API `GET/PATCH /api/settings/graph` と専用設定画面で切替する。変更は次回以降の取込に適用され、既存文書への反映には再取込が必要。外部グラフ DB は導入しない。
   - Memory Router / Retrieval Plan・Resolver / Verifier・Context Builder・Agent Memory（`rag_agent_memories` への検索と writeback）は #595 で削除し、テーブルは #596 で削除した。監査の列は既存の監査の行を変えないため残す。

10. 回答生成
   - LLM は **OCI Enterprise AI**。回答フローが、small-to-big で復元した根拠の文脈を回答生成のプロンプト（検索・回答設定 > 回答プロンプト。`rag_answer_prompts`）で渡して回答を生成する。追加の LLM provider は導入しない。
   - チャットでは、会話履歴から質問を単独で意味が通る形に書き換えてから回答する（`RAG_HISTORY_REWRITE_ENABLED`）。書き換えた質問も安全チェックを通す。
   - Enterprise AI gateway の request shape が標準 payload と異なる場合は、`PLATFORM_OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` で JSON object template を設定する。
   - LLM 契約は `python -m app.rag.enterprise_ai_probe --surface llm` で個別に検証できる。回答本文は probe artifact に保存せず、parse 成功と文字数だけを確認する。
   - 生成後に回答側の安全チェックで secret leakage をブロックし、回答と根拠の文脈の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返す。
   - `/api/search`・`/api/search/stream`・チャットの送信は、回答を LLM で生成するため `RAG_ANSWER_TIMEOUT_SECONDS`（既定 300 秒）で pipeline 実行時間を制限する（#375。品質評価の 1 ケースも同じ上限で打ち切る。#383）。通常検索は timeout 時に 504 を返す。SSE は stream 開始後に timeout した場合、HTTP status は維持して `error` event（`message` に時間切れになった工程と再試行の案内、`stage` に工程名）を返し、どちらも `rag_search_audit.error_stage=timeout` を残す。
   - 進捗の stage は `history_rewrite`（会話履歴による質問の書き換え）・`answer`（回答フロー）と、その中の入れ子の工程 `answer_step:<工程名>`（「質問の理解」「文書検索（1回目）」など）、検索だけの経路の `retrieval` を通知する（#375 / #593）。画面（RAG 検索・チャット）は今の工程と経過時間を出す。
   - stage は `rag_search_stage_duration_seconds` で stage 別 latency を記録する。回答フローの工程ごとの時間は `diagnostics.answer.execution_steps` に残る（負荷試験 CLI `search_load_cli` はこれで工程の p95 を集計する）。
   - レスポンスには `trace_id`、`citations`、`guardrail_warnings`、`diagnostics`、`elapsed_ms` を含める。MCP の `rag_search` は `citations` を根拠の `evidence[]`（場所・版・回答に使ったか・切り詰めの有無）に写し、本文の続きは `rag_read_source` で読む（#1219。契約は `platform/contracts/mcp/rag-tools.json`）。
   - MCP の根拠（`rag_search` と `rag_retrieve_evidence` の `evidence[]`）は、`evidence_limit` で切る前に、回答に使った根拠（`used_in_answer`。回答に使った順）→ 検索で当たった chunk（`role=retrieved_anchor`。関連度＝rerank の後の順の `evidence_retrieval_rank`）→ 前後の文脈（`neighbor_context` / `same_page_context` / `parent_context` など。`citations` の順）に並べ直す（#1348。`app/mcp/tools.py` の `mcp_evidence_order`）。`citations` は文書ごとのかたまりの中を文書の順に並べるため、そのまま切ると上位の文書の前置き・前の章で枠が埋まり、当たった chunk が落ちる。画面の検索結果・回答の文脈の並びは変えない。切った件数は `evidence_omitted`。既定の `evidence_limit` は `rag_search` が 12、回答を作らない `rag_retrieve_evidence` が検索する件数（`top_k` の既定）と同じ 20 で、`evidence_limit` を省略して `top_k` を広げたときは `top_k` 件（上限 50）まで返し、検索で当たった chunk を既定で全部返す（#1365。#1335 の再評価で、台帳の行が当たった chunk の 13〜20 位にあり 12 で切れていた）。
   - 根拠の場所（`locator`）には、根拠の先頭の要素の定位子 `element_locator`（`doc:{document_id}/ext:{解析の結果の ID}/page:{頁}/el:{要素の ID}`。`app/rag/element_locator.py`）を入れる（#1330）。解析の結果の ID（`rag_chunk_sets.extraction_recipe_id`）は、文書分割の設定だけを変えて chunk を作り直しても変わらないため、`rag_read_source` に `locator` を渡すと、同じ要素を含む今の chunk を読める（chunk の ID は作り直すたびに変わる）。解析をやり直した後の古い定位子は `source_stale`。要素の ID を持たない分割（文字数・区切り文字・Markdown の見出し）の根拠は `null`。
   - 文書を順に読む MCP のツール（#1332）: `rag_outline` は文書の節の構成（節の見出し・頁・chunk の数・文字数と、その節から読む `cursor`）を、`rag_read_document` は頁（`page`）・節（`section`）・根拠の定位子（`locator`）・続き（`cursor`）の位置から本文を chunk の順に `max_chars`（既定 8000・上限 20000）まで返す。頁が変わる所に `--- p.N ---` の行を入れ、返した本文に含まれる chunk（`chunk_id`・定位子・本文の中の位置）を添え、続きは `next_cursor`（最後まで読んだら `null`）。見え方の条件は検索と同じ。複数レシピの文書は、有効な chunk_set のうちレシピの番号が最も小さいものを読む（同じ本文を別の分け方で持つだけのため）。`cursor` は chunk_set に縛り、作り直された後は `source_stale`。多段の質問は RAG の中で多段に検索せず、Agent がこれらのツールで読み進める（#595 の方針。評価は `evaluation/multi-hop/`、#1335）。
   - `POST /api/search/stream` は SSE で `stage`、`metadata`、`delta`、`citations`、`done` を返す。`stage` event は工程ごとの `started` / `success` / `error` と低機密 attributes を表す。回答 token は完全生成、PII マスク、groundedness、回答検査の後にだけ `delta` 分割する。

## Agent からの呼び出し（MCP）

Agent（Production Control Plane）は RAG を `POST /api/mcp`（MCP の Streamable HTTP、JSON 応答。#232）で呼ぶ。製品同士はコードで依存しない。

- 入口と認証: `Authorization: Bearer <サービストークン>`（`aud=rag`、`sub`=Run の利用者の `user_uuid`、署名鍵は共通 `.env` の `PLATFORM_SERVICE_TOKEN_SECRET`）。Cookie / CSRF は使わない。route manifest では `/mcp` を「認証済みなら通す」とし、権限はツールごとに判定する。
- 利用者: token の利用者の現在のロール・権限・検索・回答プロファイル / ナレッジベースの対象範囲を画面と同じ判定で使う。回答履歴・rate limit も同じ利用者。token の `agent_id` / `run_id` は hash して監査 context の agent / thread に入れる。
- 業務 Agent のデータの範囲（#1379）: token に任意の claim `profile_ids`（業務 Agent の定義の、使える検索・回答プロファイルの ID）があれば、すべてのツールを「利用者の権限 ∩ 範囲」で判定する（`app/mcp/profile_scope.py`。claim の無い token は今までどおり）。
  - ツールは、監査 context の検索・回答プロファイルを「利用者の範囲 ∩ claim」、ナレッジベースを「利用者の範囲 ∩ 範囲のプロファイルの参照先」に絞った中で実行する。一覧（`rag_list_search_answer_profiles`）は範囲で絞られ、文書を読むツール（`rag_read_source` / `rag_outline` / `rag_read_document`）と `rag_validate_answer` の根拠の読み直しは、検索と同じ見え方の条件（文書の KB の所属）なので範囲の外の文書を「見つからない」（`source_not_found`）にする。範囲のプロファイルを読めないときは 503（範囲なしとして通さない）。
  - `rag_search` / `rag_retrieve_evidence` は `search_answer_profile_id` が要る（`knowledge_base_ids` だけの検索は 403）。範囲の外の `search_answer_profile_id`（`rag_lookup_guides`・`rag_validate_answer` の `guide` も）と、指定したプロファイルの参照先の外の `knowledge_base_ids` は 403（`structuredContent.error_code` が `PROFILE_SCOPE_FORBIDDEN`）。claim にあっても利用者が使えないプロファイルは今までどおり 404。
- ツール: `rag_list_search_answer_profiles`（検索・回答プロファイル一覧と同じ権限）、`rag_search`（`menu.search`。`POST /api/search` と同じ処理）。入出力は [backend/README.md](../backend/README.md) の「MCP」を参照。
- チャットは MCP で提供しない（#787）。チャットは画面（SSE の `POST /api/chat/conversations/{id}/messages/stream`）だけの機能で、MCP で提供するのは検索だけ。
- チャットの回答の作成は SSE の接続から切り離している（#1175。`app/rag/chat_answer_runs.py`）。送信を受けたプロセスの中の task が作成し、作成中の回答（`STREAMING`）・段階・最終の回答は `rag_messages` に保存する。SSE はその task の event を購読するだけで、接続が切れても作成は続く。画面は `GET /api/chat/conversations/{id}/messages/{質問の id}/stream`（`Last-Event-ID`）で続きを購読し直し、できなければ保存済みの会話を取り直す。停止は `POST .../messages/{質問の id}/cancel` の明示の取消。
- チャットの処理の段階（質問の整理 → 文書の検索 → 並べ替え → 回答の作成 → 回答の確認）は、3 製品共通の段階のイベント（`pr_backend_core.chat_progress`。#1359）で記録する（`app/rag/chat_progress.py` の `ChatProgressTracker`。対象は作成中の回答のメッセージ）。最初に 5 段階を待機中で出し、状態が変わった段階だけを番号（`seq`）つきで記録して、完了・失敗・停止・中断の終端まで `progress_json` に保存する。名前（文言）は記録に入れず、補足は値（補正検索の回数 `attempt`・根拠の件数 `citations`）にして画面が i18n で付ける（`frontend/src/lib/chat-progress.ts`）。配信は 3 つ: 回答の配信の SSE の `chat_progress`（作成中の今のターン）、保存済みの回答の `progress`（会話の取得）、段階の polling（`GET .../messages/{回答の id}/progress?since=`）と SSE（`.../progress/stream`。保存済みの記録を 1 秒ごとに読み直すので、別の worker が作成している回答・再読込の後でも段階が届く）。画面は `useChatProgressStream` で受け取る。

## Oracle AI Database DDL 例

`app.clients.oracle` の `oracle_document_schema_sql()` / `oracle_vector_schema_sql()` / `oracle_search_audit_schema_sql()` / `oracle_ingestion_audit_schema_sql()` が返す DDL をベースにする。

```sql
CREATE TABLE rag_documents (
    document_id              VARCHAR2(64) PRIMARY KEY,
    file_name                VARCHAR2(512) NOT NULL,
    status                   VARCHAR2(32) NOT NULL,
    tenant_id_hash           CHAR(64),
    category_name            VARCHAR2(256),
    object_storage_path      VARCHAR2(1024),
    content_type             VARCHAR2(255),
    file_size_bytes          NUMBER(19),
    content_sha256           CHAR(64),
    duplicate_of_document_id VARCHAR2(64),
    extraction               JSON,
    error_message            VARCHAR2(2000),
    uploaded_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    indexed_at               TIMESTAMP WITH TIME ZONE
);

CREATE INDEX rag_documents_content_sha256_idx
    ON rag_documents (content_sha256);

CREATE INDEX rag_documents_tenant_status_uploaded_idx
    ON rag_documents (tenant_id_hash, status, uploaded_at DESC);

CREATE TABLE rag_chunks (
    chunk_id        VARCHAR2(128) PRIMARY KEY,
    document_id     VARCHAR2(64) NOT NULL,
    tenant_id_hash  CHAR(64),
    chunk_index     NUMBER NOT NULL,
    chunk_text      CLOB NOT NULL,
    metadata_json   JSON,
    embedding       VECTOR(1536, FLOAT32),
    created_at      TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx
    ON rag_chunks (embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 95
    PARAMETERS (
        TYPE HNSW,
        NEIGHBORS 32,
        EFCONSTRUCTION 500
    );

CREATE INDEX rag_chunks_text_idx
    ON rag_chunks (chunk_text)
    INDEXTYPE IS CTXSYS.CONTEXT;

CREATE INDEX rag_chunks_tenant_document_idx
    ON rag_chunks (tenant_id_hash, document_id, chunk_index);

CREATE TABLE rag_search_audit (
    audit_id              VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type            VARCHAR2(32) DEFAULT 'rag.search' NOT NULL,
    trace_id              VARCHAR2(64) NOT NULL,
    request_id            VARCHAR2(128),
    tenant_id_hash        CHAR(64),
    user_id_hash          CHAR(64),
    outcome               VARCHAR2(32) NOT NULL,
    search_mode           VARCHAR2(16) NOT NULL,
    query_hash            CHAR(64) NOT NULL,
    query_chars           NUMBER(10) NOT NULL,
    filter_keys           JSON,
    memory_plan_id        VARCHAR2(32),
    top_k                 NUMBER(10),
    rerank_top_n          NUMBER(10),
    query_variant_count   NUMBER(10) DEFAULT 1 NOT NULL,
    guardrail_codes       JSON,
    guardrail_severities  JSON,
    retrieved_count       NUMBER(10) DEFAULT 0 NOT NULL,
    reranked_count        NUMBER(10) DEFAULT 0 NOT NULL,
    deduplicated_count    NUMBER(10) DEFAULT 0 NOT NULL,
    context_diversified_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_group_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_expanded_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compressed_count NUMBER(10) DEFAULT 0 NOT NULL,
    context_compression_saved_chars NUMBER(10) DEFAULT 0 NOT NULL,
    -- memory_plan_id・top_k・rerank_top_n・query_variant_count と、reranked_count から
    -- context_window_chars までの内訳の列は、#595 以降は既定値を書く（既存の行を変えないため残す。#596）。
    agent_memory_retrieved_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_count NUMBER(10) DEFAULT 0 NOT NULL,
    agent_memory_writeback_status VARCHAR2(32) DEFAULT 'skipped' NOT NULL,
    evidence_count        NUMBER(10) DEFAULT 0 NOT NULL,
    support_count         NUMBER(10) DEFAULT 0 NOT NULL,
    structure_count       NUMBER(10) DEFAULT 0 NOT NULL,
    history_count         NUMBER(10) DEFAULT 0 NOT NULL,
    resolver_rejected_count NUMBER(10) DEFAULT 0 NOT NULL,
    insufficient_context_count NUMBER(10) DEFAULT 0 NOT NULL,
    citation_count        NUMBER(10) DEFAULT 0 NOT NULL,
    context_chars         NUMBER(10) DEFAULT 0 NOT NULL,
    context_window_chars  NUMBER(10),
    document_ids          JSON,
    config_fingerprint    CHAR(64),
    elapsed_ms            NUMBER(12, 3) NOT NULL,
    error_stage           VARCHAR2(64),
    error_type            VARCHAR2(128),
    created_at            TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE TABLE rag_ingestion_audit (
    audit_id               VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type             VARCHAR2(32) DEFAULT 'rag.ingestion' NOT NULL,
    trace_id               VARCHAR2(64) NOT NULL,
    request_id             VARCHAR2(128),
    tenant_id_hash         CHAR(64),
    user_id_hash           CHAR(64),
    document_id            VARCHAR2(64) NOT NULL,
    outcome                VARCHAR2(32) NOT NULL,
    source_sha256          CHAR(64) NOT NULL,
    source_bytes           NUMBER(19) NOT NULL,
    document_type          VARCHAR2(128),
    extraction_confidence  NUMBER(6, 5),
    chunk_count            NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count           NUMBER(10) DEFAULT 0 NOT NULL,
    elapsed_ms             NUMBER(12, 3) NOT NULL,
    error_type             VARCHAR2(128),
    error_message          VARCHAR2(2000),
    created_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
```

document / chunk table には `tenant_id_hash` を持たせる。production（`RAG_AUTH_MODE=production`）では client の `X-Tenant-ID` を使わず、tenant なし（単一 tenant）で動かす。利用者と検索・回答プロファイル / ナレッジベースの対象範囲はログイン中の利用者（MCP ではサービストークンの利用者）から決め、client の `X-RAG-Allowed-*` header も使わない（#214 / #225）。local（`RAG_AUTH_MODE=local`）だけは開発・検証のため、HTTP header `X-Tenant-ID` がある場合に raw tenant id を保存せず hash 化し、一覧・詳細・重複判定・retrieval を同一 tenant に閉じる（header がない場合は全体を参照できる）。同じく local では `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与すると、document id / category name scope を request context に保持し、document 一覧、詳細、chunk count、Oracle AI Vector Search、Oracle Text keyword search の SQL predicate に適用する。scope header が存在するが有効値がない場合は deny-all とする。

監査 table は query 本文、OCR 原文、tenant/user id の raw 値を保存しない。検索は `query_hash` と `query_chars`、filter key、安全チェックの code、検索件数・citation 件数、引用した文書 ID、RAG 設定 fingerprint を保存する（旧 standard の検索・context の内訳の列は #595 以降は既定値。既存の監査の行を変えないため列は残す。#596）。回答フローの工程と根拠は回答の記録（`rag_answer_records`）に残す。tenant/user id は `tenant_id_hash` / `user_id_hash` として保存する。取込は `source_sha256` と `source_bytes` を保存し、trace id / request id でアプリログ・Langfuse・Prometheus と相関する。

## Trace export

`record_trace_span()` は構造化ログへ `rag_trace_span` を出し、`RAG_TRACE_EXPORT_HTTP_ENDPOINT` が設定されている場合は同じ脱機密化済み event を非同期 HTTP JSON で OpenTelemetry / Langfuse gateway へ送信する。export 対象は `trace_id`、stage 名、outcome、duration、低 cardinality attributes、`error_type` のみで、query 本文、context 本文、OCR 原文、prompt、例外 message は含めない。export queue が満杯または送信失敗しても RAG pipeline は継続し、失敗は `app.trace` logger の `rag_trace_export_*` イベントで確認する。
