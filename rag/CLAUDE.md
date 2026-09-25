# CLAUDE.md

> **プロジェクトのルールは [AGENTS.md](./AGENTS.md) を正本とします。** 以下の import で全内容を取り込みます。
> ルール変更は **AGENTS.md 側を編集**してください(Claude Code / Codex 両対応のため二重管理しない)。

@AGENTS.md

---

## Claude Code 固有メモ

- **UI/UX 作業では `ui-ux-pro-max` skill を必ず起動する**(設計・実装・レビュー・改善のいずれも)。
- ドキュメント生成(.docx/.pptx/.xlsx/.pdf)が必要な場合は対応する skill を使う。
- RAG の製品語は **ナレッジ構築 / 業務ビュー / 検索・回答設定** を優先し、工程語は高度な診断または内部コードに限定する。
- SQL 専用プロダクトの設計・実装は sibling repo 側で扱い、この RAG repo へ UI/API/設定を混在させない。
- 応答・コミットメッセージ・コメントは日本語(技術用語・識別子は原語のまま)。
