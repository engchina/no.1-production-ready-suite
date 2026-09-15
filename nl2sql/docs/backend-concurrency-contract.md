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
