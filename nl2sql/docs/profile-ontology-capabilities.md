# Profile Ontology の構築・公開・能力利用

`AI 構築を実行` は現在の DB context / Profile の資料と Schema から業務定義の草稿を作る。業務のサンプルを利用環境へ登録せず、Function / Action を自動実行・自動有効化しない。

## 構築から利用まで

1. Object Type / Property / Link Type / Function / Action Type / Interface と補助 7 分類を型付きで保存する。根拠不足・不適用・失敗は分類別に表示する。
2. 入力固定、証拠抽出、物理モデル、共通契約、能力契約、統合検証の各段階を記録する。再構築では安定 ID、手編集、レビュー済み定義を保持し、差分をレビューへ送る。
3. 型付き版から文書、図、OWL/SHACL、SQL context を生成する。静的検証と人のレビューは別のゲートであり、Profile の公開 pointer を独立して更新する。データ検証・受入テストは独立した確認操作で実行し、対象・標本件数・未検証範囲を報告する。
4. 「能力の利用（Capabilities）」で公開された定義に実装を設定する。設定不足は「設定が必要（Configuration Required）」のまま残す。SQL 生成 session / job は `business_release_id` を保持し、後日の公開で過去の生成 context を置き換えない。

## 能力と権限

| 能力 | 初期実装 | 必要な権限 |
| --- | --- | --- |
| binding の更新 | ETag による競合検出、公開版に固定、明示的な有効化・無効化 | `nl2sql.ontology.capabilities.manage` |
| Function | Oracle の検査済み SELECT、bind パラメーターを使う宣言式、登録済みのバックエンド実装 | `nl2sql.sql.execute` |
| Action | 主キーで一意な単一テーブルの属性更新、transaction 内の登録済み処理 | `nl2sql.ontology.actions.execute` |

Profile 所有権・現在の権限・公開版の有効範囲をサーバーで再確認する。管理メニュー権限から Action 実行権限を暗黙追加しない。DB の `UPDATE` 権限は別途管理する。AI の資料から DB 権限を付与しない。

Action は資料から生成されたパラメーター、書込み可能な Property、影響範囲に加えて、業務条件を明示的に binding する。現在状態の等値条件を宣言でき、複雑な業務処理はサーバーで登録した実装に委ねる。SQL の任意 UDF、DDL、DML、実行 hint、曖昧な列は Function SQL の対象外。`COUNT(*)` は許可する。

登録はアプリケーション code の `register_function` / `register_action` のみ。HTTP に code 登録 API はない。Function の実装には検査済み SELECT だけを提供する `ReadOnlyFunctionContext`、Action の実装には契約内の属性だけを更新できる `ActionExecutionContext` を渡す。後者に独立した commit はない。外部サービスへの副作用を持つ実装はこの transaction registry の対象外であり、外部 adapter は追加していない。

## 確認・競合・transaction

プレビューは Profile、公開版、binding ETag、ユーザー、入力パラメーター、対象主キー、対象値、`ORA_ROWSCN`、有効期限に固定する。画面で入力が変わった場合、離脱・再読込した場合は確認を破棄する。実行前には DB 行をロックして再取得し、状態とバージョンを再検査する。

`ORA_ROWSCN` は `ROWDEPENDENCIES` のないテーブルでは block 単位になる。そのため他行の更新でも安全側に再プレビューを要求する場合がある。ビュー・外部テーブルには利用できないため、初期の属性更新は物理テーブルを対象にする。[Oracle ORA_ROWSCN](https://docs.oracle.com/en/database/oracle/oracle-database/19/sqlrf/ORA_ROWSCN-Pseudocolumn.html)

属性更新、実行結果、冪等キーの記録は同一のユーザーデータ接続・同一 transaction で commit する。変更後の実値がプレビューに一致しない場合も rollback する。成功監査の保存失敗は更新全体の失敗とする。失敗監査は rollback 後に区別して保存する。commit の通信結果が不明な場合は成功記録を再照会し、確認できなければ結果未確定として扱う。プレビューごとの実行 ID を固定し、別キーで同じプレビューを送っても再適用しない。

## 明示的 migration

- `021_profile_ontology_revisions.sql`: 旧業務 revision の Profile 所有列と公開一意性。旧 ID / artifact は保持する。
- `022_ontology_capability_transaction.sql`: `NL2SQL_ONT_ACTION_TX` package と body。システムテーブル管理の既存 migration 操作で適用する。構築・読取・能力 API から DDL を実行しない。

package は公開 pointer と binding の行をロックして更新競合を検出し、実行監査 2 種だけを artifact table へ挿入する。`AUTHID DEFINER` を使うが、通常ユーザーの業務更新は `user_data_connection()` の権限・DeepSec を維持する。package は `COMMIT` / autonomous transaction を使用しない。権限は既存 `NL2SQL_APP_DB_ROLE` に限定し、業務テーブルや artifact table の広い権限を追加しない。DeepSec の後日初期化時も package が既にあれば EXECUTE のみ設定する。[Oracle AUTHID](https://docs.oracle.com/en/database/oracle/oracle-database/18/lnpls/plsql-subprograms.html)

## 検証の扱い

通常 CI / pytest は fixture、InMemory store、Oracle 接続・transaction のテストダブルを使う。Playwright も API を mock する。これらを OCI 推論、実 Oracle migration、実 DeepSec / DML の統合成功とは扱わない。環境に適用後、専用のテスト用 Profile とテーブルで package のコンパイル、通常ユーザーの最小権限、行競合、二重送信、監査保存失敗の rollback を確認する。
