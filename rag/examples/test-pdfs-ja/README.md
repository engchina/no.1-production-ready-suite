# 日本語の合成 PDF とテスト質問

RAG と業務 Agent の検索・回答を確かめるための合成資料です。PDF の本文、表、条件、確認番号はすべて架空で、実際の業務や運用条件を示しません。

10 件の PDF は合計 110 ページです。グループ A・B はそれぞれ 5 件、55 ページで、各グループの質問は 15 件です。

## ファイル

| グループ | PDF | 形式 | ページ数 |
|---|---|---|---:|
| A | [テスト_01_運用手順書.pdf](テスト_01_運用手順書.pdf) | 手順書 | 5 |
| A | [テスト_02_操作FAQ.pdf](テスト_02_操作FAQ.pdf) | FAQ | 8 |
| A | [テスト_03_月次稼働報告.pdf](テスト_03_月次稼働報告.pdf) | 表形式の報告 | 10 |
| A | [テスト_04_障害対応フロー.pdf](テスト_04_障害対応フロー.pdf) | 手順とフロー図 | 12 |
| A | [テスト_05_設備保守台帳.pdf](テスト_05_設備保守台帳.pdf) | 台帳 | 20 |
| B | [テスト_06_文書登録ガイド.pdf](テスト_06_文書登録ガイド.pdf) | 手順書 | 5 |
| B | [テスト_07_検索FAQ.pdf](テスト_07_検索FAQ.pdf) | FAQ | 8 |
| B | [テスト_08_品質評価報告.pdf](テスト_08_品質評価報告.pdf) | 表形式の報告 | 10 |
| B | [テスト_09_公開承認フロー.pdf](テスト_09_公開承認フロー.pdf) | 手順とフロー図 | 12 |
| B | [テスト_10_資料仕様一覧.pdf](テスト_10_資料仕様一覧.pdf) | 仕様一覧 | 20 |

- [questions.md](questions.md)：日本語の質問 30 件と期待する回答、PDF のページへのリンク。
- [questions.json](questions.json)：同じ質問の構造化データ。回答の要点、判断、出典と引用断片を含みます。
- [manifest.json](manifest.json)：PDF の名前、グループ、形式、ページ数、SHA-256。

## 質問の使い方

1. A・B のナレッジベースを作り、各グループの PDF を 5 件ずつ登録します。
2. 文書の処理を始め、全件の embedding と索引構築が完了し、検索可能であることを確認します。
3. 検索・回答プロファイルをグループごとに作り、それぞれ対応する KB 1 件だけを参照させます。
4. Agent で試す場合は `business-rag-research` スキルを選び、`data_scopes.rag.profile_ids` と `default_profile_id` に対応するプロファイルだけを設定して公開します。
5. A の質問は A のプロファイル／Agent、B の質問は B のプロファイル／Agent に入力します。各質問は新しい会話で実行し、期待する回答は入力に含めません。

条件検索、表の数値・計算、フローの分岐、操作の完了判定、例外・否定条件、文書間の照合を含みます。`test-a-15` は資料に個人名・メールアドレスがない場合、`test-b-15` は別グループの情報を求めた場合の確認です。この 2 件は回答の保留を期待し、氏名・連絡先・日数を推測して補わないことを確認します。両グループをまとめて検索すると、検索範囲の分離を検証できません。

期待する回答は意味を照合するための基準です。空白、全角・半角、曜日や時刻の表記が違っても、事実・単位・条件が一致すれば許容します。数値だけが一致していても、章・期間・項目や対象グループを取り違えた回答は正解にしません。

`sources` は期待する回答を裏付ける出典です。ページは先頭を 1 とする物理ページで、`evidence_quotes` はそのページの本文・表から抜き出した断片です。PDF の改行や空白は正規化して照合します。通常の質問では回答の要点を裏付ける引用を確認し、文書間の照合を求める `test-b-14` は両方の文書を確認します。資料不足の質問は `sources` を空にしています。

## JSON の扱い

`questions.json` は環境に依存しない質問集で、評価 API にそのまま渡す request ではありません。実環境の ID は含みません。

| 項目 | 意味 |
|---|---|
| `id` | 質問を識別する論理キー |
| `group` | 実行先のグループ A / B |
| `primary_document` | 質問の主な対象。各 PDF に 3 件ずつ割り当て |
| `type` | 確認する検索・回答の性質 |
| `question` | 入力する日本語の質問 |
| `answerable` | 指定したグループの資料で回答できるか |
| `expected_response_decision` | 期待する判断。`answer` 28 件、`hold` 2 件 |
| `expected_answer` | 期待する回答 |
| `required_answer_points` | 回答で確認する必須の要点 |
| `sources` | PDF の名前、ページ、根拠となる引用断片 |

既存の評価形式へ変換する場合は、`question` を質問欄に、`expected_answer` を標準回答欄に設定します。ファイル名はアップロード後の文書 ID に解決し、A・B に分けて対応するプロファイルまたは Agent で実行してください。RAG の評価形式は [evaluation/README.md](../../evaluation/README.md) を参照してください。

この変更では質問と原本を保存します。KB・プロファイル・Agent の構成定義と再構築コマンドは [#2905](https://github.com/engchina/no.1-production-ready-suite/issues/2905) で扱います。

## 原本の確認

このディレクトリで次を実行すると、PDF の SHA-256 を確認できます。Python の標準ライブラリだけを使います。

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path.cwd()
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
for document in manifest["documents"]:
    path = root / document["file"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != document["sha256"]:
        raise SystemExit(f"原本が一致しません: {path.name}")
print("PDF 10 件の SHA-256 が一致しました。")
PY
```
