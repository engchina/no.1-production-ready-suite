# 技術の分担と製品名の書き方

> 扱う範囲: AI / DB のサービスの分担（LLM・embedding・ベクトル検索・データの正本）、Oracle の製品名の書き方、backend / frontend の言語と framework、実行の方法。root の [AGENTS.md](../../AGENTS.md) の目次から読み、root の規則と同じ強さで守る（規則を変えるときはこのファイルを直す）。

- **AI / DB は OCI / Oracle に集約する**: LLM / VLM の既定 = **OCI Enterprise AI**（OpenAI 互換のターシャリ接続・Anthropic Claude のクォータナリ接続は登録モデルごとの任意）、embedding / rerank = **OCI Generative AI**（Cohere Embed v4 / Rerank v4 fast）、ベクトル検索 = **Oracle AI Vector Search**、データの正本 = **Oracle AI Database**。外部ベクトル DB・ほかの LLM provider・別 SaaS は入れず、逸脱は理由を添えてユーザーに確認する。
- **Oracle の製品名は公式名で、バージョン（`26ai` 等）を入れない**（「Oracle AI Database」「Oracle AI Vector Search」「Oracle Autonomous AI Database」）。対応バージョンは [terraform/README.md](../../terraform/README.md)「Autonomous AI Database（全製品で共有）」だけに書く（`adb_db_version`・`oracle_26ai` などの値・識別子は変えない）。
- Backend は Python 3.12 + FastAPI + Pydantic v2 + uv と `pr_backend_core`（実装の標準は [backend-standard.md](../../platform/docs/backend-standard.md)）。Frontend は Vite + React Router + TypeScript + Tailwind + 共有 UI + TanStack Query + Zustand。
- **Python は 3.12 に固定する**（版を変えるときは [ci.md](./ci.md)）。
- 自前のコードは Docker を使わずネイティブで動かす（開発は `uv run` / `npm run dev`、本番は systemd + Nginx。詳細は [deploy.md](./deploy.md)）。
