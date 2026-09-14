# Profile Ontology：13分類と Markdown の構築・公開

Markdown を業務定義の編集・公開元とする。画面の順序は **構築入力・進捗 → Markdown 下書き／公開版 → 接地確認グラフ**。独立した構築結果・公開能力の画面は廃止した。Function / Action Type は業務の意味を記述する定義であり、この画面から実装の設定・呼出し・更新操作を実行しない。

## 分類と同一性

| 分類 | 読む順序 |
| --- | --- |
| 主要6種類 | Object Type → Property → Link Type → Interface → Function → Action Type |
| 補助7種類 | Shared Property → Value Type → Enumeration → Metric → Business Rule → Business Event → Object Set |

旧業務エンティティは Object Type、旧属性・指標・ルール等は対応する分類に変換する。新規生成は型付き `definitions` を正本とし、旧形式への二重出力を要求しない。同一性は Profile と分類、安定 ID / API 名で確認し、同じ表示名や同じ物理表という理由だけで併合しない。説明・別名・証拠・マッピングは保持し、式・型・制約等の異なる値は Markdown の確認事項へ残す。Schema / 表 / ビュー / 列は物理層に保持する。

すべての分類を確認するが、根拠のないインスタンスは作らない。件数と根拠不足・不適用・失敗理由は coverage として保持する。

## 構築

1. 範囲・資料の準備：Profile、Schema、構築資料、Q/A、業務説明を固定する。
2. 関連概念の構築：各入力バッチから主要6種類・補助7種類をまとめて抽出する。共有定義・能力契約を別工程で再生成しない。
3. 統合・検証：同一性、参照、SQL 式、物理範囲、業務上の不足を確認する。
4. Markdown 下書き生成：13分類の順に定義・証拠・確認事項を出力する。Q/A 由来 SQL ルールと物理情報を保持する。
5. 保存・最終確認：下書きと派生データを保存する。

Q/A 由来の Link Type は、JOIN 条件が参照する物理列を SQL AST で解決し、同一バッチ内の同じ Q/A SQL に存在することを統合前に検証する。表別名・CTE・引用識別子を考慮し、根拠を確認できない候補だけを除外して対象定義と理由を警告へ残す。SQL 条件式全体の意味同値を判定するものではない。資料・スキーマからの独立した抽出結果は保持する。

Q/A の補助的な `DUAL` / `SYS.DUAL` 参照は SQL の名前解決に限って扱い、そのシステム列を業務列の根拠には追加しない。Profile の同名業務表と CTE は元の名前解決を維持し、未知の表・列は引き続き拒否する。同一入力の追加抽出で空の JOIN 条件を補完できた場合、未補完版も根拠・別名・他フィールドの競合を保って統合し、補完済み空欄の拒否警告は生成しない。非空の不正な条件や未補完の関係に対する警告は保持する。

取りこぼし確認は既存設定 `nl2sql_ontology_extraction_gleaning_passes` に従い、既定で最大1回行う。入力上限内で未抽出候補・不足フィールドのみを補い、全文を再生成しない。資料分割、再試行、キャンセル、部分成功は維持する。

既存タスクの記録・警告は改変せず、旧 `objects/shared/capabilities` を「関連概念の構築」に統合表示する。新タスクのイベントには工程識別子を記録し、5段階の件数・概念一覧・処理ログを同じ工程に集約する。完了した工程は折りたたみ可能。失敗時も成功した抽出を保持し、再実行では既存 checkpoint を再利用する。

`title_property` は空文字を許す任意項目である。JSON のキー出力要件と業務上の値の必須要件を区別し、根拠がない空欄を資料不足にしない。設定時は参照先の存在・Property 型・所属オブジェクトを検証する。主識別子・物理マッピング等の実際の不足や不正参照は対象定義とフィールドを付けて報告する。修正した生成指示は新規構築・再構築から適用し、保存済み警告・公開済み定義は保持する。

