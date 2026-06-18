# Oracle HNSW ベクトル索引 推奨設定

この文書は、本プロジェクトで Oracle 26ai AI Vector Search を使う際の HNSW ベクトル索引の推奨設定、採用理由、検証方法、Oracle 公式ドキュメントへのリンクをまとめる。

> 注意: 索引名は **HNSW**。正式名称は `Hierarchical Navigable Small World`。`HWSW` ではない。

## 適用前提

- データベース: Oracle 26ai / Oracle AI Database。
- ベクトル列: `VECTOR(1536, FLOAT32)`。
- 埋め込み: OCI Generative AI Cohere Embed v4、1536 次元。
- RAG 検索の優先度: まず根拠チャンクの召回率と回答の根拠性を重視し、その後にリランク、フィルタ、キャッシュでレイテンシを最適化する。
- 距離関数: テキスト意味検索では既定で `COSINE` を使い、索引定義と検索 SQL の距離指標を必ず一致させる。

## 推奨 DDL

メンテナンス時間帯など、通常のオフライン作成でよい場合:

```sql
CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx
ON rag_chunks (embedding)
ORGANIZATION INMEMORY NEIGHBOR GRAPH
DISTANCE COSINE
WITH TARGET ACCURACY 95
PARAMETERS (
  TYPE HNSW,
  NEIGHBORS 32,
  EFCONSTRUCTION 500
)
PARALLEL 8;
```

索引作成中もベース表への書き込みを継続する必要がある場合は `ONLINE` を使う:

```sql
CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx
ON rag_chunks (embedding)
ORGANIZATION INMEMORY NEIGHBOR GRAPH
DISTANCE COSINE
WITH TARGET ACCURACY 95
PARAMETERS (
  TYPE HNSW,
  NEIGHBORS 32,
  EFCONSTRUCTION 500
)
ONLINE;
```

テナント、ステータス、文書 ID などで絞り込む検索が多い場合は、`INCLUDE` によるカバリング列を評価する:

```sql
CREATE VECTOR INDEX rag_chunks_embedding_hnsw_idx
ON rag_chunks (embedding)
INCLUDE (tenant_id_hash, document_id)
ORGANIZATION INMEMORY NEIGHBOR GRAPH
DISTANCE COSINE
WITH TARGET ACCURACY 95
PARAMETERS (
  TYPE HNSW,
  NEIGHBORS 32,
  EFCONSTRUCTION 500
);
```

`INCLUDE` はフィルタや列取得時のベース表アクセスを減らせる可能性がある。一方で、Oracle では included columns を持つ HNSW 索引の `ONLINE` 作成は現在サポートされていない。実運用では、実際の検索条件と実行計画で効果を確認してから採用する。

## 検索 SQL

検索側でも、索引と同じ距離指標を指定する:

```sql
SELECT
  chunk_id,
  document_id,
  chunk_text,
  VECTOR_DISTANCE(embedding, :query_vector, COSINE) AS distance
FROM rag_chunks
WHERE tenant_id_hash = :tenant_id_hash
ORDER BY VECTOR_DISTANCE(embedding, :query_vector, COSINE)
FETCH APPROX FIRST 20 ROWS ONLY WITH TARGET ACCURACY 95;
```

一時的に精度とレイテンシのバランスを変えたい場合は、検索 SQL で目標精度を上書きできる:

```sql
FETCH APPROX FIRST 20 ROWS ONLY WITH TARGET ACCURACY 90;
```

HNSW では `efsearch` を明示することもできる:

```sql
FETCH APPROX FIRST 20 ROWS ONLY
WITH TARGET ACCURACY PARAMETERS (efsearch 500);
```

`efsearch` を大きくすると、一般に召回率は上がるがレイテンシも増える。まずは `TARGET ACCURACY` を使い、実測でより細かい制御が必要になった場合だけ `efsearch` を指定する。

## この設定を推奨する理由

### `ORGANIZATION INMEMORY NEIGHBOR GRAPH`

Oracle では、HNSW は In-Memory Neighbor Graph ベクトル索引に分類される。HNSW はグラフベースの近似最近傍索引であり、大規模な意味検索で低レイテンシと高い召回率の両立を狙いやすい。

本プロジェクトでは外部ベクトル DB を導入しない。Oracle 26ai の HNSW を使うことで、データ、メタデータフィルタ、トランザクション整合性、ベクトル検索を同じ Oracle 境界内に集約できる。

### `DISTANCE COSINE`

テキスト埋め込みの意味検索では、余弦距離を既定にする。本プロジェクトの問い合わせ用埋め込みと文書チャンク用埋め込みは同じ Cohere Embed v4 の 1536 次元ベクトル空間にあるため、`COSINE` を標準の距離関数とする。

Oracle 公式ドキュメントでは、索引定義の距離指標と `VECTOR_DISTANCE()` で使う距離指標が一致しない場合、オプティマイザがベクトル索引を使わない可能性があると説明されている。そのため、DDL と検索 SQL の両方で `COSINE` を明示する。

