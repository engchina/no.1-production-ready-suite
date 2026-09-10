# 合成データ生成の受理・実行・結果確認

Issue #305。UI の `生成開始` は永続記録の受理であり、書込み成功の通知ではない。
要求件数と `DBA_LOAD_OPERATIONS` / operation の状態表が報告する書込み件数を分離する。
既存表の総行数から今回の生成件数を推定しない。

## UI

「生成状況」に受付済み、生成中、結果確認中、完了、部分完了、生成失敗、追加 0 件、結果未確認を表示する。
表ごとの要求・追加件数、処理終了日時、失敗理由を保持する。実行 ID は詳細を開いた場合のみ表示する。
ページ遷移・再読込では既存 run を再取得し、生成を再送しない。確認語は解除する。
別ページでは完了通知と結果へのリンクを表示する。状態取得失敗時は以前の状態を残して更新失敗を明示する。

完了・部分完了後、結果の表示条件を編集していなければ、一度だけ上限付きの参照を行う。
参照結果は「現在のテーブル内容」であり、今回生成された行だけの抽出ではない。SQL/結果本体は永続化しない。
生成記録の追加件数が正数でも現在の参照が 0 件なら、接続・権限・生成後の変更を確認する説明を表示する。
参照エラーは生成失敗に書き換えない。削除・再作成された対象表の結果参照は 409 で拒否する。

## API

- `POST /api/nl2sql/synthetic-data/runs` → 202。既存生成条件に `idempotency_key`（16〜128 文字）を追加する。
- `GET /api/nl2sql/synthetic-data/runs` → 同一 actor / DB context の直近 100 件。
- `GET /api/nl2sql/synthetic-data/runs/{run_id}` → 保存済み状態。履歴一覧に無い古い run も ID で参照できる。
- `GET /api/nl2sql/synthetic-data/runs/{run_id}/results?table_name=...&limit=100` → 今回の対象だけを現在の actor の参照権限で取得。上限 1〜10000。

同一 actor・context・受付キーで同じ条件なら既存 run を返す。条件が変われば 409。
同一 DB context・対象表の未終了 run がある場合、別 actor を含め重複受付を 409 で拒否する。
旧 `POST /synthetic-data/generate` も同じ永続受理を使う（202、`executed=false`、`status=accepted`、
`engine_meta.run_id/status_url`）。旧レスポンスの成功判定に依存する外部クライアントは新 API へ移行する。
旧 endpoint の再送は受付キーを持たないため、応答不明時は一覧から確認する。

## Oracle と worker

migration `019_synthetic_runs.sql` が `NL2SQL_SYNTHETIC_RUNS` と `NL2SQL_SYNTHETIC_LOCKS` を作成する。
受理は run と対象表 lock を同一 transaction で commit。CAS version で `pending → running` を一度だけ claim する。
実行直前に actor/権限、DB context、対象 OBJECT_ID、Profile、非対応列を再検証する。
生成前に監視権限と専用接続の SID / SERIAL# / USERNAME / 開始時刻を確認・保存する。
`DBA_LOAD_OPERATIONS` はこの組合せで検索し、全体の `MAX(ID)` を使わない。
Oracle 終端状態と全対象の chunk 件数を確認して初めて完了とする。未確認件数は null のまま保持する。

worker 再起動時は既に claim した生成を再実行せず、Oracle 記録との照合だけを再開する。
書込み開始後の応答切断は `unknown` とし lock を保持する。照合が復旧すれば最終状態へ移行する。
Oracle が結果を一意に返せない場合は自動再実行・lock 解除をしない。
管理者が実行記録を調査する必要がある。現時点では強制解除・中止・部分実行の自動再試行 UI は提供しない。

監視には接続ユーザーによる `DBA_LOAD_OPERATIONS`、現在セッションの `V$SESSION`、生成状態表の参照が必要。
監視できない構成では生成前に失敗させる。Oracle runtime / Oracle persistence が必須。

## 配置

1. 既存のシステムテーブル初期化フローで migration 019 を適用する。
   `cd backend && uv run python -m app.cli.nl2sql_system_schema --initialize`
2. ローカルは `NL2SQL_SYNTHETIC_WORKER_MODE=inprocess`（既定）。生成の本体は API の要求処理外で動く。
3. Compose は `synthetic-data-worker`、新規 systemd 配置は `production-ready-nl2sql-synthetic-worker.service`。
   専用 worker 構成では backend を `NL2SQL_SYNTHETIC_WORKER_MODE=external` にする。
4. 旧 systemd 配置に専用 unit が無い場合、更新スクリプトは backend の inprocess 動作を維持する。
   手動で external に変更した場合は専用 worker も起動する。

worker: `uv run python -m app.cli.nl2sql_synthetic_worker`。
システム表の再作成は未終了・未確認 run があれば拒否する。

## 待機表示と時間

生成状況は「生成開始」の直下、結果データ領域の手前に配置する。送信中は共通
`ProcessingIndicator` の受付表示とボタン内の spinner、受理後は状態パネル内の spinner
1 個に切り替える。状態ラベルは `StatusBadge`、計時は `useOperationTiming` を再利用する。

受付待ち・生成中・結果確認はサーバー状態の polling に追従する。経過時間は受付時刻
`created_at` から毎秒更新し、待機時間を含む。開始への遷移や再読込でリセットしない。
終了後は `finished_at - created_at` を「処理時間」として保持する。表示は共通規約の
`mm:ss` / `h:mm:ss`、読み上げは `role="timer" aria-live="off"`、reduced motion に対応する。

状態取得失敗時は直前の情報と「現在の処理状況は未確認」を表示し、spinner を停止する。
この場合の時計は受付からの経過であり、処理継続の証拠ではない。結果不明 (`unknown`) は
終了を断定せず、計時表示を外して再確認を案内する。新規生成を受理したら過去結果の
URL 選択を解除し、新しい生成状態を表示する。

## 検証

pytest は受理、認可、再起動、CAS、部分/0/null 件、API の隔離を検証する。
Playwright は desktop / mobile-375 で再読込、状態、通知、空結果、参照上限、既存操作を検証する。
`NL2SQL_SYNTHETIC_LIVE_TEST=1 uv run pytest tests/test_nl2sql_synthetic_oracle_live.py`
は独立した一時メタデータ表を作成・削除し、実 Oracle の CLOB/JSON、CAS、lock 競合 rollback を検証する。
このテストは業務表・生成プロシージャを変更/実行しない。
