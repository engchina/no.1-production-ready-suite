# Backend Concurrency Contract

FastAPI は ASGI で動作するため、`async def` route の中で同期 I/O を直接実行すると event loop 全体を塞ぐ。
Production Ready NL2SQL の API は、単一 worker のローカル開発でも 1 つの重い処理が他画面の API を止めないことを契約とする。

## 原則

- **sync route by default**: domain service、Oracle repository、file/CLOB/Excel、OCI SDK など同期処理を呼ぶ route は普通の `def` で実装する。FastAPI/Starlette が route 全体を threadpool で実行する。
- **async route は本当に async の時だけ**: `UploadFile.read()`、SSE/WebSocket、async OCI client など、`await` が必要な処理を持つ route だけ `async def` にする。
- **async route から同期処理を呼ぶ時は `run_sync_io`**: `backend/app/api/concurrency.py` の `run_sync_io(...)` で同期 I/O を threadpool に逃がす。
- **CLI/worker は対象外**: ASGI event loop 上ではない CLI、batch、worker は同期 service を直接呼んでよい。

## 禁止例

```python
@router.get("/example")
async def example() -> ApiResponse[ExampleData]:
    data = nl2sql_service.load_heavy_data()
    return ApiResponse(data=data)
```

## 推奨例

同期 service だけを呼ぶ route は `def` にする。

```python
@router.get("/example")
def example() -> ApiResponse[ExampleData]:
    return ApiResponse(data=nl2sql_service.load_heavy_data())
```

UploadFile など async I/O と同期 service が混在する場合だけ `run_sync_io` を使う。

```python
@router.post("/example/import")
async def import_example(file: UploadFile) -> ApiResponse[ImportData]:
    content = await file.read()
    data = await run_sync_io(nl2sql_service.import_data, content)
    return ApiResponse(data=data)
```

## Guardrail

`backend/tests/test_async_route_concurrency_contract.py` が `backend/app/**/router.py` を AST で走査し、route-decorated `async def` が同期 service/Oracle/file/SDK 呼び出しを直接行わないことを検査する。
新しい async route を追加してこのテストが落ちた場合は、route を `def` に変えるか、同期呼び出しを `await run_sync_io(...)` に移す。

## Markdown 公開準備の実行期限と中断

- Markdown 公開前の解析ジョブは成果物を状態の正本とし、`jobs` の状態を開始時・終了時・取得時に同期する。
- `NL2SQL_ONTOLOGY_PREPARATION_TIMEOUT_SECONDS`（既定 600 秒）は待機時間を含む期限。Enterprise AI の解析は自動再送せず、残り時間を HTTP timeout に渡す。
- in-process で開始した解析は shutdown 時に off-loop で `PREPARATION_INTERRUPTED` として終了させてから DB pool を閉じる。起動時の DB 接続は行わない。
- 強制終了や旧版が残した `queued` / `running` は、状態取得時に期限を確認して `PREPARATION_TIMEOUT` に収束する。旧レコードは作成日時と設定値から期限を求める。
- 実行開始と結果保存は同じ成果物の ETag を使う。重複配送は解析を再送せず、中断・期限切れ後の遅着結果は破棄する。期限は同期 HTTP スレッドを強制 kill するものではない。
- エラー後は画面から公開前の確認を再実行する。解析成功後も、差分・検証結果を確認してから公開するゲートを維持する。

## Oracle Profile 同期

- `NL2SQL_PROFILE_SYNC_JOB_TIMEOUT_SECONDS`（既定 300 秒）の期限を job payload の `deadline_at` に保存する。旧 job は作成日時から判定し、取得時に期限切れを `PROFILE_SYNC_TIMEOUT` へ収束させる。
- API は認可用 `peek()` の後に scope を確認し、初めて状態を回復する `get()` を呼ぶ。起動時の DB 読み取りは行わない。
- 同期開始は queued の取得済み ETag で claim する。各 phase / 終了 / 失敗の保存と次の Oracle 操作の前に、その実行が保持する ETag を検証する。running / terminal の再配送で Oracle 反映を再送しない。
- 正常停止では、このプロセスが開始した job を off-loop で中断してから pool を閉じる。同期呼び出しの強制 kill は行わず、中断後の戻り値を破棄し後続 phase を開始しない。
- Oracle 操作は部分反映の可能性があるため、中断は成功と断定しない。日本語メッセージで Oracle Profile / Agent の確認を促し、明示的な再試行だけを受け付ける。自プロセスの前回呼び出しがまだ残っている場合は再試行を拒否する。

## オントロジー AI 構築

