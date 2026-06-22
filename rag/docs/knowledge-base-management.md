# ナレッジベース管理設計

> 本ドキュメントは、Production Ready RAG に「ナレッジベース管理」を追加するための設計メモである。
> Dify / RAGFlow / AnythingLLM / FastGPT / MaxKB / R2R などのプロダクト級 RAG が持つ
> dataset / knowledge base / collection の考え方を参考にしつつ、実装は本プロジェクトの確定スタック
> (OCI Enterprise AI / OCI Generative AI Cohere / Oracle 26ai / Vite + React Router) へ再マッピングする。

最終更新: 2026-06-15

---

## 1. 背景と結論

現在の実装は `rag_documents.category_name` による軽い分類、`X-RAG-Allowed-Document-Ids` /
`X-RAG-Allowed-Category-Names` による検索・一覧スコープを持つ。ただしこれは「文書属性」であり、
運用者が検索対象のまとまりを作り、アップロード時に所属先を選び、ナレッジベース単位で索引状況・エラー・検索範囲を
管理するための一等リソースではない。

プロダクト級 RAG では、文書単体よりも「どのナレッジベースに入っているか」が主要な運用単位になる。したがって本プロジェクトでも
`ナレッジベース` を first-class resource として追加する。

設計方針:

- `category_name` は廃止せず、タグ/カテゴリ相当の軽量 facet として残す。
- ナレッジベースは `rag_knowledge_bases` として独立管理する。
- 文書とナレッジベースは多対多の関連表で管理し、同じ文書・chunk・embedding を物理複製しない。
- 検索、評価、ダッシュボード、監査、認可はナレッジベース ID をスコープとして扱う。
- UI 表示名は日本語で「ナレッジベース」とし、i18n key 経由で管理する。

## 2. 参考プロジェクトから取り込む考え方

| 参考元 | 取り込む考え方 | 本プロジェクトでの再マッピング |
|---|---|---|
| Dify | Knowledge / Dataset をアプリ・検索範囲の単位にする | Oracle 上の `rag_knowledge_bases` と retrieval scope |
| RAGFlow | 複雑文書をナレッジベース単位で取込・検索・引用確認する | 既存の構造化抽出 / chunk metadata / citation と接続 |
| AnythingLLM | ワークスペース/コレクション単位で文書を整理する | tenant + knowledge base + document membership |
| FastGPT / MaxKB | ナレッジベース管理と RAG 検索 UI を一体化する | サイドナビ、アップロード、文書一覧、検索の共通スコープ |
| R2R | API first の文書管理・検索管理 | FastAPI の CRUD / membership / search API と pytest 契約 |

外部ベクトル DB、別 LLM プロバイダ、別フロントエンド基盤は導入しない。

## 3. 用語

| 用語 | 意味 |
|---|---|
| ナレッジベース | 検索対象文書をまとめる運用単位。例: 社内規程、製品 FAQ、契約書、障害対応手順。 |
| 文書 | アップロードされた原本ファイルと抽出結果。既存の `rag_documents`。 |
| membership | 文書がどのナレッジベースに所属するかを表す関連行。 |
| カテゴリ | 文書の軽量 facet。既存 `category_name`。ナレッジベースとは別物として残す。 |
| 既定ナレッジベース | アップロード時に明示選択がない文書を入れるデフォルトの所属先。 |

## 4. 機能範囲

### Phase 1 で必須

- ナレッジベースの作成、一覧、詳細、更新、アーカイブ。
- 文書アップロード時のナレッジベース指定。
- 既存文書のナレッジベース追加・削除。
- 文書一覧のナレッジベース絞り込み。
- RAG 検索のナレッジベース絞り込み。
- tenant / access scope とナレッジベース scope の AND 条件適用。
- backend pytest と frontend API/Vitest/Playwright の追加。

### Phase 1 では扱わない

- ナレッジベースごとの別 embedding モデル設定。
- ナレッジベースごとの外部 vector store。
- 人手承認ゲート付きの取込ワークフロー。
- ACL / RBAC の完全なユーザー管理 UI。
- GraphRAG 専用のグラフ管理 UI。

これらは将来拡張として扱う。

## 5. データモデル

### 5.1 基本方針