### `WITH TARGET ACCURACY 95`

RAG の主な失敗は、検索が少し遅いことよりも、重要な根拠チャンクを取り逃がして回答が根拠に支えられなくなることにある。`TARGET ACCURACY 95` は召回率を重視した初期値として扱う。

検索時には用途ごとに調整する:

- プレビューや補助的な検索: `90` まで下げてレイテンシを優先する余地がある。
- 回答生成に使う本番 RAG 検索: まず `95` を基準にする。
- 高リスク QA や評価セット実行: `95` 以上、または `efsearch` 指定を検討する。
- 大量バッチ処理: スループットと精度レポートを見て個別に調整する。

## Vector Index アダプター(検索精度プロファイル)

この調整を UI から選べるよう、**Vector Index アダプター(`rag_vector_index_profile`)** に束ねる。`app/rag/vector_index_adapter.py` が profile を解決し、検索時 target accuracy を runtime 即時に切り替える(`GET/PATCH /api/settings/vector-index` と専用設定画面)。

| profile | 検索時 target accuracy | 推奨 HNSW ビルド(参考表示) | 索引再作成 |
|---|---|---|---|
| `balanced`(既定) | `ORACLE_VECTOR_TARGET_ACCURACY`(既定 95)をそのまま使用 | NEIGHBORS 32 / EFCONSTRUCTION 500 / COSINE(現行) | 不要 |
| `accurate` | 98 | NEIGHBORS 48 / EFCONSTRUCTION 800 / COSINE | 要 |
| `fast` | 85 | NEIGHBORS 16 / EFCONSTRUCTION 300 / COSINE | 要 |

- 機能レバーは **検索時 target accuracy**(runtime 即時反映)。`balanced` は既存設定値を尊重し挙動不変。
- 推奨ビルドパラメータは参考値で、適用には索引の再作成(再プロビジョニング)が必要(`requires_reprovision`)。本ドキュメントの推奨 DDL を `accurate` / `fast` のパラメータへ調整して再作成する。版管理された schema DDL artifact は自動変更しない。

### `NEIGHBORS 32`

`NEIGHBORS` は `M` と同義で、HNSW グラフの各ベクトルが持てる最大近傍接続数を表す。値を大きくするとグラフが密になり、召回率は上がりやすいが、メモリ使用量、索引作成時間、保守コストも増える。

`32` は本プロジェクトの初期値として、次の理由で採用する:

- 小さすぎる値よりグラフが疎または分断されにくい。
- 1536 次元のテキスト埋め込みに対して、召回率を重視した出発点になる。
- メモリコストを極端に増やさず、後続の精度レポートに基づく調整がしやすい。

### `EFCONSTRUCTION 500`

`EFCONSTRUCTION` は、HNSW グラフ構築時に挿入処理の各ステップで考慮する候補ベクトル数を表す。値を大きくするとグラフ品質は上がりやすいが、索引作成時間は増える。

`500` は品質寄りの本番初期値とする。RAG では索引作成コストより検索品質のほうが重要になりやすいため、まずは高めの構築品質から始める。Oracle 公式の HNSW 例でも、`NEIGHBORS 32, EFCONSTRUCTION 500` や `NEIGHBORS 40, EFCONSTRUCTION 500` の構成が示されている。

### `PARALLEL 8`

`PARALLEL 8` は索引作成時の初期値であり、固定値ではない。実際には DB の CPU、I/O、保守時間帯、同時実行ワークロードに合わせて調整する。高頻度 DML がある表では `ONLINE` 作成を優先して検討するが、DML が多いほど索引作成時間は伸びる。

## Vector Pool メモリ設計

HNSW 索引は SGA の Vector Pool に保持される。Oracle 公式ドキュメントでは、HNSW 索引に必要なメモリの概算式として次の式が示されている:

```text
1.3 * number_of_vectors * number_of_dimensions * size_of_dimension_type
```

本プロジェクトでは `VECTOR(1536, FLOAT32)` を使う。`FLOAT32` は 1 次元あたり 4 bytes のため:

```text
1.3 * N * 1536 * 4
```

100 万ベクトルあたりの概算:

```text
約 7.99 GB、約 7.44 GiB
```

本番では式だけに頼らず、`DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR` で見積もる。

ベクトル件数、次元数、型から見積もる例:

```sql
VARIABLE response_json CLOB;

EXEC DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR(
  'HNSW',
  1000000,
  1536,
  'FLOAT32',
  '{"neighbors":32}',
  :response_json
);

PRINT response_json;
```

既存テーブルとベクトル列から見積もる例:

```sql
VARIABLE response_json CLOB;

EXEC DBMS_VECTOR.INDEX_VECTOR_MEMORY_ADVISOR(
  USER,
  'RAG_CHUNKS',
  'EMBEDDING',
  'HNSW',
  '{"neighbors":32}',
  :response_json
);

PRINT response_json;
```

