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
