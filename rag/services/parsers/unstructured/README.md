# parser-unstructured

Unstructured を独立 image で動かす parser マイクロサービス。

- 出力契約: `rag_parser_core` の `StructuredExtraction`(`POST /parse`)
- readiness: `GET /health`
- unstructured[all-docs] のバージョンは本サービス単独で upgrade 可能
- OS 依存(libGL / poppler / tesseract)は本 image 内に隔離

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-parser-unstructured.service`)で起動 / 停止する。Docker は使わない(`Dockerfile` は後続の PR で削除する)。

```bash
# rag/ で実行する
scripts/rag-services.sh install parser-unstructured   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run parser-unstructured       # systemd の無い環境で前面に起動
```
