# Oracle 原生 hybrid 索引の削除情報の整理

文書・Chunk の削除や、既存ベクトル／検索本文の更新は、業務と同じ transaction 内で小さな整理要求を記録します。rollback した要求は残りません。検索時の候補上限、検索回数、権限・検索範囲は変更しません。

API と取込 worker が永続要求を処理します。取込を無効にしていても API が動いていれば処理されます。複数プロセスでは DB の lease と token によって一つが取得します。検索要求から索引全件の集計や整理は呼びません。整理待ちを理由に検索全体を拒否する門番も設けません。

## 実行と失敗

- 業務の commit 後、別接続で `CTX_DDL.OPTIMIZE_INDEX(..., CTX_DDL.OPTLEVEL_TOKEN_TYPE, section_type => CTX_DDL.SECTION_SEMANTIC_INDEX)` を実行して `$VR` の削除残骸を片付けます（検索の候補の不足を起こすのはこの残骸です。#4097）。索引の再作成、LLM、embedding の呼出しはありません。
- 呼出しの成功では完了としません。`$VR` と可視の件数を比べ、残っていれば serial の `OPTLEVEL_FULL` へ進みます（Oracle Free では semantic で残骸が減らないことがあります。#4021）。件数が合っても `SEARCH` が足りないことがあり（Oracle Free の小さい索引で確認。FULL で戻りました。#4097）、件数の確認だけでは検索の回復を保証しません。
- 既定（`RAG_NATIVE_INDEX_FULL_OPTIMIZE_INTERVAL_HOURS=0`）では要求ごとに semantic の後で `OPTLEVEL_FULL` も実行します。全文の分（`$I`・`$N`）もここで整理します。実索引で semantic の後の検索を確かめるまで、この値は広げません。値を広げると、間隔が過ぎるまで FULL を飛ばします。最後に FULL を終えた時刻は `RAG_NATIVE_INDEX_MAINTENANCE` の `state_key='hybrid_vectors_full'` の行に残します。
- この FULL が時間切れになっても、`$VR` の件数が合っていれば要求は完了にします（時間切れの再試行を繰り返さないためです。続きは次の要求で進みます）。**このとき検索の回復は確かめていません。** ログの `native_index_maintenance_full_deferred` で分かります。
- 失敗の次の回は semantic を飛ばして FULL だけを実行します。FULL は serial なら続きから再開できますが、`TOKEN_TYPE` は毎回最後まで実行するので、時間内に終わらないと進みません。
- Oracle の [OPTIMIZE_INDEX 仕様](https://docs.oracle.com/en/database/oracle/oracle-database/26/ccref/optimize_index.html) は、整理時も問い合わせ・DML を継続できることと、整理する接続に暗黙 commit が発生することを説明しています。このため業務接続・lease の行 lock を持つ接続では整理しません。
- 確認間隔は15秒、lease は600秒です。接続取得後に token と残り lease を再確認します。整理全体の実行予算180秒を各 DB 呼出しの残り時間へ反映し、heartbeat は30秒間隔で延長します。
- coroutine を取り消しても SQL が終了したとは扱いません。専用接続の処理が終わるまで待ち、その間 heartbeat を維持します。取消時は要求を完了せず回復対象に残します。
- 取得時点で commit 済みだった要求の ID だけを完了します。後から commit した要求は次回へ残します。失敗時も要求は残り、指数的な待機（最大900秒）の後に再実行します。成功すると失敗回数をリセットします。
- 整理が追いつくまで削除済み情報が候補枠を消費する可能性は残ります。非同期整理の導入だけで、その期間の候補完全性を保証するものではありません。再検索や候補上限の増量で隠しません。

## 運用確認

管理権限で DB の以下の状態を読み取れます。要求には本文・質問・文書名を保存しません。

```sql
SELECT COUNT(*) AS pending_events FROM RAG_NATIVE_INDEX_EVENTS;
SELECT state_key, lease_owner, lease_until, next_attempt_at, attempt_count,
       last_error_type, last_completed_at
FROM RAG_NATIVE_INDEX_MAINTENANCE;
```

`state_key='hybrid_vectors'` の `last_completed_at` は最後に取得した要求集合の完了時刻です。新しい要求が後から届く場合があるため、この時刻だけで未処理ゼロとは判断しません。ログには整理の方式（`strategy`）、段階ごとの時間、整理前後の stored/visible 件数、完了した要求数、失敗の例外型を出し、SQL・接続情報・例外本文は出しません。

新しい表・trigger はシステムテーブルの通常の更新で適用します。各 cycle でシステムスキーマの準備状態を確認し、準備未完了と判定された間は取得を待ちます。`SYNC (ON COMMIT)` と削除情報の物理整理は別処理です。取込や検索の設定を変更して、この整理の代用にはしません。
