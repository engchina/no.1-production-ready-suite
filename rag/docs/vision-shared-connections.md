# 図の共有接続と条件の保持

図の読み取りは一回の Vision 呼出で行う。文字や形を持つ要素と、線がつながる分岐点（junction）を区別し、隣り合う要素をつなぐ接続段ごとにラベルを保存する。共有段の条件をモデルに枝ごとに書き直させない。

回答・索引へ渡す接続説明は、この保存構造から一つだけ投影する。分岐点を経由する経路でも各段のラベルを原文のまま保持し、別の枝のラベルを混ぜない。ラベルの語や条件式の意味をコードで解釈しない。

端点が不明・参照IDが不明・重複した接続は、その接続だけを未確認にする。画像全体の有用な情報を破棄せず、未確認の接続を確定した経路へ補わない。モデルの自由な要約・検索本文から別の経路を作る投影は行わない。

探索の回数や経路数が上限と一致しただけでは省略記号を付けない。実際に未処理の探索候補・追加の経路を残して打ち切った場合だけ「…」を示す。

この構造は矢印や条件の視覚的な読み取りの正しさを保証しない。例えば矢印のない共有線をモデルが有方向と読む可能性は残る。確認には原図を使う。二回目の意味監査や、回答後の再検索・再生成は追加しない。

## キャッシュと既存の図

Vision checkpoint は応答schemaのhashをキーに含め、旧図構造を新契約として再利用しない。checkpoint版は3、Vision本文形式は5、backendの解析artifact版は12。

誤った関係をすでに保存した図は、再投影や再分割だけでは正しくならない。配備後、対象の文書レシピで「解析」から再処理する。既存文書の一括再処理をこの修正が自動で実行することはない。

## 実行コマンド

関係する合成回帰（リポジトリ直下から）:

```bash
cd rag/packages/rag_engine
uv sync --locked --dev --extra pdf
uv run --locked --no-sync python -m pytest -q tests/test_diagram_shared_conditions.py tests/test_picture_descriptions.py tests/test_vision_checkpoints.py tests/test_vision_text_projection.py tests/test_vision_prompt_rules.py
```

コードの解析artifact版の確認（リポジトリ直下から）:

```bash
cd rag/backend
.venv/bin/python -c 'from app.rag.ingestion import EXTRACTION_ARTIFACT_SCHEMA_VERSION; print(EXTRACTION_ARTIFACT_SCHEMA_VERSION)'
```

配備が承認されたときだけ、リポジトリ直下で再配備する:

```bash
sudo -n ./platform/deploy/suite-update.sh
```

## 画像原本から図領域が検出されない場合（#4879）

図・画像の読み取りが有効な文書レシピで、画像原本の解析結果に Picture / Table 領域が一つもない場合は、原本全体を一つの図として同じ Vision 読取へ渡す。文字要素だけの画像と、文字要素もない画像を対象とする。Docling とその他の解析エンジンに共通して適用し、OCR の文字・位置は保持する。成功した説明と領域は解析成果物へ保存し、同じ成果物を再度渡しても読み取りを重複させない。

既存の図表領域がある画像には全体の読取を追加しない。PDF の文字だけのページも対象にしない。レシピで図・画像の読み取りが無効なら実行しない。文字だけの画像も対象になり得るため、従来読み取りを省略していた画像はモデル呼出が一回増える。読み取りの失敗は診断へ記録し、元の OCR は残す。意味の監査や二回目の読取は追加しない。

旧成果物へ説明を反映するには、配備後に対象文書のレシピで「解析」から再処理する。再分割だけでは反映されない。解析artifact版を11から12へ上げて旧抽出結果の再利用を防ぐ。Vision本文の形式とプロンプトは変更しない。

合成画像と固定した解析結果による回帰（リポジトリ直下から）:

```bash
cd rag/backend
uv sync --locked --dev
uv run --locked --no-sync python -m pytest -q tests/test_vision_source_image.py tests/test_vision.py tests/test_vision_text_map.py tests/test_vision_producer_bindings.py
uv run --locked --no-sync python -m pytest -q tests/test_vision_crop_projection_ingest.py tests/test_vision_failed_picture_ingest.py tests/test_vision_strategy_projection.py tests/test_vision_projection_review.py
uv run --locked --no-sync ruff check app/rag/vision.py app/rag/ingestion.py tests/test_vision_source_image.py
uv run --locked --no-sync ruff format --check app/rag/vision.py app/rag/ingestion.py tests/test_vision_source_image.py
uv run --locked --no-sync mypy app/rag/vision.py app/rag/ingestion.py
```
