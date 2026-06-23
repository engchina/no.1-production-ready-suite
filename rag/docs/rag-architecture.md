# RAG アーキテクチャ

このリポジトリは、data ingestion、chunking、indexing、hybrid retrieval、reranking、evaluation、observability、guardrails、deployment best practices をカバーする production-ready RAG reference implementation です。Backend は OCI Enterprise AI / OCI Generative AI / Oracle 26ai を直接使い、local / oci の実行モード切り替えは持ちません。

Oracle Developer Day 2026 の AIDB RAG / Memory Engineering 手法は [AIDB Memory Engineering](./aidb-memory-engineering.md) を正とする。検索 runtime は `依頼 → Business Context Pack → Retrieval Plan → AIDB Retrieval → Resolver / Verifier → Context Builder → Agent Memory Loop` の順に扱い、単純な vector 類似 chunk 投入に戻さない。

## パイプライン

1. ドキュメントアップロード
   - API: `POST /api/documents/upload`
   - 原本は Object Storage 境界へ保存する。`UPLOAD_STORAGE_BACKEND=local` では `local://...` として `LOCAL_STORAGE_DIR` 配下に保存し、`UPLOAD_STORAGE_BACKEND=oci` では Object Storage SDK で `oci://namespace/bucket/key` として保存する。
   - `MAX_UPLOAD_BYTES` と `ALLOWED_UPLOAD_CONTENT_TYPES` でサイズ・MIME type を制限する。
   - 原本 bytes から SHA-256 とサイズを計算し、`content_sha256` / `file_size_bytes` として文書行へ保存する。
   - 同一 `content_sha256` の既存文書がある場合は `duplicate_of_document_id` に最初の原本文書 ID を保存する。
   - upload レスポンスには `source_profile` を含める。`source_profile` は原本ファイル名、正規化後ファイル名、拡張子、保存 MIME type、拡張子から推定した MIME type、サイズ、SHA-256、重複元、原本 modality、推奨 parser profile、テキスト charset、品質警告を返す。
   - Dify Knowledge Pipeline / RAGFlow / R2R の「取込前にデータソース品質・処理方針・重複を明示する」ベストプラクティスは、外部 parser や別 storage を追加せず、この `source_profile` と既存の Oracle document metadata に再マップする。
   - `ingestion_mode=manual` を multipart form で受け付ける。アップロードは原本保存と KB 所属確定だけを行い、後続処理は永続取込 job として明示投入する。

2. OCR・本文抽出と索引
   - API: `POST /api/documents/{document_id}/ingest`
   - LLM/VLM は **OCI Enterprise AI** のみを使う。OCI Generative AI chat API は使わない。
   - Object Storage から取得した原本 bytes は、保存済み `file_size_bytes` / `content_sha256` と照合してから OCR へ渡す。
   - アップロード時の MIME type を VLM payload へ渡し、PDF / 画像 / text の real endpoint 解析条件を維持する。
   - サイズまたは SHA-256 が一致しない場合は `ERROR` にし、409 で拒否する。
   - VLM 出力は `StructuredExtraction` で Pydantic 検証してから保存する。`raw_text` に加えて `elements`（`title` / `text` / `list` / `table` / `figure` / `header` / `footer` 等）を持ち、page number、bbox、section path、confidence、parser metadata を保存できる。
   - `elements` が欠落した旧形式の抽出結果は `raw_text` から軽量推定し、`raw_text` が欠落した構造化結果は検索可能 element から本文を合成する。
   - Docling / Marker / Unstructured / RAGFlow DeepDoc の「ページ・読み順・表・章節を要素として残す」ベストプラクティスは、外部 parser 依存を追加せず OCI Enterprise AI の structured output schema と軽量な raw text element 推定に再実装する。
   - Enterprise AI gateway の request shape が標準 payload と異なる場合は、`OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` で JSON object template を設定する。
   - `python -m app.rag.enterprise_ai_probe` で LLM/VLM endpoint の request preview と実 response parsing を Oracle / Object Storage から切り離して確認できる。probe 出力には raw prompt、context、OCR 本文、回答本文を含めず、payload shape と parse summary だけを残す。
   - `GET /api/documents/{document_id}/extraction-export?format=json|markdown|html|chunks` で保存済み `StructuredExtraction` を JSON / Markdown / escaped HTML / 非 embedding chunk view として監査できる。DocumentPreviewWorkspace では抽出本文 panel 内の「抽出エクスポート」で同じ 4 形式を切り替えて確認できる。HTML は `tables[].cells` を safe `<table>` として再構成し、row / col / bbox lineage を保持する。Marker / Docling 的な多形式出力はここで本プロジェクト schema へ再マップし、再解析や外部 LLM / vector DB 呼び出しは行わない。
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
   - 本番は Oracle 26ai AI Vector Search。ベクトル列は `VECTOR(1536, FLOAT32)`。
   - スキーマ成果物は HNSW 索引(`COSINE`、目標精度 `95`、neighbors `32`、efconstruction `500`)を作成する。
   - ベクトル検索は `FETCH APPROX ... WITH TARGET ACCURACY` を使い、問い合わせ側の精度は `ORACLE_VECTOR_TARGET_ACCURACY` で調整する。
   - 検索精度は **Vector Index アダプター(`rag_vector_index_profile`)** で手動選択する。`app/rag/vector_index_adapter.py` が profile を解決し、`balanced`(既定・`ORACLE_VECTOR_TARGET_ACCURACY` をそのまま使用)/ `accurate`(98)/ `fast`(85)で検索時 target accuracy を runtime 即時に切り替える([oracle.py](backend/app/clients/oracle.py) の vector fetch clause へ反映)。推奨 HNSW ビルドパラメータ(neighbors/efconstruction/distance)は `GET/PATCH /api/settings/vector-index` と専用設定画面に参考表示し、適用には索引再作成(`requires_reprovision`)が必要。版管理された schema DDL artifact は自動変更しない。`SearchDiagnostics.vector_index_profile` に残す。
   - python-oracledb の共有 pool を遅延初期化し、document/chunk の永続化、集計、状態更新を Oracle table に対して実行する。
   - chunk 保存と vector search の入口でも embedding 幅を再検証する。
   - 検索対象の chunk は `INDEXED` の文書に限定する。
   - `OracleClient.count_document_chunks()` で document 単位の索引済み chunk 数を確認できる。
   - 文書が `INGESTING` / `ERROR` へ移る場合は、その文書の既存 chunk/index 行と古い抽出結果を削除して古い根拠や OCR 結果を残さない。
   - 外部ベクトル DB は使わない。

