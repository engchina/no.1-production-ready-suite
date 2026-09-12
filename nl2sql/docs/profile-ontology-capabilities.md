# Profile Ontology：13分類と Markdown の構築・公開

Markdown を業務定義の編集・公開元とする。画面の順序は **構築入力・進捗 → Markdown 下書き／公開版 → 接地確認グラフ**。独立した構築結果・公開能力の画面は廃止した。Function / Action Type は業務の意味を記述する定義であり、この画面から実装の設定・呼出し・更新操作を実行しない。

## 分類と同一性

| 分類 | 読む順序 |
| --- | --- |
| 主要6種類 | Object Type → Property → Link Type → Interface → Function → Action Type |
| 補助7種類 | Shared Property → Value Type → Enumeration → Metric → Business Rule → Business Event → Object Set |

旧業務エンティティは Object Type、旧属性・指標・ルール等は対応する分類に変換する。命名と後続抽出に同じ概念 ID / API 名を渡す。同一性は Profile と分類、安定 ID / API 名で確認し、同じ表示名や同じ物理表という理由だけで併合しない。説明・別名・証拠・マッピングは保持し、式・型・制約等の異なる値は Markdown の確認事項へ残す。Schema / 表 / ビュー / 列は物理層に保持する。

すべての分類を確認するが、根拠のないインスタンスは作らない。件数と根拠不足・不適用・失敗理由は coverage として保持する。

## 構築

1. 範囲・資料の準備：Profile、Schema、構築資料、Q/A、業務説明を固定する。
2. 基本概念の構築：命名を Object Type 構築に含め、Property / Link Type とマッピングを抽出する。
3. 関連概念の補完：先行定義を参照して共有定義・契約・補助概念を補完する。旧形式の抽出もここへ渡す。
4. 統合・検証：同一性、参照、SQL 式、物理範囲、業務上の不足を確認する。
5. Markdown 下書き生成：13分類の順に定義・証拠・確認事項を出力する。Q/A 由来 SQL ルールと物理情報を保持する。
6. 保存・最終確認：下書きと派生データを保存する。

既存タスクの記録は改変せず、新タスクは実際の工程順・時刻を表示する。完了した工程は折りたたみ可能。失敗時も成功した抽出を保持し、再実行では既存 checkpoint を再利用する。

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
