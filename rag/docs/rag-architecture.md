# RAG アーキテクチャ

このリポジトリは、請求書・伝票を対象にした production-ready RAG の参照実装です。クラウド接続なしで動く `AI_SERVICE_ADAPTER=local` を既定にし、同じ抽象境界を `AI_SERVICE_ADAPTER=oci` で OCI Enterprise AI / OCI Generative AI / Oracle 26ai へ差し替える構成です。

## パイプライン

1. ドキュメントアップロード
   - API: `POST /api/documents/upload`
   - 原本は Object Storage 境界へ保存する。local では `local://...` として `LOCAL_STORAGE_DIR` 配下に保存し、OCI では Object Storage SDK で `oci://namespace/bucket/key` として保存する。
   - `MAX_UPLOAD_BYTES` と `ALLOWED_UPLOAD_CONTENT_TYPES` でサイズ・MIME type を制限する。
   - 原本 bytes から SHA-256 とサイズを計算し、`content_sha256` / `file_size_bytes` として文書行へ保存する。
   - 同一 `content_sha256` の既存文書がある場合は `duplicate_of_document_id` に最初の原本文書 ID を保存する。

2. OCR・構造化抽出
   - API: `POST /api/documents/{document_id}/analyze`
   - LLM/VLM は **OCI Enterprise AI** のみを使う。OCI Generative AI chat API は使わない。
   - Object Storage から取得した原本 bytes は、保存済み `file_size_bytes` / `content_sha256` と照合してから OCR へ渡す。
   - サイズまたは SHA-256 が一致しない場合は `ERROR` にし、409 で拒否する。
   - VLM 出力は `StructuredExtraction` で Pydantic 検証してから保存する。
   - `UPLOADED` / `ERROR` を分析対象にし、`ANALYZING` は二重実行防止で 409 にする。
   - `ANALYZED` / `REGISTERED` は force なしなら既存結果を返す。`force=true` は `ANALYZED` の再分析に限定し、`REGISTERED` の再分析は拒否する。
   - 本登録は `ANALYZED` / `REGISTERED` かつ検索可能 chunk が 1 件以上ある場合だけ許可する。

3. チャンク分割
   - 実装: `backend/app/rag/chunking.py`
   - 日本語の句点・疑問符・感嘆符を文境界として扱い、`RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` で制御する。
   - `RAG_CHUNK_OVERLAP >= RAG_CHUNK_SIZE` は設定検証で拒否する。
   - `RAG_MAX_CHUNKS_PER_DOCUMENT` を超える文書は索引せず `ERROR` にして、異常な OCR 出力や誤設定による embedding コスト急増を防ぐ。

4. 埋め込み
   - 実装: `backend/app/clients/oci_genai.py`
   - 本番は OCI Generative AI Inference の `embed_text` で Cohere Embed v4、1536 次元を使う。
   - query embedding は `SEARCH_QUERY`、文書 chunk embedding は `SEARCH_DOCUMENT` の input type を使う。
   - local は deterministic hashing embedding を使い、CI でも同じ結果になる。
   - adapter の返却件数と 1536 次元幅を検証し、不一致なら Oracle へ渡す前に fail fast する。

5. 索引
   - 実装: `backend/app/clients/oracle.py`
   - 本番は Oracle 26ai AI Vector Search。ベクトル列は `VECTOR(1536, FLOAT32)`。
   - OCI adapter は python-oracledb の共有 pool を遅延初期化し、document/chunk の永続化、集計、状態更新を Oracle table に対して実行する。
   - chunk 保存と vector search の入口でも embedding 幅を再検証する。
   - 検索対象の chunk は `ANALYZED` / `REGISTERED` の文書に限定する。
   - `OracleClient.count_document_chunks()` で document 単位の索引存在確認を行い、索引のない文書を本登録させない。
   - 文書が `ANALYZING` / `ERROR` へ移る場合は、その文書の既存 chunk/index 行と古い抽出フィールドを削除して古い根拠や OCR 結果を残さない。
   - 外部ベクトル DB は使わない。

6. ハイブリッド検索
   - API: `POST /api/search`
   - `mode=hybrid|vector|keyword` を指定できる。hybrid は vector と keyword を Reciprocal Rank Fusion で統合する。
   - `filters` は `document_id`、`file_name`、`category_name`、`status` に対応し、retrieval 前に適用する。
   - keyword score は重複を除いた query token coverage として 0.0-1.0 に正規化する。
   - vector / keyword / hybrid の同点は document id、chunk index、chunk id で安定順にし、評価の再現性を保つ。
   - citation metadata には `retrieval_mode`、vector/keyword の rank/score、RRF score を含め、hybrid 召回の由来を query 本文なしで追跡できるようにする。

7. リランク
   - 本番は OCI Generative AI Cohere Rerank v4 fast。
   - local は語彙一致スコアで deterministic に並べ替える。
   - `rerank_top_n` は `top_k` 以下に制限し、retrieval 候補数を超える無意味な rerank 指定を拒否する。
   - adapter の返却 index は候補範囲内・重複なし、返却件数は `top_n` 以内、score は finite number であることを検証し、不正な rerank 結果は fail fast する。