## Markdown の公開

- 下書きを保存して `POST /profiles/{profile_id}/ontology-markdown/prepare` に ETag と `Idempotency-Key` を渡す。
- 準備タスクは Markdown、元 revision、Profile / Schema の fingerprint、期待する公開 head を固定する。OCI Enterprise AI が全行の扱いと13分類を解析する。未解析行、解釈できない行、概念の欠落、競合、静的検証エラーがあれば公開できない。
- `GET .../preparations/{preparation_id}` で進捗、定義ごとの増減・前後差分、行位置と検査事項を確認する。必要に応じて `POST .../preparations/{preparation_id}/validate-data` で標本データと受入テストを検証する。
- 確認ダイアログは Profile と実際の公開版を示す。`POST .../publish` は preparation ID、draft ETag、expected head、確認フラグ、冪等キーを必須とする。確認後に LLM を再実行しない。
- Markdown、構造化定義、graph、OWL / SHACL / 検証結果を一つの immutable snapshot に保存し、draft と head の CAS を含む同一 transaction で公開 pointer を更新する。失敗時は旧 head を維持する。
- 公開成功後は Markdown とグラフを再取得する。公開後の編集は次の下書き版を作り、以前の公開本文と成果物を書き換えない。

準備後に Markdown / Profile / Schema / 公開 head が変わった場合は再確認する。公開応答が失われた場合は `GET .../publication-outcome?key=...` で照会し、自動再送しない。準備タスクの再開時に解析済み結果を再利用し、中断して結果のない LLM 呼出しは黙示再送しない。

## SQL とグラフ

通常生成、非同期 job、引導式 session、サーバー接地検索は Profile の同じ公開 snapshot を使用する。job / session の `business_release_id` は snapshot ID を保持する。以前の独立 release を新しい生成へ重ねない。過去の release / job の読み取りは互換性のため残す。

引導式生成の正式指標には `expression_sql` と指標固有の `filter_sql` を渡す。絞り込み条件はその指標の集計対象だけに適用し、他の指標の全体 WHERE 条件へ流用しない。過去の読み取り投影に条件がない場合は、同じ snapshot の完全な型付き定義から復元する。保存済み artifact は書き換えない。

Interface の接地は下位の継承・実装を辿る。子 Interface の検索で上位だけを実装する object や兄弟 Interface の実装へ広げず、上位 Interface の検索では配下の実装を含める。Profile の node / edge 範囲は各段階で守る。

グラフには12種類の型付きノードと Link Type の選択可能な関係辺を投影する。Markdown と概念 ID を共有し、定義の説明・型固有フィールド・マッピング・証拠を詳細に表示する。「概念の種類」は主要6／補助7にまとめ、存在しない種類は該当なしとする。全体／接地パス／物理 ER を維持し、Function / Action の詳細に実行ボタンを出さない。物理マッピングを持たない概念は参照先のオブジェクト付近へ配置する。

## 既存データの移行と廃止 API

`POST .../migration-preview` は既存の定義を現在の Markdown へ取り込む内容を返す。`POST .../migrate` は preview ID と ETag を確認して下書きへ反映する。手書き本文と競合を残し、自動公開しない。概念 ID と移行マーカーにより再適用・応答喪失後の追跡で本文を重複追加しない。草稿がなければ新しい草稿を作る。過去の artifact は更新しない。

独立した定義の編集・解析・適用・レビュー・検証・公開・切戻し、能力 binding / invoke / preview / execute の HTTP mutation は認証・Profile アクセス検査後に `410 Gone` を返す。歴史的な構築結果・公開履歴・実行結果の GET は読み取り専用で残す。

Markdown 草稿と保存基準、表示タブ、業務説明、準備 ID、公開結果の照会キーはユーザー・DB・Profile を分離して同じタブに一時保存する。共通の有効期限・保存容量制限・離脱ガードを適用する。旧能力の草稿を実行操作として復元しない。