- `rag_documents` は引き続き文書原本・抽出結果・状態の正本。
- `rag_chunks` は文書ごとに一度だけ作成する。ナレッジベースごとに chunk を複製しない。
- `rag_document_knowledge_bases` で文書所属を表す。
- retrieval は `rag_chunks -> rag_documents -> rag_document_knowledge_bases -> rag_knowledge_bases` を join して絞り込む。
- `tenant_id_hash` をナレッジベースと membership にも持たせ、tenant predicate を軽くする。

### 5.2 Oracle DDL 案

```sql
CREATE TABLE rag_knowledge_bases (
    knowledge_base_id      VARCHAR2(64) PRIMARY KEY,
    tenant_id_hash         CHAR(64),
    name                   VARCHAR2(256) NOT NULL,
    description            VARCHAR2(2000),
    status                 VARCHAR2(32) DEFAULT 'ACTIVE' NOT NULL,
    default_search_mode    VARCHAR2(16) DEFAULT 'hybrid' NOT NULL,
    retrieval_config       JSON,
    created_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    updated_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    archived_at            TIMESTAMP WITH TIME ZONE,
    CONSTRAINT rag_knowledge_bases_status_ck
        CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    CONSTRAINT rag_knowledge_bases_mode_ck
        CHECK (default_search_mode IN ('hybrid', 'vector', 'keyword'))
);

CREATE UNIQUE INDEX rag_knowledge_bases_tenant_name_uidx
    ON rag_knowledge_bases (
        NVL(tenant_id_hash, '__GLOBAL__'),
        LOWER(name)
    );

CREATE INDEX rag_knowledge_bases_tenant_status_idx
    ON rag_knowledge_bases (tenant_id_hash, status, updated_at DESC);

CREATE TABLE rag_document_knowledge_bases (
    knowledge_base_id       VARCHAR2(64) NOT NULL,
    document_id             VARCHAR2(64) NOT NULL,
    tenant_id_hash          CHAR(64),
    assigned_at             TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
    assigned_by_user_id_hash CHAR(64),
    PRIMARY KEY (knowledge_base_id, document_id),
    CONSTRAINT rag_doc_kb_kb_fk
        FOREIGN KEY (knowledge_base_id)
        REFERENCES rag_knowledge_bases (knowledge_base_id)
        ON DELETE CASCADE,
    CONSTRAINT rag_doc_kb_document_fk
        FOREIGN KEY (document_id)
        REFERENCES rag_documents (document_id)
        ON DELETE CASCADE
);

CREATE INDEX rag_doc_kb_document_idx
    ON rag_document_knowledge_bases (document_id, knowledge_base_id);

CREATE INDEX rag_doc_kb_tenant_kb_idx
    ON rag_document_knowledge_bases (tenant_id_hash, knowledge_base_id, assigned_at DESC);
```

### 5.3 既存 schema への追加

`rag_documents` には必須の新規列を追加しない。既存 API と移行の互換性を保つため、所属は関連表だけで表現する。

`rag_search_audit` には `knowledge_base_ids JSON` を追加する。監査ログに名前・説明は保存しない。

```sql
ALTER TABLE rag_search_audit ADD knowledge_base_ids JSON;
```

`rag_ingestion_audit` は文書単位の監査のまま維持する。必要であれば低機密の `knowledge_base_count NUMBER(10)` を追加するが、
Phase 1 では必須にしない。

### 5.4 既定ナレッジベース

アップロード時にナレッジベースが指定されない場合は、tenant ごとに `既定ナレッジベース` を自動作成し、その文書を所属させる。

理由:

- ナレッジベース未所属の文書が検索対象に混ざる状態を避ける。
- 既存 UI のアップロード体験を壊さず段階移行できる。
- CI / local 開発では tenant header がなくても `__GLOBAL__` 相当の既定所属で動作する。

### 5.5 既存データ移行

初期移行では次のどちらかを選べるようにする。

1. 安全移行: 全既存文書を `既定ナレッジベース` に所属させる。
2. カテゴリ移行: `category_name` がある文書は category ごとに同名ナレッジベースを作成し、未分類だけ既定へ入れる。

本番移行では安全移行を既定とし、カテゴリ移行は staging で件数・名称衝突を確認してから実行する。

## 6. Backend API

