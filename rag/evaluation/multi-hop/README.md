# 多段の質問（multi-hop）の合成の評価セット（#1335・#1352）

複数の根拠をつないで初めて答えられる質問（多段の質問）を、日本語の業務文書（規程・手順書・表）で測るための合成の
評価セットです。RAG の内部に実体（システム・部署・役職・規程）の層と検索時の 1 段の拡張を入れるべきか、それとも
業務 Agent が RAG の MCP で多段に読めば足りるかを、この評価の結果で決めます（下の「実体の層を入れるかの判断」）。
資料はすべて架空の「サンプル社」（と子会社の「サンプル物流社」）の内容で、実在の顧客・製品の内容は入れていません。

資料（原稿）と評価セットは、実体（部署・システム・保守枠など）のデータから
[`rag/scripts/multi_hop_corpus.py`](../../scripts/multi_hop_corpus.py) が決定的に作ります（#1352）。原稿の文と、
評価セットの正解（期待する語）・必要な根拠（`required_evidence`）は同じデータから作るため、資料を増やしても正解と
根拠の文がずれません。手で原稿や `multi-hop.json` を直さず、このファイルを直して作り直します（下の「資料を作り直す」）。

| ファイル | 内容 |
|---|---|
| `system-ledger.xlsx` | システム台帳（システム ID・正式名・略称 / 別表記・担当部署の略号（漢字 1 文字）・重要度・機密区分。80 行） |
| `organization-rules.pdf` | 組織規程（19 部署の略号と正式名・所属の本部・通称、部署の承認者、承認者の代理） |
| `approval-rules.pdf` | 承認規程（承認者の役職ごとの承認の期限、緊急の変更、期限を過ぎたとき、承認の記録） |
| `change-procedure.pdf` | システム変更手順書（申請先、申請書の項目、重要度ごとの作業の時間帯、利用者への告知） |
| `maintenance-plan.pdf` | 定期保守計画 2026年度（システムごとの保守枠 8 件と、共通の保守枠） |
| `incident-contact-rules.pdf` | 障害連絡規程（重要度ごとの最初の連絡・復旧の目標、本部長にも連絡するシステム、連絡の手段） |
| `data-retention-rules.pdf` | データ保管規程（機密区分ごとの保管期間、閲覧の記録、データの消去） |
| `access-request-procedure.pdf` | 利用権限の申請手順（機密区分ごとの権限の承認者、権限の見直し。情報セキュリティ部は通称「情セキ」で書く） |
| `backup-rules.pdf` | バックアップ規程（重要度ごとの頻度と世代、復元） |
| `system-operation-guide.pdf` | システム運用要領（台帳の 80 システムの章。利用時間・連携元と受け取るデータ・運用の注意） |
| `approval-rules-2023.pdf` | **旧版**の承認規程（2023年度版。廃止済み。期限・記録の保管が今の版と違う） |
| `maintenance-plan-2025.pdf` | **旧版**の定期保守計画（2025年度。今の計画と同じシステムで別の時間帯） |
| `purchase-approval-rules.pdf` | **似た承認の規程**（購買の申請。システムの変更の申請とは別の承認者・期限） |
| `logistics-organization-rules.pdf` | **別の会社**（サンプル物流社）の組織規程（略号「経」「総」「情」がサンプル社と衝突する） |
| `logistics-system-ledger.xlsx` | **別の会社**のシステム台帳（20 行。略称 `OMS`・`BMS`・`DMS` がサンプル社と衝突する） |
| `multi-hop.json` | 評価セット（69 問。dev 40 問・holdout 29 問。先頭の 33 問は #1335 のまま）。`relevant_document_ids` と `required_evidence[].document_id` は `file:<ファイル名>` |
| `sources/*.html` | PDF の原稿（`multi_hop_corpus.py` が作る） |
| `sources/*.workbook.json` | xlsx の原稿（シートの前書き・表頭・行。`multi_hop_corpus.py` が作る） |

### 資料の大きさ

既定の検索（`top_k` = 20）で資料の全部が文脈に入らない大きさにしています（#1335 は 7 文書・31 chunk で、A の文脈が
全ケースで資料の全部になり、`evidence_chain_complete_rate` が天井だった）。

- 子 chunk は 241（PDF 139・xlsx 102）。PDF は Docling の解析と既定の親子階層の分割での実測（ローカルの Docling
  サービスで確認）。xlsx は Excel を 1 行 = 1 chunk に分ける #1349 の後の数（行と前書きの 1 つ）で、#1349 の前は
  複数の行が 1 つの chunk になるため少なくなります（それでも PDF だけで `top_k` の 7 倍）。
  `multi_hop_corpus.estimate_chunks` が同じ数を見積もり、テストが 200 以上を確かめます。
