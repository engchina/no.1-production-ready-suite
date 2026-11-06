# parser-unstructured

Unstructured を独立したプロセス(サービスごとの uv の venv)で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- unstructured のバージョンは本サービス単独で upgrade 可能。extra は残す形式の分（`pdf,image,docx,pptx,xlsx,csv,tsv,md`）だけで、
  `all-docs` にしない（#3351）
- 入れない依存（ライセンスの理由。#3351）: `pi-heif`（同梱の libheif / libde265 が LGPL-3.0）と `pypandoc-binary`（同梱の pandoc が
  GPL-2.0+）。`pyproject.toml` の `exclude-dependencies` で外す。そのため次の形式は Unstructured では原本を読まない:
  - HEIC / HEIF・EPUB: アップロードで止める（画像は JPEG / PNG、EPUB は PDF に変換してからアップロードする）
  - ODT / RTF: 処理レシピのファイル準備で Office→PDF を選び、PDF にしてから読む
  - reStructuredText（`.rst`）・Org（`.org`）: pandoc を通さず、プレーンテキストとして読む
- unstructured の PDF・画像の解析（`unstructured/partition/pdf.py`）は module の先頭で `pi_heif` を import する。pi-heif が無いときは
  `rag_parser_core.unstructured_compat` が何もしない `register_heif_opener` の stub を置く（HEIC / HEIF は Pillow が開けないまま）
- OS 依存(poppler / tesseract)は host に入れる(`rag/init_script.sh` / `rag/docs/deployment.md`)

## 原本の位置の記録（#4100）

Unstructured は要素に原本の文字位置を持たせないので、要素の分け方を変えずに、同じ原本から位置を別に記録して
`parser_artifacts["source_map"]` に置く（`rag_parser_core.unstructured_source_map`）。1 つの表示範囲（8192 文字）を超える
テキスト系の原本だけが対象。位置の記録は Unstructured の公開の関数だけを使い、
区切りごとの結果が全体の解析と合うことを確かめる。版を上げて結果が合わなくなると位置を記録しないだけで、解析は止まらない。版を上げるときは
`tests/test_source_map_real.py`（CI の `RAG / Parser unstructured`）で確かめる。

## モデル（配備で取得・検証し、解析の要求の時には取得しない。#4555）

Unstructured はモデルが無いと解析の要求の時に外部から取得し、venv（spaCy）と `~/.cache/huggingface`（HF）に書き込む。
外に出られない環境・venv に書けないサービスのユーザーでは解析が失敗するため、モデルは配備で揃え、実行時は取得しない。

| モデル | 使う処理 | 配備での取得と検証 | 実行時 |
|---|---|---|---|
| spaCy `en_core_web_sm`（unstructured が固定する版） | テキストの要素の分類（`.txt`・Markdown・HTML・Office など） | `pyproject.toml` の URL の依存。`uv.lock` の SHA-256 で `uv sync --locked` が検証して入れる | 取得の関数を明確なエラーに置き換える（`app/main.py`） |
| HF `unstructuredio/yolo_x_layout`・`microsoft/table-transformer-structure-recognition` | PDF・画像の `hi_res` の layout と表の構造 | `rag_parser_core.unstructured_models` が固定の commit とファイルのハッシュで取得・検証し、HF のキャッシュの `refs/main` をその commit にする | unit の `HF_HUB_OFFLINE=1` でキャッシュだけを読む |

- 配備（`rag/init_script.sh`・`scripts/rag-services.sh sync`）が、サービスの実行ユーザーで `.venv/bin/python -m rag_parser_core.unstructured_models` を実行する
  （検証と原子的な取得の手順は docling と共有の `rag_parser_core.model_integrity`）。取得に失敗しても配備は続き、`/health` が `degraded` になる。
  配備をやり直すと揃う。検証だけは `.venv/bin/python -m rag_parser_core.unstructured_models --check`（取得しない）。
- unstructured / unstructured-inference の版を上げるときは、spaCy のモデルの URL の版と、`rag_parser_core.unstructured_models.HUB_MODELS` の
  commit・ハッシュを合わせる。合っていなければ `tests/test_runtime_models.py`（CI の `RAG / Parser unstructured`）が失敗する。

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-parser-unstructured.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install parser-unstructured   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run parser-unstructured       # systemd の無い環境で前面に起動
```