### 6.1 ナレッジベース CRUD

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/knowledge-bases?status=ACTIVE&q=&limit=50&offset=0` | 一覧。文書数・索引済み数・エラー数も返す。 |
| `POST` | `/api/knowledge-bases` | 作成。 |
| `GET` | `/api/knowledge-bases/{knowledge_base_id}` | 詳細。 |
| `PATCH` | `/api/knowledge-bases/{knowledge_base_id}` | 名前、説明、既定検索モード、構築設定を更新。 |
| `POST` | `/api/knowledge-bases/{knowledge_base_id}/archive` | アーカイブ。文書と chunk は削除しない。 |

作成 payload:

```json
{
  "name": "社内規程",
  "description": "就業規則、情報セキュリティ規程、申請手順を含む",
  "default_search_mode": "hybrid",
  "adapter_config": {
    "ingestion": {
      "chunking_strategy": "structure_aware",
      "chunk_size": 800,
      "parser_adapter_backend": "local"
    }
  }
}
```

KB が持つのはナレッジ構築設定だけとする。検索方法、根拠確認、回答スタイル、安全チェック、
品質評価は業務ビューの検索・回答設定で扱う。旧 `retrieval_config` / `query` 系の値は
後方互換の読み取り対象に留め、runtime では使わない。

レスポンス summary:

```json
{
  "id": "kb_...",
  "name": "社内規程",
  "description": "就業規則、情報セキュリティ規程、申請手順を含む",
  "status": "ACTIVE",
  "default_search_mode": "hybrid",
  "document_count": 42,
  "indexed_document_count": 38,
  "error_document_count": 2,
  "searchable_chunk_count": 1280,
  "created_at": "2026-06-15T00:00:00Z",
  "updated_at": "2026-06-15T00:00:00Z",
  "archived_at": null
}
```

### 6.2 文書 membership API

| Method | Path | 用途 |
|---|---|---|
| `GET` | `/api/knowledge-bases/{knowledge_base_id}/documents?status=&q=&limit=&offset=` | 対象ナレッジベースの文書一覧。 |
| `POST` | `/api/knowledge-bases/{knowledge_base_id}/documents` | 既存文書を一括追加。 |
| `DELETE` | `/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}` | 文書をナレッジベースから外す。文書自体は削除しない。 |
| `GET` | `/api/documents/{document_id}/knowledge-bases` | 文書の所属ナレッジベース一覧。 |
| `PUT` | `/api/documents/{document_id}/knowledge-bases` | 文書の所属を指定リストに置換。 |

一括追加 payload:

```json
{
  "document_ids": ["doc_1", "doc_2"]
}
```

置換 payload:

```json
{
  "knowledge_base_ids": ["kb_1", "kb_2"]
}
```

制約:

- アーカイブ済みナレッジベースへ新規文書は追加できない。
- 文書が少なくとも 1 つのナレッジベースに所属する状態を原則とする。
- 最後の membership 削除は、代替所属先を指定するか、既定ナレッジベースへ自動移動する。

### 6.3 アップロード API

`POST /api/documents/upload` は multipart form に `knowledge_base_ids` を追加する。

```text
file=<UploadFile>
knowledge_base_ids=kb_1
knowledge_base_ids=kb_2
```

指定がない場合は既定ナレッジベースへ所属させる。

`UploadResult` には `knowledge_bases` を追加する。

```json
{
  "id": "doc_...",
  "file_name": "security-policy.pdf",
  "status": "UPLOADED",
  "file_size_bytes": 12345,
  "content_sha256": "...",
  "duplicate_of_document_id": null,
  "knowledge_bases": [
    { "id": "kb_1", "name": "社内規程" }
  ]
}
```

### 6.4 文書一覧 API

`GET /api/documents` は `knowledge_base_id` query を追加する。

```text
GET /api/documents?knowledge_base_id=kb_1&status=INDEXED&q=規程&limit=50&offset=0
```

`DocumentSummary` には `knowledge_bases` を追加する。既存 `category_name` は互換性のため残す。

```json
{
  "id": "doc_...",
  "file_name": "security-policy.pdf",
  "status": "INDEXED",
  "category_name": "社内規程",
  "knowledge_bases": [
    { "id": "kb_1", "name": "社内規程" }
  ],
  "uploaded_at": "2026-06-15T00:00:00Z",
  "indexed_at": "2026-06-15T00:10:00Z"
}
```

### 6.5 検索 API

`POST /api/search` はナレッジベース scope を明示的に受け取る。

推奨 payload:

```json
{
  "query": "リモートワーク時の申請手順は？",
  "knowledge_base_ids": ["kb_1"],
  "top_k": 20,
  "rerank_top_n": 5,
  "mode": "hybrid",
  "filters": {
    "content_kind": "text"
  }
}
```

互換性のため、Phase 1 では `filters.knowledge_base_id` も単一 ID として受け付ける。ただし新規 UI は
`knowledge_base_ids` を使う。

retrieval SQL の基本条件:

```sql
JOIN rag_document_knowledge_bases dkb
  ON dkb.document_id = d.document_id
