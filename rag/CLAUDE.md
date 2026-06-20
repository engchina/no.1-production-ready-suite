# CLAUDE.md

> **プロジェクトのルールは [AGENTS.md](./AGENTS.md) を正本とします。** 以下の import で全内容を取り込みます。
> ルール変更は **AGENTS.md 側を編集**してください(Claude Code / Codex 両対応のため二重管理しない)。

@AGENTS.md

---

## Claude Code 固有メモ

- **UI/UX 作業では `ui-ux-pro-max` skill を必ず起動する**(設計・実装・レビュー・改善のいずれも)。
- ドキュメント生成(.docx/.pptx/.xlsx/.pdf)が必要な場合は対応する skill を使う。
- **NL2SQL 生成 = Oracle Select AI / Select AI Agent**(`DBMS_CLOUD_AI` / `DBMS_CLOUD_AI_AGENT`、DB 内)、その**モデルの頭脳 = OCI Enterprise AI**(profile の `oci_endpoint_id` で参照)、**embedding/rerank = OCI Generative AI(Cohere Embed v4 / Rerank v4 fast)**、**DB/ベクトル = Oracle ADB / 26ai AI Vector Search** という分担を取り違えないこと(詳細は AGENTS.md)。OCI Generative AI の汎用 chat 推論 API を NL2SQL 生成に直接は使わない(Select AI 経由のみ)。
- 設計の調査源は **[docs/reference-nl2sql-projects.md](./docs/reference-nl2sql-projects.md)**。特に engchina/No.1-SQL-Assist と engchina/no.1-denpyo-toroku-kun の Select AI 連携が中核設計源。
- 応答・コミットメッセージ・コメントは日本語(技術用語・識別子は原語のまま)。
