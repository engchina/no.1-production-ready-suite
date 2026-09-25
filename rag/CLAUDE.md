# CLAUDE.md

> **RAG（`rag/`）のルールは [AGENTS.md](./AGENTS.md) を正本とします。** 以下の import で全内容を取り込みます。
> monorepo 共通のルールと Claude Code 固有メモは [../CLAUDE.md](../CLAUDE.md) / [../AGENTS.md](../AGENTS.md) にあります（作業ディレクトリの上位にある CLAUDE.md は自動で読み込まれます）。
> ルール変更は **AGENTS.md 側を編集**してください（Claude Code / Codex 両対応のため二重管理しない）。

@AGENTS.md

---

## Claude Code 固有メモ（RAG）

- RAG の製品語は **ナレッジ構築 / 業務ビュー / 検索・回答設定** を優先し、工程語は高度な診断または内部コードに限定する。
- SQL 専用プロダクトの設計・実装は `../nl2sql/` 側で扱い、`rag/` へ UI/API/設定を混在させない。
