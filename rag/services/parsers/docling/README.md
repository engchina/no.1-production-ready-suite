# parser-docling

Docling を独立したプロセス(サービスごとの uv の venv)で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`(導入 version を返す)
- docling のバージョンは本サービス単独で upgrade 可能(他 parser / backend に非干渉)

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/parsers/docling \
  uvicorn app.main:app --host 127.0.0.1 --port 18020
```

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-parser-docling.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install parser-docling   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run parser-docling       # systemd の無い環境で前面に起動
```

## 分数と上付き・下付き（#4140）

Docling は上下に積んだ分子と分母を同じ行の字の並びにするので、解析の後に `rag_engine.parsing.math_layout` が PDF の text layer の字の位置と、ページに描かれた細い横線から構造を本文へ戻します（`1/(1-x)`・`(a+b)/(c-d)`・`x^2`・`a_1`）。モデル・追加の依存を使わず、1 ページあたり数 ms です。

- 分数: 短い横線の真上と真下に、線の幅に収まり中央にそろった字があるときだけ。下線・取り消し線・表の罫線・区切りの線は対象にしません。式が複数の record に分かれていたら最初の record へまとめます。
- 上付き・下付き: 分数を含む塊と、Docling が数式と判定した塊だけ（本文の脚注の番号を指数にしません）。
- 線の無い積み重ね（2 行の式・行列と区別できない）と、フォーム XObject の中の線と、text layer の無いスキャンは推測せず、元の本文のままです。
- Docling の公式の式の認識（`do_formula_enrichment`。CodeFormulaV2）は使いません。日本語を含む式で字が崩れ、CPU で 1 式あたり約 1 分かかるためです。

保存済みの文書には再処理（解析の工程から）するまで反映されません。

## OCR モデルの検証と復旧

Terraform の初回配備・再配備と `scripts/rag-services.sh sync parser-docling` は、モデル取得の前後に登録済み RapidOCR（ONNX・Torch など）の SHA-256 と Hugging Face モデルの hash を検証します。RapidOCR の期待値と取得元は導入済み公式 registry を使用します。版面・表・コード／数式・画像分類などの Hugging Face モデルは、取得 metadata の commit と ETag（LFS は SHA-256、Git blob は SHA-1）を使い、同じ版を検証・再取得します。metadata が無いファイルを検証済みとは扱いません。
不一致なら対象ファイルだけを再取得し、SHA-256 が一致した一時ファイルを原子的に置き換えます。通信断・誤った内容・再試行の失敗では元ファイルを残し、失敗を報告します。正常なキャッシュは再取得しません。

全キャッシュの検証／修復だけを行う場合は、サービスと同じ利用者で次を実行してください。

```bash
cd /u01/aipoc/no.1-production-ready-suite/rag/services/parsers/docling
.venv/bin/python -m rag_engine.resources.docling_models --check
.venv/bin/python -m rag_engine.resources.docling_models --repair
```

Docling の自動管理キャッシュは、converter の準備時にも検証します（OCR が OFF の場合は OCR モデルを検証しません）。明示した `DOCLING_ARTIFACTS_PATH` や独自モデルを実行時に勝手に変更しません。修復後はサービスを再起動し、失敗した文書レシピを解析工程から再実行してください。
`InvalidProtobuf` と hash 不一致だけでは、ダウンロード途中の切断かエラーページの保存かまでは確定できません。モデルのサイズ・形式・先頭バイトと取得ログで区別します。
