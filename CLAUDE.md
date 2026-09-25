# CLAUDE.md

> **ルールの正本は [AGENTS.md](./AGENTS.md)（monorepo 共通）と、各ディレクトリの `AGENTS.md`（製品固有）です。** 以下の import で共通ルールを取り込みます。
> 製品ディレクトリ（`platform/` `rag/` `nl2sql/` `agent/`）で作業するときは、そのディレクトリの `CLAUDE.md` / `AGENTS.md` もあわせて適用します。
> ルール変更は **AGENTS.md 側を編集**してください（Claude Code / Codex 両対応のため二重管理しない）。

@AGENTS.md

---

## Claude Code 固有メモ

- **UI/UX 作業では `ui-ux-pro-max` skill を必ず起動する**（設計・実装・レビュー・改善のいずれも）。
- ドキュメント生成（.docx/.pptx/.xlsx/.pdf）が必要な場合は対応する skill を使う。
- LLM/VLM = **OCI Enterprise AI**、embedding/rerank = **OCI Generative AI（Cohere Embed v4 / Rerank v4 fast）**、ベクトル DB = **Oracle 26ai** という分担を取り違えないこと。
- 応答・コミットメッセージ・コメントは日本語（技術用語・識別子は原語のまま）。ユーザーが会話で別の言語を指定した場合は、応答だけその言語に合わせる。
