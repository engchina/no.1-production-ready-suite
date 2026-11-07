# Oracle 原生 hybrid 索引の削除情報の整理

文書・Chunk の削除や、既存ベクトル／検索本文の更新は、業務と同じ transaction 内で小さな整理要求を記録します。rollback した要求は残りません。検索時の候補上限、検索回数、権限・検索範囲は変更しません。

API と取込 worker が永続要求を処理します。取込を無効にしていても API が動いていれば処理されます。複数プロセスでは DB の lease と token によって一つが取得します。検索要求から索引全件の集計や整理は呼びません。整理待ちを理由に検索全体を拒否する門番も設けません。

## 実行と失敗

- 業務の commit 後、別接続で `CTX_DDL.OPTIMIZE_INDEX(..., CTX_DDL.OPTLEVEL_TOKEN_TYPE, section_type => CTX_DDL.SECTION_SEMANTIC_INDEX)` を実行して `$VR` の削除残骸を片付けます（検索の候補の不足を起こすのはこの残骸です。#4097）。索引の再作成、LLM、embedding の呼出しはありません。
- 呼出しの成功では完了としません。`$VR` と可視の件数を比べ、残っていれば要求を残して再試行します（Oracle Free では semantic で残骸が減らないことがあります。#4021）。件数が合っても `SEARCH` が足りないことがあり（Oracle Free の小さい索引で確認。FULL で戻りました。#4097）、件数の確認だけでは検索の回復を保証しません。どちらも serial の `OPTLEVEL_FULL` で片付けます。全文の分（`$I`・`$N`）もここで整理します。
- **FULL は `RAG_NATIVE_INDEX_FULL_OPTIMIZE_INTERVAL_HOURS`（既定 6 時間）に 1 回までしか始めません（#4214）。** 開始の前に次に始めてよい時刻を commit するので、成功・失敗・時間切れ・再起動のどれでも開始から数えます。再試行でも、semantic の後に残骸があっても、間隔の内は FULL を始めません。
- **FULL の借り:** 間隔の内に semantic だけで要求を終えると、FULL の借りが残ります。間隔が過ぎたら、新しい要求が無くても worker が FULL だけを実行します。これで最後の要求の後も `$VR`・`SEARCH` の件数と全文の分が収束します（`tests/test_native_maintenance_oracle.py`）。
- FULL が時間切れになっても、`$VR` の件数が合っていれば要求は完了にします。**このとき検索の回復は確かめていません。** ログの `native_index_maintenance_full_deferred` で分かります。借りは残り、続きは次の間隔で進みます。`$VR` が残っていれば要求も残り、既存の待ちの後に semantic を再試行します。
- FULL を始めてよいときの失敗の次の回は、semantic を飛ばして FULL だけを実行します。FULL は serial なら続きから再開できますが、`TOKEN_TYPE` は毎回最後まで実行するので、時間内に終わらないと進みません。
- 節流の間に残骸があっても、検索はエラーにしません。許可された範囲が `RAG_NATIVE_VECTOR_EXACT_MAX_CHUNKS` 以下のベクトルの分岐は距離順に読むので残骸の影響を受けず（#4150）、切替をまたいだ検索は再確認します（#4179）。上限を超える範囲のベクトルの分岐は、不足したときだけ候補を広げて 1 回読み直し（`RAG_NATIVE_VECTOR_SHORTAGE_OVERSAMPLE_FACTOR`。#4097）、それでも残る不足は処理の経過とログに残します。

### 既定の 6 時間の根拠

測定はどれも開発の ADB（`cpu_count=1`）と合成の索引の結果で、値は目安です。規模や CPU が違う環境では値を見直します。

| 項目 | 結果 |
|---|---|
| FULL の所要（約 47,000〜50,000 行。ログ） | 37〜2,096 秒 |
| FULL の CPU（ASH。1 回の 29 分） | 1,658 サンプル中 ON CPU 1,439 |
| semantic の所要（合成の 20,000 行。#4161） | 0.6〜6.7 秒。実索引は未測定 |
| FULL を止めた影響（合成。再取込 1 回 + semantic を 10 回。#4161） | `SEARCH` の件数は変わらず。`$I` が 18% 増、全行にある語の全文が 116→138 ms |

6 時間なら、最悪の FULL（約 35 分）でも 1 CPU の約 1 割です。24 時間にすると CPU は減りますが、時間切れの FULL の続き（途中まで進む例が #4097 にあります）と候補の不足が長く残ります。

`call_timeout` は FULL を確実には止めません（lock の待ちで 54 分以上続いた例があります。#4215）。最小間隔は、時間切れの後にサーバー側で実行が残っても、次の FULL を重ねないための上限でもあります。Oracle の背景の job（`DR$<idx>$OMJ`）の負荷は、この設定では変わりません（#4215）。
- Oracle の [OPTIMIZE_INDEX 仕様](https://docs.oracle.com/en/database/oracle/oracle-database/26/ccref/optimize_index.html) は、整理時も問い合わせ・DML を継続できることと、整理する接続に暗黙 commit が発生することを説明しています。このため業務接続・lease の行 lock を持つ接続では整理しません。
- 確認間隔は15秒、lease は600秒です。接続取得後に token と残り lease を再確認します。整理全体の実行予算180秒を各 DB 呼出しの残り時間へ反映し、heartbeat は30秒間隔で延長します。
- coroutine を取り消しても SQL が終了したとは扱いません。専用接続の処理が終わるまで待ち、その間 heartbeat を維持します。取消時は要求を完了せず回復対象に残します。
- 取得時点で commit 済みだった要求の ID だけを完了します。後から commit した要求は次回へ残します。失敗時も要求は残り、指数的な待機（最大900秒）の後に再実行します。成功すると失敗回数をリセットします。
- 整理が追いつくまで削除済み情報が候補枠を消費する可能性は残ります。非同期整理の導入だけで、その期間の候補完全性を保証するものではありません。検索は不足を見つけたときだけ候補を広げて 1 回読み直し、最終の件数・範囲・権限は変えません（[rag-engine.md](./rag-engine.md)「ベクトルの分岐の読み方」。#4097）。読み直しは整理の代わりではなく、不足は処理の経過とログに残します。

## 運用確認

管理権限で DB の以下の状態を読み取れます。要求には本文・質問・文書名を保存しません。

```sql
SELECT COUNT(*) AS pending_events FROM RAG_NATIVE_INDEX_EVENTS;
SELECT state_key, lease_owner, lease_until, next_attempt_at, attempt_count,
       last_error_type, last_completed_at
FROM RAG_NATIVE_INDEX_MAINTENANCE;
```

`state_key='hybrid_vectors'` の `last_completed_at` は最後に取得した要求集合の完了時刻（FULL の借りだけの回も含みます）です。新しい要求が後から届く場合があるため、この時刻だけで未処理ゼロとは判断しません。

`state_key='hybrid_vectors_full'` の行は FULL の記録です。`next_attempt_at` は次に FULL を始めてよい時刻、`attempt_count` は前回の FULL の完了の後に受けた要求の取得の回数（FULL の借り。0 なら不要）、`last_completed_at` は FULL を最後に終えた時刻です。

ログには整理の方式（`strategy`）、段階ごとの時間、整理前後の stored/visible 件数、完了した要求数、失敗の例外型を出し、SQL・接続情報・例外本文は出しません。

新しい表・trigger はシステムテーブルの通常の更新で適用します。各 cycle でシステムスキーマの準備状態を確認し、準備未完了と判定された間は取得を待ちます。`SYNC (ON COMMIT)` と削除情報の物理整理は別処理です。取込や検索の設定を変更して、この整理の代用にはしません。