JOIN rag_knowledge_bases kb
  ON kb.knowledge_base_id = dkb.knowledge_base_id
WHERE d.status = 'INDEXED'
  AND kb.status = 'ACTIVE'
  AND dkb.knowledge_base_id IN (...)
  AND tenant / access predicates
```

検索レスポンスの citation には、可能な場合だけ `knowledge_bases` または `knowledge_base_ids` を低機密 metadata として含める。
監査ログには ID のみ保存し、名前・説明は保存しない。

### 6.6 評価 API

`/api/evaluation/run` と `/api/evaluation/compare` の case / experiment に `knowledge_base_ids` を追加する。

```json
{
  "case_id": "remote-work-policy",
  "query": "リモートワーク時の申請手順は？",
  "knowledge_base_ids": ["kb_1"],
  "relevant_document_ids": ["doc_1"],
  "expected_keywords": ["申請", "承認"]
}
```

これにより golden set をナレッジベース単位で管理できる。

## 7. 認可・tenant・監査

### 7.1 tenant

- `X-Tenant-ID` がある場合、ナレッジベース、membership、文書、chunk は同じ `tenant_id_hash` に限定する。
- raw tenant id は DB / レスポンス / 監査ログへ保存しない。
- tenant header がない local / CI では全体を参照できるが、既定ナレッジベースは global 相当として扱う。

### 7.2 access scope

既存 scope に加えて、次の header を追加する。

```text
X-RAG-Allowed-Knowledge-Base-Ids: kb_1,kb_2
```

適用ルール:

- `allowed_document_ids`、`allowed_category_names`、`allowed_knowledge_base_ids` はすべて AND 条件。
- header が存在するが有効値が 0 件の場合は deny-all。
- ナレッジベース一覧は許可された ID のみ返す。
- 文書一覧、詳細、membership 更新、検索 retrieval、評価 retrieval は同じ predicate を使う。
- membership 変更は、対象ナレッジベースと対象文書の両方にアクセスできる場合だけ許可する。

### 7.3 監査

検索監査へ追加する項目:

- `knowledge_base_ids`: 検索 scope に使われた ID の配列。
- `knowledge_base_count`: scope 件数。
- `filter_keys`: `knowledge_base_ids` または `knowledge_base_id` を含める。

ログへ出さない項目:

- query 本文。
- OCR 原文。
- ナレッジベース名・説明。
- raw tenant id / user id。
- 例外 message。

## 8. UI / UX 設計

### 8.1 ナビゲーション

サイドナビの「データ取込」セクションに `ナレッジベース` を追加する。

推奨順:

1. ダッシュボード
2. ナレッジベース
3. アップロード
4. 文書インデックス

理由:

- ユーザーは先に検索対象の箱を作り、その後にアップロードする。
- アップロード済み文書の管理もナレッジベース詳細から始められる。
- 既存のサイドナビ構造を保ち、深い階層にしない。

新規 route:

```ts
knowledgeBases: "/knowledge-bases"
knowledgeBaseDetail: "/knowledge-bases/:id"
```

### 8.2 ナレッジベース一覧

目的: 管理者・運用者が検索対象のまとまりと状態を素早く把握する。

表示要素:

- ページヘッダー: `ナレッジベース`
- 主アクション: `作成`
- 検索 input: 名前・説明の部分一致。
- status filter: `すべて / 有効 / アーカイブ済み`
- 一覧 table:
  - 名前
  - 説明
  - 文書数
  - 索引済み
  - エラー
  - 検索対象チャンク
  - 更新日時
  - 操作
- empty / loading / error state は `docs/frontend-messaging-spec.md` に従う。

UI 方針:

- 業務系コンソールなので、カードを並べるよりも dense な table を主表示にする。
- 行クリックで詳細へ遷移する。操作ボタンは編集・アーカイブなど明確な command だけにする。
- 破壊的操作は確認ダイアログを出す。アーカイブは文書削除ではないことを明記する。
- モバイル幅では主要列だけ表示し、その他は詳細画面へ寄せる。

### 8.3 作成・編集

作成/編集は dialog または dedicated page のどちらでもよいが、Phase 1 は dialog を推奨する。

フィールド:

- 名前: required、最大 256 文字。
- 説明: optional、最大 2000 文字。
- 既定検索モード: segmented control (`hybrid / vector / keyword`)。
- 構築設定: 折りたたみ領域。文書解析、文書分割、索引構築、品質 gate を設定。
- 検索・回答設定は業務ビュー側で設定し、KB 作成/編集フォームには出さない。

UX 要件:

- label を必ず表示し、placeholder だけにしない。
- validation error は該当 field の直下に表示する。
- submit 中は primary button を loading / disabled にする。
- 成功時は toast、失敗時は form status と toast を使い分ける。
- Escape / キャンセルで閉じられるが、未保存変更がある場合は確認する。

### 8.4 ナレッジベース詳細

構成:

- breadcrumb: `ナレッジベース / {name}`
- header:
  - 名前
  - status badge
  - 文書数、索引済み、エラー、検索対象チャンク
  - actions: `アップロード`, `文書を追加`, `編集`, `アーカイブ`
- tabs:
  - `文書`
  - `概要`
  - `設定`

`文書` tab:

- 既存の文書一覧 table を再利用する。
- status filter / search / pagination / bulk selection を維持する。
- 一括操作:
  - 取込
  - 所属解除
  - 別ナレッジベースへ追加
- 所属解除は文書削除ではないことを確認ダイアログに明記する。

`概要` tab:

- 最近のアップロード/索引 activity。
- エラー文書の短いリスト。
- 検索対象 chunk 数の推移は将来拡張。Phase 1 は静的 metrics でよい。

`設定` tab:

- 名前、説明、既定検索モード、retrieval config。
- アーカイブ状態の説明。

### 8.5 アップロード画面

アップロード前にナレッジベース選択を表示する。

推奨:

- 初期値は最近使ったナレッジベース、なければ `既定ナレッジベース`。
- 単一選択を基本にし、詳細オプションで複数選択を許可する。
- 選択肢には status、文書数、説明を出す。
- アーカイブ済みナレッジベースは選択不可。

アップロード後の `DocumentWorkspace` には所属ナレッジベースの badge を表示する。

### 8.6 文書インデックス

変更点:

- filter に `ナレッジベース` select を追加する。
- table の `カテゴリ` 列は残すが、主列として `ナレッジベース` を追加する。
- 複数所属は badge で最大 2 件まで表示し、超過分は `+N` とする。
- 行内操作に `所属を変更` を追加する。

### 8.7 RAG 検索

検索フォームの上部に `検索対象ナレッジベース` を置く。

要件:

- 既定では最近使ったナレッジベースを選択する。
- 選択なしで全体検索を許可するかは権限に従う。運用 UI では明示的に `すべてのアクセス可能なナレッジベース` と表示する。
- 検索結果の citation にナレッジベース badge を表示する。
- 検索 scope は URL query または state に保持し、戻る操作で復元する。

### 8.8 アクセシビリティとレスポンシブ

UI 実装時の必須確認:

- 375px 幅で横スクロールしない。
- table はモバイルで重要列優先にする。必要なら行詳細展開を使う。
- すべての input/select/dialog/button に label または aria-label を付ける。
- icon-only button は tooltip と aria-label を持つ。
- keyboard だけで作成、編集、所属変更、検索実行ができる。
- focus ring を消さない。
- loading / empty / error / success state を `docs/frontend-messaging-spec.md` に合わせる。
- button size / variant / 配置は `docs/frontend-button-spec.md` に合わせる。

## 9. Backend 実装方針

### 9.1 schema

追加予定:

- `backend/app/schemas/knowledge_base.py`
  - `KnowledgeBaseStatus`
  - `KnowledgeBaseSummary`
  - `KnowledgeBaseDetail`
  - `KnowledgeBaseCreateRequest`
  - `KnowledgeBaseUpdateRequest`
  - `KnowledgeBaseDocumentAssignmentRequest`
  - `KnowledgeBaseRef`

既存 schema 変更:

- `DocumentSummary.knowledge_bases: list[KnowledgeBaseRef]`
- `DocumentDetail.knowledge_bases: list[KnowledgeBaseRef]`
- `UploadResult.knowledge_bases: list[KnowledgeBaseRef]`
- `SearchRequest.knowledge_base_ids: list[str] = []`
- `SearchDiagnostics.knowledge_base_count: int`

### 9.2 OracleClient

追加予定メソッド:

- `create_knowledge_base(...)`
- `list_knowledge_bases(...)`
- `get_knowledge_base(...)`
- `update_knowledge_base(...)`
- `archive_knowledge_base(...)`
- `ensure_default_knowledge_base(...)`
- `assign_documents_to_knowledge_base(...)`
- `remove_document_from_knowledge_base(...)`
- `replace_document_knowledge_bases(...)`
- `list_document_knowledge_bases(document_id)`

既存メソッド変更:

- `create_document(..., knowledge_base_ids: list[str] | None = None)`
- `list_documents(..., knowledge_base_id: str | None = None)`
- `count_documents(..., knowledge_base_id: str | None = None)`
- retrieval where builder に `knowledge_base_ids` を追加。

Local store も Oracle adapter と同じ契約で更新し、単体テストが外部 DB なしで通るようにする。

### 9.3 取込 pipeline

取込処理自体は文書単位のまま変更しない。

- `UPLOADED -> INGESTING -> INDEXED` は文書状態。
- 同じ文書が複数ナレッジベースに所属しても、chunk / embedding は一度だけ作る。
- ナレッジベース所属変更では再 embedding しない。
- 検索対象になるかどうかは、文書が `INDEXED` かつ所属ナレッジベースが `ACTIVE` かで決まる。

## 10. Frontend 実装方針

追加予定:

- `frontend/src/components/knowledge-bases/KnowledgeBaseListClient.tsx`
- `frontend/src/components/knowledge-bases/KnowledgeBaseDetailClient.tsx`
- `frontend/src/components/knowledge-bases/KnowledgeBaseFormDialog.tsx`
- `frontend/src/components/knowledge-bases/KnowledgeBasePicker.tsx`
- `frontend/src/components/knowledge-bases/KnowledgeBaseBadges.tsx`

既存変更:

- `APP_ROUTES` に `knowledgeBases` を追加。
- `NAV_SECTIONS` に `ナレッジベース` を追加。
- `api.ts` に knowledge base 型と API client を追加。
- `queries.ts` に TanStack Query hooks を追加。
- `UploadWorkspace` に `KnowledgeBasePicker` を追加。
- `FileListClient` に knowledge base filter / column / membership action を追加。
- `SearchClient` に search scope picker を追加。
- `DashboardClient` に knowledge base metrics を追加するか、Phase 1 ではリンクだけ追加する。

i18n key 例:

```ts
"nav.knowledgeBases": "ナレッジベース"
"knowledgeBases.title": "ナレッジベース"
"knowledgeBases.create": "作成"
"knowledgeBases.edit": "編集"
"knowledgeBases.archive": "アーカイブ"
"knowledgeBases.field.name": "名前"
"knowledgeBases.field.description": "説明"
"knowledgeBases.empty.title": "ナレッジベースがありません"
"knowledgeBases.error.load": "ナレッジベースの取得に失敗しました。"
```

## 11. テスト範囲

### 11.1 Backend pytest

追加・更新するテスト:

- schema validation:
  - name required / max length。
  - `default_search_mode` enum。
  - `knowledge_base_ids` の空値・重複除去。
- API:
  - create/list/get/update/archive。
  - archived KB への document assignment 拒否。
  - upload with knowledge base。
  - upload without knowledge base uses default。
  - list documents by knowledge base。
  - search by knowledge base。
- Oracle adapter:
  - DDL artifact に `rag_knowledge_bases` / `rag_document_knowledge_bases` が含まれる。
  - membership insert/delete。
  - retrieval SQL が membership join と `kb.status = 'ACTIVE'` を含む。
  - tenant predicate と allowed knowledge base predicate が入る。
- local store:
  - multi membership。
  - search scope filtering。
  - deleting document removes membership。
- audit:
  - search audit に `knowledge_base_ids` が入る。
  - name / description は audit に出ない。

### 11.2 Frontend Vitest

追加・更新するテスト:

- `api.listKnowledgeBases` query string。
- `api.createKnowledgeBase` payload。
- `api.uploadDocument` が `knowledge_base_ids` を FormData に入れる。
- `api.search` が `knowledge_base_ids` を送る。
- `KnowledgeBaseBadges` の省略表示。
- `KnowledgeBasePicker` の empty / loading / selected state。

### 11.3 Playwright

UI/UX 変更なので必須。

主要 e2e:

- ナレッジベース一覧を表示し、作成 dialog で新規作成できる。
- 名前 validation error が field 直下に出る。
- ナレッジベース詳細から文書を追加・所属解除できる。
- アップロード時にナレッジベースを選択でき、アップロード後に badge が表示される。
- 文書インデックスでナレッジベース filter が効く。
- RAG 検索で選択したナレッジベース ID が request payload に入る。
- 375px と desktop 幅で、主要操作に横スクロールや重なりがない。
- keyboard 操作で作成 dialog を開き、保存/キャンセルできる。
- loading / empty / error state を確認する。

### 11.4 実行コマンド

実装後に最低限実行する。

```bash
cd backend && uv run pytest && uv run ruff check . && uv run mypy .
cd frontend && npm run lint && npm run build && npm run test -- --run
cd frontend && npm run e2e
```

Playwright 実行時は dev server を起動し、desktop と mobile viewport の両方を確認する。

## 12. 段階実装計画

### Step 1: Backend foundation

- `knowledge_base.py` schema 追加。
- Oracle DDL artifact 追加。
- `OracleClient` に CRUD / membership 追加。
- request context に `allowed_knowledge_base_ids` を追加。
- pytest で DB 契約を固定する。

### Step 2: Document and search integration

- upload API に `knowledge_base_ids` 追加。
- document summary/detail に `knowledge_bases` 追加。
- document list / count に `knowledge_base_id` filter 追加。
- search request / retrieval / diagnostics / audit に `knowledge_base_ids` 追加。
- evaluation case / experiment に `knowledge_base_ids` 追加。

### Step 3: Frontend management UI

- route / nav / i18n 追加。
- ナレッジベース一覧・作成/編集 dialog。
- ナレッジベース詳細・文書 membership UI。
- Vitest と Playwright を追加。

### Step 4: Existing flows update

- upload に `KnowledgeBasePicker`。
- file list に filter / column。
- search に scope picker。
- dashboard に metrics / link。
- mobile / desktop / keyboard / empty-loading-error state を Playwright で確認。

### Step 5: Migration and ops

- 既存文書の構築 artifact 回填は [oracle-variant-backfill-runbook.md](./oracle-variant-backfill-runbook.md) を正とする。
- `app.rag.variant_backfill_cli` で read-only 検証 SQL と manifest を生成し、staging artifact として保存する。
- 実データの回填はレビュー済み手順で行い、parser / preprocess 差分が再抽出できない文書は `needs_reingest` として分離する。
- staging で category migration option を検証する。
- 監査 table migration と docs/rag-architecture.md / backend README を更新する。

## 13. 受け入れ基準

- ユーザーはナレッジベースを作成し、そこへ文書をアップロードできる。
- 文書は 1 つ以上のナレッジベースに所属する。
- 同じ文書を複数ナレッジベースに所属させても chunk / embedding は複製されない。
- ナレッジベースを選択した検索は、その所属文書の `INDEXED` chunk だけを retrieval 対象にする。
- tenant / document / category / knowledge base の認可 scope はすべて AND 条件で効く。
- アーカイブ済みナレッジベースは新規 upload / assignment / search scope に使えない。
- 監査ログに query 本文、OCR 原文、ナレッジベース名・説明、raw tenant/user id が出ない。
- UI は日本語 i18n、既存 Button / Messaging spec、Playwright 検証を満たす。
