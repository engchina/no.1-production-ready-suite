# 多段の質問（multi-hop）の合成の評価セット（#1335）

複数の根拠をつないで初めて答えられる質問（多段の質問）を、日本語の業務文書（規程・手順書・表）で測るための合成の
評価セットです。RAG の内部に実体（システム・部署・役職・規程）の層と検索時の 1 段の拡張を入れるべきか、それとも
業務 Agent が RAG の MCP で多段に読めば足りるかを、この評価の結果で決めます（下の「実体の層を入れるかの判断」）。
資料はすべて架空の「サンプル社」の内容で、実在の顧客・製品の内容は入れていません。

| ファイル | 内容 |
|---|---|
| `system-ledger.xlsx` | システム台帳（システム ID・正式名・略称 / 別表記・担当部署の略号（漢字 1 文字）・重要度・機密区分。8 行） |
| `organization-rules.pdf` | 組織規程（部署の略号と正式名・通称、部署の承認者、承認者の代理） |
| `approval-rules.pdf` | 承認規程（承認者の役職ごとの承認の期限、緊急の変更、期限を過ぎたとき、承認の記録） |
| `change-procedure.pdf` | システム変更手順書（申請先、申請書の項目、重要度ごとの作業の時間帯、利用者への告知） |
| `maintenance-plan.pdf` | 定期保守計画（システムごとの保守枠と、共通の保守枠） |
| `incident-contact-rules.pdf` | 障害連絡規程（重要度ごとの最初の連絡・復旧の目標、連絡の手段） |
| `data-retention-rules.pdf` | データ保管規程（機密区分ごとの保管期間、閲覧の記録、データの消去） |
| `multi-hop.json` | 評価セット（33 問。dev 20 問・holdout 13 問）。`relevant_document_ids` と `required_evidence[].document_id` は `file:<ファイル名>` |
| `sources/*.html` | PDF の原稿 |
| `sources/system-ledger.workbook.json` | `system-ledger.xlsx` の原稿（シートの前書き・表頭・行） |

## 資料に入れた実体のつながり

資料をまたいで実体をたどらないと答えられないように、つながりを意図して入れています。

```text
システム（正式名・略称）
  └─ システム台帳: 担当部署の略号（「経」など漢字 1 文字）・重要度・機密区分
       ├─ 組織規程 第 2 章: 略号 → 部署の正式名・所属の本部
       │    └─ 組織規程 第 3 章: 部署 → 承認者の役職（経理部は部長ではなく管理本部長）
       │         ├─ 承認規程 第 2 章: 役職 → 承認の期限（部長 3 営業日・本部長 5 営業日・担当役員 7 営業日）
       │         └─ 組織規程 第 4 章: 承認者の代理（所属の本部の本部長 → 担当役員）
       ├─ 障害連絡規程: 重要度 → 最初の連絡の期限・連絡先・復旧の目標
       ├─ システム変更手順書 第 4 章: 重要度 A は定期保守の時間帯だけ → 定期保守計画の保守枠
       └─ データ保管規程: 機密区分 → 保管期間
```

- **実体の表記ゆれ**（実体の名寄せの効果を測るため）:
  - 全角 / 半角: 台帳は `HRM`・`OMS`・`SYS-104`、定期保守計画・障害連絡規程は `ＨＲＭ`・`ＯＭＳ`・`ＳＹＳ－１０４`。
  - カナ / 英字: 台帳の正式名は「ドキュメントポータル」、定期保守計画は「Document Portal」。
  - 略称 / 正式名: 「経費精算ポータル」と「経費 Portal」（システム変更手順書は略称だけ）、「情報システム部」と「情シス」
    （障害連絡規程は「情シス部長」）。
  - 1 文字の漢字の部署名: 台帳の担当部署は「情」「経」「人」「総」「営」。正式名は組織規程の第 2 章にだけある。
- **同じ文書の中の参照**: 「第 4 章を参照してください」（組織規程・承認規程）、「第 3 章を参照してください」（システム
  変更手順書・障害連絡規程・データ保管規程）、「この章に無いシステムは、第 2 章の共通の保守枠で保守します」（定期保守
  計画）。
- **表の行をたどる段**: 本文の実体（`ＨＲＭ`・`Document Portal`・`ＳＹＳ－１０４`・部署名）で台帳の行を引く。

## 質問の種類と段の数

各ケースは種類（`reasoning_type`）と段の数（`hops`。根拠をたどる回数。比べる質問は 1 つの実体あたりの回数）を
持ち、`required_evidence` に**段ごとの根拠**を全部書いています（`hops` 以上の件数）。

| 種類 | 内容 | dev | holdout |
|---|---|---|---|
| `bridge` | 橋渡し（A の属性で B を引き、B の属性で答える。2〜5 段） | 8 | 5 |
| `comparison` | 2 つの実体の属性を比べる | 3 | 2 |
| `intra_document_reference` | 同じ文書の中の参照をたどる | 4 | 2 |
| `table_lookup` | 本文の実体で表の行を引く（表の中の絞り込みを含む） | 3 | 2 |
| `single_hop` | 1 つの根拠で答えられる（対照） | 2 | 2 |

- 段の数は 1 段 6 問・2 段 17 問・3 段 3 問・4 段 6 問・5 段 1 問です。
- 区分は、dev・holdout のどちらにもすべての種類が入るように分け、同じ質問の言い換えを両方に入れていません
  （holdout は dev と同じ型の質問を、別の実体・別の資料の組み合わせで聞きます）。holdout の結果を見て評価セットを
  直したら、そのケースは dev に移します。