6. ハイブリッド検索
   - API: `POST /api/search`
   - `mode=hybrid|vector|keyword` を指定できる。hybrid は vector と keyword を Reciprocal Rank Fusion で統合し、RRF 定数は `RAG_RRF_K` で調整する。
   - retrieval 前に deterministic な query expansion を行い、請求書/invoice、保管/storage、図/figure などの業務同義語を最大 `RAG_QUERY_EXPANSION_MAX_VARIANTS` 件の query variant として検索する。元 query は rerank / LLM 生成に維持し、audit / trace には query 本文や展開語ではなく `query_variant_count` だけを残す。
   - 複数 query variant の検索結果は chunk id 単位で RRF 融合し、citation metadata には `query_fusion_score`、`query_variant_count`、`matched_query_variant_count` を低機密 metadata として付与する。
   - 検索前のクエリ計画は **Agentic アダプター(`rag_agentic_profile`)** で手動選択する。`app/rag/agentic_adapter.py` が profile を解決し、`off`(既定・LLM 計画なし=現行挙動)/ `query_rewrite`(検索向けに 1 回書き換え)/ `decompose`(最大 `RAG_AGENTIC_MAX_SUBQUERIES` 個の sub-question へ分解)/ `multi_hop`(decompose + 根拠が弱い時に top context で 1 回追加分解、上限 1 hop・corrective retrieval と排他)を束ねる。計画は `OciEnterpriseAiClient.plan_query`(OCI Enterprise AI、JSON 配列受領・失敗時は元 query へ安全 fallback)で行い、結果を上記マルチクエリ RRF 融合経路へ追加 variant として注入する。`off` 以外は検索ごとに追加 LLM 呼び出しが発生するため明示 opt-in。`SearchDiagnostics` に `agentic_profile` / `agentic_subquery_count` / `agentic_hops` を残し、query 本文は audit / trace に出さない。設定 API `GET/PATCH /api/settings/agentic` と専用設定画面で切替する。外部 LLM provider は導入しない。
   - `filters` は `document_id`、`file_name`、`category_name`、`status` に加え、chunk metadata の `content_kind`、`section_title`、`section_path`、`source_acl`、`document_version` に対応し、retrieval 前に適用する。
   - `content_kind` は `text` / `list` / `table` / `figure` の完全一致、`section_title` / `section_path` は部分一致で使い、複雑文書の章節、表、図・画像説明だけに検索候補を絞れるようにする。
   - `source_acl` と `document_version` は Oracle chunk metadata に対する完全一致 filter として使い、AIDB RAG の Business Context Pack で tenant / ACL / dataset / version を検索前に固定する。
   - keyword score は重複を除いた query token coverage として 0.0-1.0 に正規化する。
   - vector / keyword / hybrid の同点は document id、chunk index、chunk id で安定順にし、評価の再現性を保つ。
   - citation metadata には章節 metadata に加えて `retrieval_mode`、vector/keyword の rank/score、`rrf_k`、RRF score を含め、hybrid 召回の由来を query 本文なしで追跡できるようにする。

