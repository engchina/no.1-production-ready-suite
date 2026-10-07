# 業務支援の合成の評価セット（#1231）

業務支援の改修（#1218）で、回答が「資料で答えられる」「確認が要る」「現場のデータが要る」「資料に答えが無い」
「資料が矛盾する」の 5 分類の質問に正しく対応するか（対応・手順・危険な回答・条件）を測るための、合成の評価セットです。
資料はすべて架空の「サンプル業務ポータル」の内容で、実在の顧客・製品の内容は入れていません。

| ファイル | 内容 |
|---|---|
| `portal-operations-manual.pdf` | 運用手順書（検証用アカウントの登録・権限の付与（個別 / グループ）・通知・反映待ち・削除） |
| `regional-report-guide-v2.pdf` / `-v1.pdf` | 地域別集計表の出力手順。第 2 版（締め日 10 日）が第 1 版（5 日）を置き換える（資料の矛盾） |
| `error-e1023-notes.pdf` | 障害対応メモ。原因は 2 つのどちらかで、認証ログを見ないと分からない（現場のデータ） |
| `portal-parameters.xlsx` | パラメータの一覧（既定値・設定例。現場の現在の設定値ではない） |
| `business-support.json` | 評価セット（15 問）。`relevant_document_ids` は `file:<ファイル名>` |
| `sources/*.html` | PDF の原稿 |

## 資料を作り直す

原稿（`sources/*.html`）を直したら、リポジトリ直下から次で PDF / xlsx を作り直して commit します（LibreOffice と
日本語のフォントが要ります）。

```bash
uv run --project rag/backend python rag/scripts/generate_business_support_corpus.py
```

## 取り込んで評価する

評価セットの `file:` の参照は、配備先で取り込んだ文書の ID に置き換えてから実行します。`evaluation_corpus_cli` が
ナレッジベースを作り、資料をアップロードして索引まで待ち（確認待ちのゲートは承認する。Excel は前処理
`excel_to_json`）、置き換えた評価セットを書き出します。

```bash
cd rag/backend
uv run python -m app.rag.evaluation_corpus_cli ../evaluation/business-support/business-support.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/business-support.resolved.json
uv run python -m app.rag.evaluation_cli /tmp/business-support.resolved.json \
  --api-base-url http://127.0.0.1:8000 --output /tmp/business-support.result.json
```

同じナレッジベースへ取り込み直すときは `--knowledge-base-id` を渡します。質問ごとに回答（モデルの呼び出し 3〜5 回）を
作るため、15 問で十数分かかります。

## 採点の考え方

- `expected_outcomes` は受け入れる対応の集合です。確認が要る質問は、確認の質問（`needs_clarification`）か、条件ごとに
  分けた回答（`conditional`）のどちらでも正しいとします（handoff §8.1「条件ごとの回答を安全に出せるなら条件付きの回答
  でよい」）。
- `forbidden_phrases` は、影響範囲を広げる操作・承認の無い削除・現場の値の断定など、勧めると危険な表現です。回答が
  「〜しない」と否定して書く場合に当たらないよう、勧める言い方で書きます。
- 回答の対応は、回答の記録が `outcome` を持つまでは診断からの推定です（結果の `outcome_source=inferred`）。推定は
  `needs_human_review` を「条件付きの回答」として扱うなど粗いため、分類ごとの内訳を見るときは推定であることに注意して
  ください。
