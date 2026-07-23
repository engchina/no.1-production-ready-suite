# 高速処理・長時間処理アーキテクチャ

## 原則

対話画面は「軽量 read model + revision-aware cache/ETag + SWR」を使用し、全量 Catalog、CLOB、
Ontology graph、embedding を読み込まない。構造再取得や AI 処理など長時間処理は HTTP request 内で
実行せず、永続 job、lease/heartbeat、独立 worker、backpressure で処理する。

同期 I/O の実行場所は [backend-concurrency-contract.md](./backend-concurrency-contract.md) に従う。

## Request class と性能予算

| class | timeout | 実装契約 |
|---|---:|---|
| `interactive-list` | 8 秒 | keyset pagination、最大100件、SWR、cancel、dedupe。p95 3秒以内 |
| `interactive-detail` | 15 秒 | owner/object 単位。DDL、CLOB、関連 graph は必要時のみ |
| `job-control` | 5 秒 | submit/status のみ。1秒間隔、前回完了後に次回 poll |
| `long-running` | HTTP 実行禁止 | durable job + worker。lease、heartbeat、再開、coalescing 必須 |

timeout とユーザー取消は区別する。取消はエラー表示せず、timeout は現在の表示を保持して再試行を出す。
legacy fallback は 404/410/501 の互換性エラーだけに限定し、network/timeout/5xx で重い旧 API を呼ばない。

## Schema read/write path

- 一覧: `NL2SQL_SCHEMA_OBJECTS` を `q/type/row_state/cursor/limit` で検索する。
- head: `NL2SQL_SCHEMA_CATALOG_HEAD` だけを読み、ETag は published catalog version に結び付ける。
- 詳細: owner + object name の列・制約だけを取得する。
- refresh: `NL2SQL_SCHEMA_REFRESH_JOBS` へ HTTP 202 で投入する。pending/running job は合流する。
- publish: worker が manifest 差分を取得し、成功時だけ active catalog を切り替える。失敗時は旧版を維持する。
- concurrency: read path は Schema refresh のプロセス内実行 lock を取得しない。

Docker/本番は `NL2SQL_SCHEMA_REFRESH_WORKER_MODE=external` と `schema-refresh-worker` を使用する。
直接ローカル実行だけ `inprocess` を既定とする。

## Mutation の影響分類

- データのみ: INSERT/UPDATE/DELETE/MERGE/TRUNCATE、既存表 CSV、append/truncate import。Schema job を作らない。
- 構造/metadata: CREATE/ALTER/DROP/COMMENT/ANNOTATION、create/replace import。非同期 Schema job を1件返す。

frontend は mutation response の `schema_refresh_job_id` を追跡し、同じ refresh を再投入しない。

## 可用性と repository circuit

可用性は OCI ADB lifecycle、Oracle SQL connection、NL2SQL persistence/read model を別々に扱う。
`/api/ready/database` は polling 互換のため常に HTTP 200 とし、接続状態は response body で判定する。
業務 API の 503 は persistence status と安定 `error_code` を併せて確認する。

- listener、session 切断、driver timeout だけが process-local persistence circuit を open にする。
- SQL 構文、集約、bind など repository 実装エラーは operation 単位の 500 とし、他の read model を停止しない。
- open circuit は 5–30 秒で backoff し、期限後の最初の1要求だけが half-open probe を行う。並行要求は待たずに失敗する。
- probe 成功後は同じ安全な read request を1回だけ再試行できる。DML/DDL や他の非冪等 request は自動再送しない。
- migration table/column 不整合は軽量 migration check で `setup_required` と operation error を区別する。

## Backpressure と観測性

- 同一 Schema の pending/running refresh は coalesce する。
- worker は lease 切れ job だけを回収し、同一プロセスの二重実行を防ぐ。
- route-template HTTP latency に加え、pending age、phase duration、error code、changed object 数、repository SQL、
  CLOB bytes、repository failure class、circuit state、recovery outcome を Prometheus へ記録する。
- structured log は request ID、job ID、catalog version を相関キーにする。

## 今後の移行対象

| flow | 予算/方針 |
|---|---|
| Enterprise AI 生成 | submit 500ms以内、durable job/SSE。対話同期の場合も明示 timeout と cancel |
| 大容量 CSV/Excel import | upload 完了後に永続 job。parse/write/schema publish を別 phase 化 |
| XLSX/CSV export | 小規模は15秒以内。大規模は snapshot version 付き export job |
| Ontology build/reasoning | 既存 durable job + external worker を維持し、object detail lock と分離 |
