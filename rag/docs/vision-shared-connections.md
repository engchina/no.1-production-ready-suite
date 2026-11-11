# 図の共有接続と条件の保持

図の読み取りは一回の Vision 呼出で行う。文字や形を持つ要素と、線がつながる分岐点（junction）を区別し、隣り合う要素をつなぐ接続段ごとにラベルを保存する。共有段の条件をモデルに枝ごとに書き直させない。

回答・索引へ渡す接続説明は、この保存構造から一つだけ投影する。分岐点を経由する経路でも各段のラベルを原文のまま保持し、別の枝のラベルを混ぜない。ラベルの語や条件式の意味をコードで解釈しない。

端点が不明・参照IDが不明・重複した接続は、その接続だけを未確認にする。画像全体の有用な情報を破棄せず、未確認の接続を確定した経路へ補わない。モデルの自由な要約・検索本文から別の経路を作る投影は行わない。

この構造は矢印や条件の視覚的な読み取りの正しさを保証しない。例えば矢印のない共有線をモデルが有方向と読む可能性は残る。確認には原図を使う。二回目の意味監査や、回答後の再検索・再生成は追加しない。

## キャッシュと既存の図

Vision checkpoint は応答schemaのhashをキーに含め、旧図構造を新契約として再利用しない。checkpoint版は3、Vision本文形式は4、backendの解析artifact版は10。

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