7. 構造化・関係検索境界
   - RAG repo では SQL 生成 endpoint を公開しない。SQL 専用の自然言語問い合わせは sibling repo `../no.1-production-ready-nl2sql` の責務とする。
   - 集計・関係・横断要約のような query は、GraphRAG-lite と Oracle 26ai hybrid retrieval の範囲で扱う。
   - GraphRAG/navigation/metadata layer が未 materialize の場合は diagnostics と KB 詳細で `planned_only` / `needs_reingest` を表示し、構築済みと見せない。

8. リランク
   - 本番は OCI Generative AI Cohere Rerank v4 fast。
   - `rerank_top_n` は `top_k` 以下に制限し、retrieval 候補数を超える無意味な rerank 指定を拒否する。
   - OCI rerank の返却 index は候補範囲内・重複なし、返却件数は `top_n` 以内、score は finite number であることを検証し、不正な rerank 結果は fail fast する。
   - rerank 後、context へ入れる前に `text_sha256` または正規化本文 hash で同一本文 chunk を除外し、重複根拠が context window を消費しないようにする。去重件数は diagnostics / audit の `deduplicated_count` にだけ残し、本文はログへ出さない。
   - `RAG_CONTEXT_DIVERSITY_LAMBDA` が 1.0 未満の場合は、rerank anchor を MMR 風に重排し、同質 chunk だけが先に context window を消費しないようにする。既定は 1.0 で無効。重排件数は `context_diversified_count`、順位が変わった citation は `context_diversified` / `context_original_rank` / `context_diversified_rank` に残す。
   - `RAG_CONTEXT_GROUP_EXPANSION_ENABLED=true` の場合は、rerank anchor の `chunk_group_id` と同じ sibling chunk を Oracle から取得し、分割された表・箇条書き・章節の前後文脈を生成 context へ低優先で追加する。既定は無効。anchor ごとの追加上限は `RAG_CONTEXT_GROUP_MAX_CHUNKS`、追加件数は `context_group_expanded_count`、citation metadata は `context_group_expanded` / `context_anchor_chunk_id` / `context_group_id` / `context_group_distance` で追跡する。
   - `RAG_CONTEXT_NEIGHBOR_WINDOW` が 1 以上の場合は、rerank anchor の同一 document 前後 chunk を Oracle の `chunk_index` で取得し、生成 context へ低優先で追加する。既定は 0 で無効。追加件数は `context_expanded_count`、citation metadata は `context_expanded`、`context_anchor_chunk_id`、`context_neighbor_distance` で追跡する。
   - `RAG_CONTEXT_ADAPTIVE_EXPANSION_ENABLED=true` の場合は、同一 group sibling と隣接 chunk を query relevance / 構造連続性で絞り込み、必要な chunk だけを追加する。追加件数は `context_adaptive_expanded_count`、citation metadata は `context_adaptive_expanded` / `context_adaptive_reason` で追跡する。
   - `RAG_CONTEXT_DEPENDENCY_PROMOTION_ENABLED=true` の場合は、rerank top_n から落ちた caption / child / parent chunk も `dependency_edges` と `element_ids` で再取得・昇格する。追加件数は `context_dependency_promoted_count`、citation metadata は `context_dependency_promoted` / `context_dependency_reason` / `context_dependency_shared_element_ids` で追跡する。
   - `RAG_CONTEXT_COMPRESSION_ENABLED=true` の場合は、LLM context へ入れる前に query 関連 sentence / line を抽出して長い chunk を圧縮する。既定は無効。圧縮件数と節約文字数は `context_compressed_count` / `context_compression_saved_chars` に残し、citation metadata は `context_compressed`、`context_original_chars`、`context_compressed_chars` を持つ。query 本文や除外した本文は audit / trace に残さない。