- 必要な根拠の語句は、指した資料の原稿に実際に書いてある文です（テストが原稿と同梱の PDF / xlsx で照合します）。
  台帳（xlsx）の根拠は、行を指すシステム ID のセル（`SYS-101` など）にしています。前処理 `excel_to_json` は 1 行を
  1 記録にするため、その行を引用で取れたかを測れます。
- 対応（`expected_outcomes`）は、すべて答えられる質問なので `answered` / `conditional` を受け入れます（拒答は失敗）。
  期待する語（`expected_answer_keywords`）は NFKC・大小文字・空白を無視して照合します（「ＨＲＭ」と「HRM」、
  「5 年」と「5年」は同じ）。

## 指標

[../README.md](../README.md) の「評価の指標」に加えて、次を見ます（どちらも参考の集計で、閾値の判定には使いません）。

| 指標 | 意味 |
|---|---|
| `required_evidence_recall` | 必要な根拠のうち、最後の回答の引用で取れた割合（ケースの平均） |
| `evidence_chain_complete_rate` | 必要な根拠を**すべて**取れたケースの割合（多段は 1 つでも欠けると答えられないため、再現率の平均とは別に見る） |
| `reasoning_type_breakdown` / `hops_breakdown` | 種類別・段の数別の `required_evidence_recall`・`evidence_chain_complete_rate`・`answer_keyword_hit_rate` と件数。`split_breakdown` の各区分の中にも同じ内訳がある |

## 資料を作り直す

原稿（`sources/*.html` と `sources/system-ledger.workbook.json`）を直したら、リポジトリ直下から次で PDF / xlsx を
作り直して commit します（PDF の生成には LibreOffice と日本語のフォントが要ります。xlsx だけなら `--xlsx-only`）。
xlsx は作成時刻を固定するため、同じ原稿からは同じセルの値・書式になります（テストが原稿から作った xlsx と同梱の
xlsx を照合します）。

```bash
uv run --project rag/backend python rag/scripts/generate_evaluation_corpus.py rag/evaluation/multi-hop
```

## 取り込んで評価する（A: RAG の回答）

業務支援の評価セット（[../business-support/README.md](../business-support/README.md)）と同じ道具を使います。
`evaluation_corpus_cli` がナレッジベースを作り、資料をアップロードして索引まで待ち（確認待ちのゲートは承認する。
Excel は前処理 `excel_to_json`）、`file:` の参照を文書 ID に置き換えた評価セットを書き出します。A は全体の既定の
設定とナレッジベースの指定で回答します。判断の基準は dev で決めるため、まず `--split dev` で流します。

```bash
cd rag/backend
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/multi-hop/multi-hop.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/multi-hop.resolved.json
uv run python -m app.rag.evaluation_cli /tmp/multi-hop.resolved.json --split dev \
  --api-base-url http://127.0.0.1:8000 --output /tmp/multi-hop.a.json
```

`evaluation_cli` は、終わったときに標準エラーへ全体の指標と、`required_evidence`・`reasoning_type=<種類>`・
`hops=<段の数>` の行（`required_evidence_recall`・`evidence_chain_complete_rate`・`answer_keyword_hit_rate`）を
出します。閾値を下回ると終了コード 1 を返しますが、結果の JSON は書きます。質問ごとに回答（モデルの呼び出し
3〜5 回）を作るため、dev の 20 問で 20 分前後かかります。

## 業務 Agent と比べる（D）

D は、同じナレッジベースを参照する検索・回答プロファイルを使う業務 Agent（スキル `business_rag_research`）の Run です。
この評価セットには業務ガイドが無いため（`evaluation_corpus_cli --guides` は業務ガイドの無いファイルを受け付けない）、
画面の「検索・回答プロファイル」で、取り込んだナレッジベース（`/tmp/multi-hop.resolved.json` の
`knowledge_base_ids`）だけを参照するプロファイルを作って公開し、その ID を渡します。

```bash
cd rag/backend
uv run python -m app.rag.agent_evaluation_cli run /tmp/multi-hop.resolved.json --split dev \
  --agent-api-base-url http://127.0.0.1:8020 --create-agent \
  --search-answer-profile-id <同じナレッジベースを参照するプロファイル> --output /tmp/multi-hop.d.json
# 比べる表（種類別・段の数別・区分ごとの種類別の根拠の連鎖の完全率の行を含む）
uv run python -m app.rag.agent_evaluation_cli summarize A=/tmp/multi-hop.a.json D=/tmp/multi-hop.d.json
```

- D の引用は、その Run の `rag_search` / `rag_retrieve_evidence` が返した根拠で、本文は MCP の抜粋（最大 1000 文字）
  です。必要な根拠はその範囲で照合します（業務支援の評価と同じ）。
- 文書の構成・続きを読む MCP のツール（#1332）がある Agent では、ツールの有る / 無いの 2 回を流して D と D' として
  比べます。

## 実体の層を入れるかの判断

この評価の結果で、別の Issue として決めます（#1335 に記録する）。

- A と D の両方で、dev の `bridge` の `evidence_chain_complete_rate` が低く（基準は dev の結果を見て決め、#1335 に
  記録する）、取れなかった根拠（`missing_evidence`）が「2 段目以降の根拠が検索に出ない」ものなら、実体の層の Issue を
  作ります。
- 1 段の対照（`single_hop`）も低いときは、多段ではなく検索そのものの問題として先に切り分けます。表記ゆれ（全角 /
  半角・略称）を含むケースだけが低いときは、名寄せ（NFKC・別名）の効果を見込めます。

## nightly で流さない理由

業務支援の評価セットと同じく、配備先に資料を取り込み（parser と OCI / Oracle が要る）、`file:` の参照を文書 ID に
置き換えてからでないと採点できず、回答も 1 問 3〜5 回のモデルの呼び出しがかかるため、nightly には入れず手動 /
ステージングで流します。
