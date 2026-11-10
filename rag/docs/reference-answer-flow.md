# 参考回答の生成と技術再試行

通常の回答は、検索した原文を入力予算に収め、一度生成する。生成後に正確性・全小問の網羅性を別のモデルで採点して、再検索・再生成する段は設けない。

## 原文の選択

関連度の順に重複を除き、原文の文字予算と、system・prompt・出力 schema・出力予約を含むモデルの入力予算を確認する。大きい原文の一部を渡すときは原文の区切りと絶対位置を保持し、表は途中で壊さない。省略した原文があることを既存の言語資源で表示する。

入力に入らなかった資料に重要な条件がある場合、回答から漏れることがある。全資料を読んだという意味ではない。入力が大きいことを理由に、分批 LLM 読取や繰り返し縮約へ自動で進めない。

## 回答種別と品質

生成は同じ応答で `response_kind` を宣言する。既存の `answered`、`conditional`、`insufficient_evidence` 等へそのまま記録する。種別は正確性の認証でも、本文を隠す条件でもない。資料不足でも、参考にできる原文・説明を残す。

実行の成功、回答の種類、品質評価は区別する。適切な拒答は完了した返信として扱うが、回答できる質問への拒答は独立の評価で不合格になり得る。技術失敗は拒答の品質成功に置き換えない。宣言の無い過去の記録は従来の構造で読む。

## 技術再試行

同じモデル・同じ論理呼出の通信障害や出力形式不正に、既定で最大 1 回再試行する。内側と外側の処理は同じ回数枠を使い、回答全体の期限・取消を優先する。低品質、部分回答、拒答を再試行の条件にしない。別モデルへの切替と、画像応答が失敗した後の文字だけの再生成は行わない。以前の代替モデル指定は通常の回答生成では使用しない。

`rag/backend/.env` の全体設定:

```dotenv
RAG_LLM_MAX_RETRIES=1
```

0 は再試行なし。設定の実効値の確認（リポジトリ直下）:

```bash
cd rag/backend
.venv/bin/python -c 'from app.config import get_settings; print(get_settings().rag_llm_max_retries)'
```

関係する合成回帰（リポジトリ直下）:

```bash
cd rag/packages/rag_engine
uv run --locked --no-sync python -m pytest -q tests/test_evidence_selection.py tests/test_response_kind.py tests/test_llm_retry_budget.py tests/test_answer_flow_single_generation.py
```

配備は開発・CI の完了とは別の操作。配備が承認されたときだけリポジトリ直下で実行する:

```bash
sudo -n ./platform/deploy/suite-update.sh
```