- PDF の大半は運用要領（82）で、ほかの規程は章ごとに 1 chunk（4〜6）です。台帳の行（計 100）と運用要領の章が、
  橋渡しの実体（システム）をまぎらわしくします。

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
       ├─ バックアップ規程: 重要度 → 頻度と世代
       ├─ システム変更手順書 第 4 章: 重要度 A は定期保守の時間帯だけ → 定期保守計画の保守枠
       ├─ データ保管規程: 機密区分 → 保管期間
       └─ 利用権限の申請手順: 機密区分 → 権限の承認者（極秘は情セキ部長も → 組織規程の通称）
システム運用要領: 連携先 → 連携元のシステム（→ 台帳 → 組織規程）
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
- **まぎらわしい実体と資料**（#1352。上位の検索に紛れ込み、橋渡しを取り違えさせるため）:
  - 名前の似たシステム: 「経費精算ポータル」と「交通費精算ポータル」、「人事評価システム」と「人材評価分析システム」、
    「受発注管理システム」と「受注分析システム」・「発注管理システム」など。
  - 名前の似た部署: 経理部と財務部、人事部と労務部、情報システム部と情報セキュリティ部、営業企画部と営業推進部。
  - 略称の衝突: 台帳の中で `PMS`（購買管理・案件管理）と `TMS`（配送管理・資金管理）が 2 つずつ。サンプル物流社の
    `OMS`・`BMS`・`DMS` と部署の略号「経」「総」「情」がサンプル社と同じ。
  - 旧版（2023年度の承認規程・2025年度の定期保守計画）と、似た承認の規程（購買承認規程）。
  - 資料を増やしても #1335 の問の正解が変わらないこと（例: 重要度 A かつ極秘のシステムは今も 2 つ、情報システム部が
    担当するシステムは 1 つ）は、`multi_hop_corpus.py` が作るときに確かめます（変わると作らずに止まる）。

## 質問の種類と段の数

各ケースは種類（`reasoning_type`）と段の数（`hops`。根拠をたどる回数。比べる質問は 1 つの実体あたりの回数）を
持ち、`required_evidence` に**段ごとの根拠**を全部書いています（`hops` 以上の件数）。

| 種類 | 内容 | dev | holdout |
|---|---|---|---|
| `bridge` | 橋渡し（A の属性で B を引き、B の属性で答える。2〜5 段） | 19 | 14 |
| `comparison` | 2 つの実体の属性を比べる（略称が衝突する 2 つ・旧版との比較を含む） | 6 | 5 |
| `intra_document_reference` | 同じ文書の中の参照をたどる | 6 | 3 |
| `table_lookup` | 本文の実体で表の行を引く（表の中の絞り込み・別の会社の台帳を含む） | 6 | 4 |
| `single_hop` | 1 つの根拠で答えられる（対照） | 3 | 3 |

- 段の数は 1 段 12 問・2 段 29 問・3 段 12 問・4 段 13 問・5 段 3 問です。
- 先頭の 33 問は #1335 のまま（ID・質問・期待する語・根拠の文を変えていない）で、#1335 の結果と比べられます。
  #1352 で足した 36 問（dev 20・holdout 16）は、足した実体（名前の似たシステム・部署、略称の衝突、表記ゆれ、旧版・
  別の会社）を使います。
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

実体・文・問を変えるときは `rag/scripts/multi_hop_corpus.py` を直し、リポジトリ直下から次で原稿・評価セット・PDF /
xlsx をまとめて作り直して commit します（`multi-hop` のフォルダは、変換の前に原稿と `multi-hop.json` を作ります。
PDF の生成には LibreOffice と日本語のフォントが要ります）。

```bash
uv run --project rag/backend python rag/scripts/generate_evaluation_corpus.py rag/evaluation/multi-hop
# 原稿と評価セットだけ（PDF / xlsx は作らない）
uv run --project rag/backend python rag/scripts/generate_evaluation_corpus.py rag/evaluation/multi-hop --sources-only
```

- 作り直しは決定的です。テストが、`multi_hop_corpus.py` から作った原稿・評価セットが同梱のものとバイト単位で同じこと、
  原稿から作った xlsx が同梱の xlsx と同じセルの値・書式であること（xlsx は作成時刻を固定する）を確かめます。