9. AIDB Memory Engineering
   - 検索 request ごとに `BusinessContextPack` を作る。tenant/user/role は raw 値を保存せず hash の有無だけを診断し、document/category/knowledge base scope、`source_acl`、`document_version` を非機密 metadata として固定する。
   - `Memory Router / Plan Builder` は `RetrievalPlan` を作り、`evidence -> similar -> structure -> history` の `memory_sequence`、Oracle backend、scope key、evidence rule、termination criteria、gap handling を `SearchDiagnostics` と監査へ残す。Agent は plan 外の自由検索をしない。
   - `AIDB Retrieval` は既存の Oracle 26ai Hybrid Vector Search / Oracle Text / GraphRAG-lite 境界へ再マップする。`structure` は GraphRAG-lite、`history` は Oracle 26ai の `rag_agent_memories` を使う Agent Memory Search として扱い、外部 memory store は導入しない。
   - GraphRAG-lite の構築深度は **GraphRAG アダプター(`rag_graph_profile`)** で手動選択する。`app/rag/graph_adapter.py` が取込側 profile を解決し、`off`(既定・KG 非構築=現行挙動)/ `entities`(entities+relationships のみ)/ `full`(claims + community summary まで)で `build_graph_index` の `build_claims` / `build_community_summaries` を切り替える(entities は軽量)。ingest の graph gate は `resolve_graph_adapter(...).enabled`、legacy `RAG_GRAPH_ENABLED=true` は `full` 相当として後方互換を保つ。検索側 routing は Retrieval アダプターの `graph_augmented`(query-time)が担い、両者は合成する。`SearchDiagnostics.graph_profile` に残し、設定 API `GET/PATCH /api/settings/graph` と専用設定画面で切替する。profile 変更は次回以降の取込に適用され、既存文書への反映には再取込が必要。外部グラフ DB は導入しない。
   - Agent Memory は `X-User-ID` / `X-RAG-Role-ID` / `X-RAG-Agent-ID` / `X-RAG-Thread-ID` を hash 化した scope がある request でのみ検索・保存する。`memory_text` は `VECTOR(1536, FLOAT32)` に保存し、HNSW + Oracle Text index を持つ。
   - 回答後の Memory Loop は、guardrail 通過済み回答について query 原文ではなく「回答要約 + 根拠 ID」を `rag_agent_memories` へ writeback する。helpful / not helpful は `usefulness_score` の移動平均として評価できる。
   - `Resolver / Verifier` は取得候補をそのまま根拠化せず、citation、scope、source ACL、version、contradiction metadata を確認する。ACL 不適合、旧版、矛盾、citation 欠落は context から除外し、件数と理由だけを診断・監査へ残す。
   - `Context Builder` は LLM context を `Evidence`、`Support`、`Structure`、`History` に分ける。回答の主張を支える必須情報は `Evidence`、理解補助は `Support`、構造関係は `Structure`、継続文脈は `History` として label 付けする。Agent Memory は `History` として追加し、rerank で一次根拠を押し出さない。
   - 検証済み chunk metadata には `memory_plan_id`、`context_role`、`resolver_verified`、`resolver_confidence`、`resolver_necessity`、`evidence_allowed` を付与する。
   - 検証済み候補が 0 件の場合は LLM を呼ばず、no-results と warning を返す。これは「不足時に自由検索へ逃がさない」という Retrieval Plan の termination criteria である。