8. 回答生成
   - LLM は **OCI Enterprise AI**。検索根拠だけを context として渡す。
   - retrieval / rerank 後に citation が 0 件の場合は LLM を呼ばず、固定の no-results 回答と warning を返す。
   - generation context は rerank 後の上位 chunk を `RAG_CONTEXT_WINDOW_CHARS` に収めて作り、レスポンスと監査ログの `citations` には実際に context へ入った chunk だけを含める。
   - 生成後に secret leakage をブロックし、回答と citation context の token / n-gram 重なりが少ない場合は `low_groundedness` warning を返す。
   - `/api/search` と `/api/search/stream` は `RAG_SEARCH_TIMEOUT_SECONDS` で pipeline 実行時間を制限し、timeout 時は 504 を返して `rag_search_audit.error_stage=timeout` を残す。
   - embedding / retrieval / rerank / generation は `rag_search_stage_duration_seconds` で stage 別 latency を記録する。
   - レスポンスには `trace_id`、`citations`、`guardrail_warnings`、`diagnostics`、`elapsed_ms` を含める。
   - `POST /api/search/stream` は SSE で `metadata`、`delta`、`citations`、`done` を返す。local adapter は生成済み回答を分割し、本番 adapter は Enterprise AI のストリーミングに差し替える。

## ダッシュボード集計

- API: `GET /api/dashboard/summary`
- 文書件数、月次アップロード/登録件数、カテゴリ数、検索可能チャンク数、最近の活動、readiness check をまとめて返す。
- local adapter は in-memory document/chunk store から算出する。OCI adapter は Oracle document/chunk table の集計 SQL を使う。

## データ参照 / Select AI

- API: `POST /api/table-browser/query`
- 本番は Oracle Select AI で自然言語を SQL に変換し、Oracle 内の登録済みデータを参照する。
- `ORACLE_SELECT_AI_PROFILE` が設定されている場合は `DBMS_CLOUD_AI.GENERATE` に profile name を渡す。
- local adapter は同じレスポンス契約（`columns` / `rows` / `row_count`）で、登録済みドキュメントを JSON-ready な行として返す。
- `query` は空白だけの入力を拒否し、`limit` は 1〜200 行に制限する。
- Select AI 境界は参照専用とし、prompt injection と SQL 変更意図（`drop/delete/update/insert` など）は 422 で拒否する。

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
    extracted_fields         JSON,
    error_message            VARCHAR2(2000),
    uploaded_at              TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    registered_at            TIMESTAMP WITH TIME ZONE
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

CREATE VECTOR INDEX rag_chunks_embedding_idx
    ON rag_chunks (embedding)
    ORGANIZATION NEIGHBOR PARTITIONS
    DISTANCE COSINE;

CREATE INDEX rag_chunks_text_idx
    ON rag_chunks (chunk_text)
    INDEXTYPE IS CTXSYS.CONTEXT;

CREATE INDEX rag_chunks_tenant_document_idx
    ON rag_chunks (tenant_id_hash, document_id);

CREATE TABLE rag_search_audit (
    audit_id              VARCHAR2(64) DEFAULT RAWTOHEX(SYS_GUID()) PRIMARY KEY,
    event_type            VARCHAR2(32) DEFAULT 'rag.search' NOT NULL,
    trace_id              VARCHAR2(64) NOT NULL,
    request_id            VARCHAR2(128),
    tenant_id_hash        CHAR(64),
    user_id_hash          CHAR(64),
    outcome               VARCHAR2(32) NOT NULL,
    mode                  VARCHAR2(16) NOT NULL,
    query_hash            CHAR(64) NOT NULL,
    query_chars           NUMBER(10) NOT NULL,
    filter_keys           JSON,
    top_k                 NUMBER(10),
    rerank_top_n          NUMBER(10),
    guardrail_codes       JSON,
    guardrail_severities  JSON,
    retrieved_count       NUMBER(10) DEFAULT 0 NOT NULL,
    reranked_count        NUMBER(10) DEFAULT 0 NOT NULL,
    citation_count        NUMBER(10) DEFAULT 0 NOT NULL,
    context_chars         NUMBER(10) DEFAULT 0 NOT NULL,
    context_window_chars  NUMBER(10),
    document_ids          JSON,
    adapter               VARCHAR2(16),
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
    field_count            NUMBER(10) DEFAULT 0 NOT NULL,
    chunk_count            NUMBER(10) DEFAULT 0 NOT NULL,
    vector_count           NUMBER(10) DEFAULT 0 NOT NULL,
    elapsed_ms             NUMBER(12, 3) NOT NULL,
    error_type             VARCHAR2(128),
    error_message          VARCHAR2(2000),
    created_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL
);
```

document / chunk table には `tenant_id_hash` を持たせる。HTTP header `X-Tenant-ID` がある場合、raw tenant id は保存せず hash 化し、一覧・詳細・重複判定・Select AI 代替・retrieval を同一 tenant に閉じる。tenant header がない local/CI 実行では全体を参照できる。

監査 table は query 本文、OCR 原文、tenant/user id の raw 値を保存しない。検索は `query_hash` と `query_chars`、retrieval/rerank/citation 件数、context 文字数、RAG 設定 fingerprint を保存する。tenant/user id は `tenant_id_hash` / `user_id_hash` として保存する。取込は `source_sha256` と `source_bytes` を保存し、trace id / request id でアプリログ・Langfuse・Prometheus と相関する。

## 残る本番差し替え点

- `OciEnterpriseAiClient._extract_with_enterprise_ai`
- `OciEnterpriseAiClient._generate_with_enterprise_ai`

これらの公開メソッドは既に API / pipeline から利用されているため、実装を接続しても上位レイヤーの契約は維持される。