- 原稿が変わらなかった PDF は、作り直すとバイトが変わる（作成時刻が入る）ため、`git checkout` で戻して差分を原稿が
  変わったものだけにします。

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
3〜5 回）を作るため、dev の 40 問で 40 分前後かかります（#1335 の 33 問の実測から見積もり）。

## 実体の層の有り / 無しで比べる（#1362）

実体の層（文書レシピの任意の処理「実体の抽出」と、回答の検索の 1 段の拡張。
[../../docs/rag-engine.md](../../docs/rag-engine.md) の `RAG_ENTITY_INDEX_ENABLED`）の効果は、同じ資料を
**別のナレッジベース**に、レシピの有り / 無しで取り込んで A を流して比べます（同じ文書を両方に入れると、実体を
持つ文書が両方の検索範囲に入るため）。

```bash
cd rag/backend
# レシピ無し（既定のレシピ。上の「取り込んで評価する」と同じ）
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/multi-hop/multi-hop.json \
  --api-base-url http://127.0.0.1:8000 --knowledge-base-name "評価: 多段 実体なし" \
  --output /tmp/multi-hop.plain.json
# レシピ有り（すべての文書のレシピで実体の抽出を選ぶ）
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/multi-hop/multi-hop.json \
  --api-base-url http://127.0.0.1:8000 --knowledge-base-name "評価: 多段 実体あり" --entity-index \
  --output /tmp/multi-hop.entity.json
for variant in plain entity; do
  for split in dev holdout; do
    uv run python -m app.rag.evaluation_cli /tmp/multi-hop.$variant.json --split $split \
      --api-base-url http://127.0.0.1:8000 --output /tmp/multi-hop.a.$variant.$split.json
  done
done
```

- 見る値: dev の `bridge` の `evidence_chain_complete_rate`（#1362 の完了条件は 0.8 以上）と、1 段の対照
  （`single_hop`）が下がらないこと。足した根拠は回答の診断の `entity_expansion`（足した chunk・実体・段・起点・
  `ambiguous`）で確かめます。
- 同じ実体のナレッジベースで拡張だけを外して比べるときは、品質評価の `rag_overrides` の
  `entity_expansion_enabled: false` を使います。
- 決定論の確認（CI）: `rag/backend/tests/test_entity_layer.py` が、この資料の原稿から作った chunk で、bridge の
  全問の台帳の行・組織規程の略号と承認者の根拠が 1 段の拡張で足されることを確かめます（上位の候補には、台帳・
  組織規程以外の必要な根拠の chunk を置く）。取れないのは、運用要領の連携元から 2 段たどる 1 件
  （`br-wms-link-department` の受発注管理システムの行）です。組織規程の第 4 章（承認者の代理）は実体では
  つながらない（同じ文書の中の参照）ため、実体の層の対象にしていません。

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

この評価の結果で、別の Issue として決めます（#1335 に記録する）。#1335 の 1 回目の評価（33 問・31 chunk）では、A の
文脈が資料の全部になって天井だったため判断せず、先に MCP の根拠の並び（#1348）・Excel の 1 行 1 chunk（#1349）・
全角 / 半角の名寄せ（#1336・#1350）・評価セットを大きくする（#1352。この資料）を行ってから、同じ評価で判断し直す
ことにしました。

- **判断し直す条件**: 上の後に、この大きさの資料（241 chunk）で dev の `bridge` の `evidence_chain_complete_rate` が
  A（既定の `top_k` = 20）と D の両方で 0.8 未満で、欠けた根拠（`missing_evidence`）の過半が、同じ質問のかけ直しで
  「検索の結果（citations）に出ない」（`evidence_limit` で切れたのではない）ものなら、実体の層の Issue を作ります。
- 資料の全部が文脈に入らないため、A の `bridge` が 1.00 にならないことを先に確かめます（1.00 なら資料がまだ小さい）。
- #1335 の 33 問だけの値（先頭の 33 問）も出し、1 回目の評価と比べます。増えた資料で #1335 の問だけが下がったなら、
  まぎらわしい実体（似た名前・略称の衝突・旧版）に検索が引かれています。
- 1 段の対照（`single_hop`）も低いときは、多段ではなく検索そのものの問題として先に切り分けます。表記ゆれ（全角 /
  半角・略称）を含むケースだけが低いときは、名寄せ（NFKC・別名）の効果を見込めます。

## nightly で流さない理由

業務支援の評価セットと同じく、配備先に資料を取り込み（parser と OCI / Oracle が要る）、`file:` の参照を文書 ID に
置き換えてからでないと採点できず、回答も 1 問 3〜5 回のモデルの呼び出しがかかるため、nightly には入れず手動 /
ステージングで流します。