10. 回答生成
   - LLM は **OCI Enterprise AI**。検索根拠だけを context として渡す。
   - 回答スタイルは **Generation アダプター(`rag_generation_profile`)** で手動選択する。`app/rag/generation_adapter.py` が profile を system prompt 変種へ決定論で解決し、`grounded_concise`(既定・現行 system prompt)/ `detailed_cited`(出典 ID 明示)/ `strict_extractive`(抽出のみ・推測禁止)/ `structured_json`(JSON 構造化出力)/ `bilingual_ja_en`(日英)を `app/clients/oci_enterprise_ai.py` の `generate` / `generate_stream` へ渡す。追加 LLM 呼び出しや別 provider は導入しない。`GET/PATCH /api/settings/generation` と専用設定画面で切替。`SearchDiagnostics.generation_profile` に残す。
   - Enterprise AI gateway の request shape が標準 payload と異なる場合は、`OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` で JSON object template を設定する。
   - LLM 契約は `python -m app.rag.enterprise_ai_probe --surface llm` で個別に検証できる。回答本文は probe artifact に保存せず、parse 成功と文字数だけを確認する。
   - retrieval / rerank 後に citation が 0 件の場合は LLM を呼ばず、固定の no-results 回答と warning を返す。
   - generation context は rerank 後の上位 chunk を `RAG_CONTEXT_WINDOW_CHARS` に収めて作り、レスポンスと監査ログの `citations` には実際に context へ入った chunk だけを含める。
   - 生成後に secret leakage をブロックし、回答と citation context の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返す。
   - `/api/search` と `/api/search/stream` は `RAG_SEARCH_TIMEOUT_SECONDS` で pipeline 実行時間を制限する。通常検索は timeout 時に 504 を返す。SSE は stream 開始後に timeout した場合、HTTP status は維持して `error` event を返し、どちらも `rag_search_audit.error_stage=timeout` を残す。
   - embedding / retrieval / rerank / generation は `rag_search_stage_duration_seconds` で stage 別 latency を記録する。
   - レスポンスには `trace_id`、`citations`、`guardrail_warnings`、`diagnostics`、`elapsed_ms` を含める。
   - `POST /api/search/stream` は SSE で `stage`、`metadata`、`delta`、`citations`、`done` を返す。`stage` event は `embedding`、`retrieval`、`rerank`、`generation` などの `started` / `success` / `error` と低機密 attributes を表し、最終 `metadata.diagnostics.stream_stage_timings` には stage 別の ms timing を含める。`RAG_STREAM_REALTIME_ENABLED=true` では Enterprise AI の token streaming から generation 中に `delta` を即時送信し、最終 response では同じ回答を二重に `delta` 化しない。既存の `metadata`、`delta`、`citations`、`done` event contract は維持する。

## ダッシュボード集計

- API: `GET /api/dashboard/summary`
- 文書件数、月次アップロード/索引済み件数、検索可能チャンク数、最近の活動、readiness check をまとめて返す。
- Oracle document/chunk table の集計 SQL を使う。

## Oracle 26ai DDL 例

`OracleClient.oracle_document_schema_sql()` / `OracleClient.oracle_vector_schema_sql()` / `OracleClient.oracle_audit_schema_sql()` が返す DDL をベースにする。

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

CREATE TABLE rag_agent_memories (
    memory_id        VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash   CHAR(64),
    user_id_hash     CHAR(64),
    role_id_hash     CHAR(64),
    agent_id_hash    CHAR(64),
    thread_id_hash   CHAR(64),
    trace_id         VARCHAR2(64) NOT NULL,
    memory_text      CLOB NOT NULL,
    metadata_json    JSON,
    embedding        VECTOR(1536, FLOAT32) NOT NULL,
    usefulness_score NUMBER(8, 6) DEFAULT 0.5 NOT NULL,
    eval_count       NUMBER(10) DEFAULT 0 NOT NULL,
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at       TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);

CREATE VECTOR INDEX rag_agent_memories_embedding_hnsw_idx
    ON rag_agent_memories (embedding)
    ORGANIZATION INMEMORY NEIGHBOR GRAPH
    DISTANCE COSINE
    WITH TARGET ACCURACY 95;

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

document / chunk table には `tenant_id_hash` を持たせる。HTTP header `X-Tenant-ID` がある場合、raw tenant id は保存せず hash 化し、一覧・詳細・重複判定・retrieval を同一 tenant に閉じる。tenant header がない場合は全体を参照できる。認証済みの上位層が `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names` を付与した場合は、document id / category name scope を request context に保持し、document 一覧、詳細、chunk count、Oracle 26ai vector search、Oracle Text keyword search の SQL predicate に適用する。scope header が存在するが有効値がない場合は deny-all とする。

監査 table は query 本文、OCR 原文、tenant/user id の raw 値を保存しない。検索は `query_hash` と `query_chars`、retrieval/rerank/context diversity/context group expansion/context expansion/context compression/citation 件数、context 文字数、RAG 設定 fingerprint を保存する。tenant/user id は `tenant_id_hash` / `user_id_hash` として保存する。取込は `source_sha256` と `source_bytes` を保存し、trace id / request id でアプリログ・Langfuse・Prometheus と相関する。

## Trace export

`record_trace_span()` は構造化ログへ `rag_trace_span` を出し、`TRACE_EXPORT_HTTP_ENDPOINT` が設定されている場合は同じ脱機密化済み event を非同期 HTTP JSON で OpenTelemetry / Langfuse gateway へ送信する。export 対象は `trace_id`、stage 名、outcome、duration、低 cardinality attributes、`error_type` のみで、query 本文、context 本文、OCR 原文、prompt、例外 message は含めない。export queue が満杯または送信失敗しても RAG pipeline は継続し、失敗は `app.trace` logger の `rag_trace_export_*` イベントで確認する。
