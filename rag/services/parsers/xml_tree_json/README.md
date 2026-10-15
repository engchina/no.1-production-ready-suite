# parser-xml-tree-json

XML ツリー JSON（XML の要素の木を `tag` / `attr` / `children` の JSON にした文書）を、Markdown などに変換せず
要素の階層のまま解析する parser マイクロサービス（#2024）。

- 出力契約: `rag_parser_core` の `StructuredExtraction`（`POST /parse`）。解析の実体は `rag_parser_core.xml_tree_json`
- readiness: `GET /health`
- 依存は `rag-parser-core[service]` だけ（標準ライブラリの `json` で木を読む。外部の parser の package は使わない）
- 対応するスキーマでない JSON は `xml_tree_json_unsupported_tree` の fallback を返す（backend は取込を止める）

backend は、使用エンジン（既定 Docling）に関係なく、対応するスキーマの XML ツリー JSON をこのサービスへ回す
（`backend/app/rag/source_format_routing.py`。`RAG_PARSER_XML_TREE_JSON_ENABLED` と文書の処理レシピで止められる）。

## 起動（uv の venv + systemd。#286）

```bash
# rag/ で実行する
scripts/rag-services.sh install parser-xml-tree-json   # venv を作り unit と sudoers を登録して起動（--cpu にも含まれる）
scripts/rag-services.sh run parser-xml-tree-json       # systemd の無い環境で前面に起動（127.0.0.1:18021）
```

## テスト

```bash
cd services/parsers/xml_tree_json && uv run --locked --all-extras --with pytest python -m pytest -q tests
```
