# parser-unstructured

Unstructured を独立したプロセス(サービスごとの uv の venv)で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- unstructured[all-docs] のバージョンは本サービス単独で upgrade 可能
- OS 依存(poppler / tesseract)は host に入れる(`rag/init_script.sh` / `rag/docs/deployment.md`)

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-parser-unstructured.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install parser-unstructured   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run parser-unstructured       # systemd の無い環境で前面に起動
```