## 検証とチューニング手順

1. 推奨 DDL で HNSW 索引を作成する。
2. 実際の RAG 問い合わせセットでベースラインを取り、top-k 召回率、エンドツーエンドのレイテンシ、リランク後の命中率、回答の根拠性を記録する。
3. `DBMS_VECTOR.INDEX_ACCURACY_QUERY` で単一問い合わせベクトルの近似検索精度を確認する。
4. `DBMS_VECTOR.INDEX_ACCURACY_REPORT` で、キャプチャ済み問い合わせベクトルに基づくワークロード精度レポートを作る。
5. 召回率が不足する場合は、まず検索側の `TARGET ACCURACY` または `efsearch` を上げる。それでも不足する場合に `NEIGHBORS` / `EFCONSTRUCTION` を上げて索引を再作成する。
6. レイテンシまたはメモリ圧迫が大きい場合は、まず検索側目標精度の引き下げを評価し、その後にスカラー量子化、パーティショニング、RAC distributed HNSW を検討する。

単一問い合わせベクトルの精度確認例:

```sql
DECLARE
  report VARCHAR2(4000);
BEGIN
  report := DBMS_VECTOR.INDEX_ACCURACY_QUERY(
    OWNER_NAME      => USER,
    INDEX_NAME      => 'RAG_CHUNKS_EMBEDDING_HNSW_IDX',
    qv              => :query_vector,
    top_K           => 20,
    target_accuracy => 95
  );
  DBMS_OUTPUT.PUT_LINE(report);
END;
/
```

ワークロード精度レポートの作成例:

```sql
SELECT DBMS_VECTOR.INDEX_ACCURACY_REPORT(
  USER,
  'RAG_CHUNKS_EMBEDDING_HNSW_IDX'
) AS task_id
FROM dual;
```

結果確認例:

```sql
SELECT
  min_target_accuracy,
  max_target_accuracy,
  num_vectors,
  min_achieved_accuracy,
  median_achieved_accuracy,
  max_achieved_accuracy
FROM dba_vector_index_accuracy_report
WHERE index_name = 'RAG_CHUNKS_EMBEDDING_HNSW_IDX'
ORDER BY task_time DESC;
```

## 運用上の注意

- HNSW 索引はメモリ専用構造であり、Vector Pool の設計が必須。
- Vector Pool が不足すると、索引作成失敗や HNSW 索引の eviction の原因になる。
- ベクトル列内の非 NULL ベクトルは、次元数と要素型を統一する。本プロジェクトでは `VECTOR(1536, FLOAT32)` に固定する。
- 同じベクトル列には 1 種類のベクトル索引だけを作成する。HNSW を選ぶ場合、同じ列に IVF を重ねて作らない。
- HNSW は parallel DML をサポートしない。HNSW 索引付きテーブルへの parallel DML は Oracle によりシリアル DML に変換される。
- ベース表の DML が多い場合、Oracle は journal、snapshot、refresh などの仕組みで整合性を保つ。ただし、未反映変更が増えると検索性能に影響する可能性があるため、精度レポートと実行計画を継続的に確認する。
- 大規模 RAC では `DISTRIBUTE` を評価できる。ただし distributed HNSW は、snapshots、included columns、online build、スカラー量子化を現在サポートしないため、採用前に個別評価する。
- メモリ制約が強い場合は `QUANTIZATION SCALAR COMPRESSION RATIO` を評価できる。ただし量子化誤差が入るため、`RESCORE FACTOR` と精度レポートで品質を確認する。

## Oracle 公式ドキュメント

- [Oracle AI Vector Search User's Guide 26ai](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/)
- [CREATE VECTOR INDEX](https://docs.oracle.com/en/database/oracle/oracle-database/26/sqlrf/create-vector-index.html)
- [Hierarchical Navigable Small World Index Syntax and Parameters](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/hierarchical-navigable-small-world-index-syntax-and-parameters.html)
- [Guidelines for Using Vector Indexes](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/guidelines-using-vector-indexes.html)
- [Overview of Hierarchical Navigable Small World Indexes](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/overview-hierarchical-navigable-small-world-indexes.html)
- [Size the Vector Pool](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/size-vector-pool.html)
- [Vector Index Status, Checkpoint, and Advisor Procedures](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/vector-index-status-checkpoint-and-advisor-procedures.html)
- [Index Accuracy Report](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/index-accuracy-report.html)
- [Approximate Search Using HNSW](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/approximate-search-using-hnsw.html)
- [Included Columns with HNSW Indexes](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/included-columns-hnsw-indexes.html)
- [Scalar Quantized HNSW Indexes](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/scalar-quantized-hnsw-indexes.html)
- [HNSW Index Architecture: Transaction Support and Persistence](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/hnsw-index-architecture-transaction-support-and-persistence.html)
- [HNSW Distribution on Oracle RAC](https://docs.oracle.com/en/database/oracle/oracle-database/26/vecse/hnsw-distribution-oracle-rac.html)