- `NL2SQL_ONTOLOGY_BUILD_TIMEOUT_SECONDS`（既定 21600 秒）の絶対期限と `NL2SQL_ONTOLOGY_BUILD_LEASE_SECONDS`（既定 120 秒）の heartbeat を永続化する。長い LLM 呼び出し中も heartbeat を更新する。
- 取得・履歴・次の開始時に失われた実行を `cancelled` へ収束させ、`ONTOLOGY_BUILD_TIMEOUT` / `ONTOLOGY_BUILD_WORKER_LOST` を表示する。旧 job は作成日時・開始日時・イベント日時を使う。external worker の queued は heartbeat 不在だけでは中断しない。
- queued の ETag で実行を claim し、実行 ID を確認して結果を保存する。期限切れ・中断後の遅着結果は下書きを上書きしない。profile lock の削除には所有 job ID も指定し、新しい job の lock を削除しない。
- 状態確認は認可後に行い、startup で全件を走査しない。同期 SDK の実行スレッド自体を強制停止する機構ではない。
- 再実行は利用者の明示操作で新しい job を作る。保存済み入力を引き継ぎ、profile fingerprint と task/context が一致する既存の抽出 checkpoint を再利用する。保存済み下書きは保持し、自動公開しない。

## 旧 Ontology 公開・推論

- 旧 global revision API の OWL2RL / SHACL worker は `NL2SQL_ONTOLOGY_PUBLISH_TIMEOUT_SECONDS`（既定600秒）の絶対期限を保存する。現在の Profile Markdown snapshot 公開とは別の経路であり、Profile を指定した旧公開要求は引き続き拒否する。
- GET は認可用 peek の後に store の最新状態を確認する。旧 job も作成日時から失効させ、inprocess の正常 shutdown は所有 job だけを off-loop で終了させる。startup は DB-free。
- queued の ETag を claim し、実行 ID を持つ worker だけが進捗・成果物を書ける。推論・検証後と公開 head 切替直前にも失効を確認し、二重配送では推論を繰り返さない。
- revision の `publish_job_id` / reasoning graph を照合する。既に公開 commit 済みなら immutable draft の Markdown コピーだけを補い、既存のコピーと head / revision 履歴を保持して job を完了させる。保存確認に失敗した場合は「版は公開済み」と明記し、再公開を案内しない。
- 期限は同期スレッドを kill しない。遅着処理の後続 commit を制限し、commit 境界での停止は取得時の照合で収束させる。

## 旧実データ検証 job

- `NL2SQL_ONTOLOGY_VALIDATION_TIMEOUT_SECONDS`（既定600秒）の絶対期限を保存し、旧 job は作成日時で判定する。認可済みの GET / 同じキーの再送で失効状態を回復する。再実行は新しいキーで明示的に開始する。
- queued（external worker の queued 由来 claim を含む）だけを CAS で開始し、実行 ID と取得済み ETag を保持する。running の再配送・期限切れ後の SQL 結果を破棄し、SQL の次の呼び出し・検証レポート commit 前にも失効を確認する。
- レポートを含む bundle と hash 付き完了証跡を artifacts の同一 transaction で保存する。job の完了保存だけが失われた場合、証跡から結果を復元し、Oracle 検証 SQL を再送しない。
- SQL_EXECUTE / Profile scope の認可、読み取り専用 SQL、sample_limit、検証後の Profile / schema fingerprint / bundle ETag 確認を維持する。sampled data を全 DB の検証済みとは扱わない。
- inprocess の shutdown は所有 job のみを off-loop で回復し、pool close 前に実施する。同期 SQL スレッドの強制 kill は行わない。現在の Markdown snapshot のデータ検証とは別の旧 async API が対象。

## Select AI DB Profile 一覧更新

- `NL2SQL_PROFILE_LIST_REFRESH_TIMEOUT_SECONDS`（既定600秒）を絶対期限として永続化する。旧 pending/running は作成日時から回復し、期限・lease 切れを error として一覧更新の再実行を案内する。
- 一覧更新 collection 内で有効な実行は1件にする。Oracle の exclusive claim は短い transaction 内で state table を lock して同 collection の生存 job を確認する。外部 DB の一覧・詳細取得は lock の外で実行する。通常の SQL job claim は従来の行単位 SKIP LOCKED のまま。
- state repository の `patch_document_if_current()` は worker / attempt 等の比較と lease / deadline を row lock 内で確認する。Profile キャッシュの差分、更新メタ情報、job 完了を同一 transaction で保存し、失効・保存失敗で旧キャッシュを部分更新しない。
- 取得時の回復は CAS 相当の条件付き更新を使い、生存 worker や完了済み job を上書きしない。running の GET は thread を再配送しない。
- inprocess shutdown はこのサービスが dispatch / claim した job だけを pool close 前に off-loop で中断する。実 Oracle Profile の作成・変更を自動再送せず、再実行するのは一覧の読み取りのみ。
