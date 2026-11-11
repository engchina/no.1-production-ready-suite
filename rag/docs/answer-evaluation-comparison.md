# 原文の対照による独立した正確性評価

品質評価はオンライン回答の後に別操作として行い、結果で元の回答を隠したり再検索・再生成したりしない。

質問、標準回答と生成回答の原文単位を一回の判定モデルへ渡す。モデルは単位IDと比較結果を返し、原文の写しはコードが付ける。存在しないIDを使った比較は評価のエラーとして残す。

- `consistent`: 述べた内容は標準回答と整合する。
- `contradicts`: 述べた条件・対象・数値・結論などが標準回答と食い違う。
- `unassessed`: 比較する材料が足りない。
- `uncovered_points`: 標準回答のどの点を答えていないか。正確性とは別の記録。

一つでも矛盾を検出したら正確性は0、述べた内容をすべて整合すると判定したら1、未評価の回答単位や判定不能があれば未判定（null）。正確な部分回答は未回答の点があっても正確性1になり得る。これはすべての質問に回答したことを意味しない。

判定層から再び判定を呼ぶ修復ループは設けない。技術的な再試行は共通adapterの有限の枠だけが扱う。

原文IDの実在・集約をコードで確認しても、整合・矛盾という意味の判断はモデルに依存する。誤判定が無くなる保証ではない。保存済み評価は書き換えず、必要な評価を再実行する。rubric版は26。

## 実行コマンド

合成回帰（リポジトリ直下から）:

```bash
cd rag/packages/rag_engine
uv sync --locked --dev
uv run --locked --no-sync python -m pytest -q tests/test_answer_evaluation.py tests/test_condition_evaluation.py
```

配備したコードの判定契約版の確認（リポジトリ直下から）:

```bash
cd rag/backend
.venv/bin/python -c 'from rag_engine.evaluation.answer_eval import RUBRIC_VERSION; print(RUBRIC_VERSION)'
```

配備が承認されたときだけ、リポジトリ直下で再配備する:

```bash
sudo -n ./platform/deploy/suite-update.sh
```
