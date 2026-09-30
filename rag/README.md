# Production Ready RAG

A production-ready RAG reference implementation covering data ingestion, chunking, indexing, hybrid retrieval, reranking, evaluation, observability, guardrails, and deployment best practices.

特定業務ドメインに固定せず、RAG システムを本番品質で構築するための参照実装として、ドキュメント取込から検索・回答生成・運用品質までを一貫して扱う。

> プロジェクトの開発ルール（技術スタック・規約）は [AGENTS.md](./AGENTS.md) が正本です（Claude Code / Codex 共通）。

## 技術スタック

| レイヤー | 採用 |
|---|---|
| LLM / VLM | **OCI Enterprise AI**（OCI Generative AI の chat API は使わない） |
| 埋め込み / リランク | **OCI Generative AI**（Cohere Embed v4 = 1536次元 / Rerank v4 fast） |
| ベクトル検索 / DB | **Oracle AI Database** / Oracle AI Vector Search（`VECTOR(1536, FLOAT32)`）+ Oracle Text |
| バックエンド | Python 3.12 + **FastAPI** + Pydantic v2 + uv |
| フロントエンド | **Vite + React Router** + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Zustand |
| ストレージ | OCI Object Storage |

UI/UX は日本語第一の業務アプリとして、情報設計・画面構成・状態モデル・タイポグラフィを本リポジトリ内で管理する。実装技術は AGENTS.md の確定スタックを正とする。

## クイックスタート

```bash
# バックエンド(重い parser 依存は持たない。外部 parser はマイクロサービスへ HTTP 委譲)
cd backend
uv sync
cp ../../platform/.env.example ../../platform/.env   # 3製品共通の設定（OCI / Oracle 接続など。PLATFORM_*）
cp .env.example .env        # RAG 固有の設定（RAG_*）
uv run uvicorn app.main:app --reload    # http://localhost:8000/docs

# フロントエンド（別ターミナル）
cd frontend
npm ci
BACKEND_URL=http://localhost:8000 npm run dev   # http://localhost:3000（BACKEND_URL 未指定なら /api は 404）

# 前処理 / CPU parser(サービスごとの uv の venv + systemd の unit。rag/ で実行。#286)
scripts/rag-services.sh install            # GPU parser(ASR)も使う場合は --gpu を足す
```

CPU の parser(docling / unstructured)は **独立した FastAPI マイクロサービス**(`services/parsers/<name>`)で
動き、backend は取込時に HTTP 委譲する。各 parser は独自依存で個別に upgrade できる。
既定の解析エンジンは **Docling**(#286)。Docling は **PDF と画像だけ**を解析し、それ以外(テキスト・HTML・Office・メール など)は
取込を始める前に止めて、処理レシピで Unstructured(または Office→PDF)を選ぶよう案内する(自動では振り分けない)。
Unstructured の解析サービスは既定では配備しない。形式ごとの取り込み方は [docs/deployment.md](./docs/deployment.md) の
「既定の解析エンジン（Docling）で扱える形式と、それ以外の形式の取り込み方」を参照。
GPU の OCR(mineru / dots_ocr)は外部で運用する API を「検索・回答設定 › 文書解析」で指定する。
Marker / Unlimited-OCR / GLM-OCR への対応は削除した(#270)。
詳細は [services/parsers/README.md](./services/parsers/README.md) と
[AGENTS.md](./AGENTS.md) の「Parser マイクロサービス」節を参照。

### サービス管理画面(マイクロサービスの稼働可視化・起動/停止)

前処理 / parser は、サービスごとの uv の venv で動くネイティブのプロセスで、systemd の unit
(`production-ready-rag-<service>.service`、`127.0.0.1:<port>`)として動かす(#286。Docker は使わない)。
システム設定の **サービス管理**(`/settings/services`)は、各 unit の状態(`systemctl show`)と `GET /health` から
稼働状態(稼働中 / 縮退 / 起動中 / 起動失敗 / 停止 / 未登録 / 未設定)を 5 秒ごとに表示し、起動 / 停止 / 再起動とログ
(`journalctl -u <unit>`)を画面から操作する。

- 起動 = `systemctl enable --now`、停止 = `systemctl disable --now`。利用者が最後に操作した状態が再起動・再配備後も保たれる。
- backend は、sudoers で許可した unit の `systemctl` / `journalctl` だけを `sudo -n` で実行する。unit 名は
  `app/services/catalog.py` の allowlist で検証し、argv は固定(任意のコマンドは実行できない)。
- 本番は `RAG_SERVICE_CONTROL_ENABLED=true`(Terraform の stack の既定)のときだけ操作でき、開発(`RAG_ENVIRONMENT=development`)は自動で有効。
- 開発環境の unit と sudoers は `scripts/rag-services.sh install` で登録する(systemd の無い環境は
  `scripts/rag-services.sh run <service>` で前面に起動)。手順は [docs/deployment.md](./docs/deployment.md)。

詳細は [backend/README.md](./backend/README.md) / [frontend/README.md](./frontend/README.md) を参照。

## 実装済みの参照フロー

- `POST /api/documents/upload`: 原本を Object Storage 境界へ保存し、SHA-256 / サイズ / 重複元を記録してドキュメント行を作成。
- `POST /api/documents/{id}/ingestion-jobs`（文書の既定レシピ）/ `POST /api/documents/{id}/recipes/{recipe_id}/ingestion-jobs`（レシピ単位）: 取込 job を投入し、worker がファイル準備 → OCI Enterprise AI 境界での OCR/構造化要素抽出 → ページ・章節・表・リスト感知 chunking → embedding → Oracle AI Database 境界への索引を工程ごとに実行する。確認待ちの工程は `POST /api/documents/{id}/recipes/{recipe_id}/approve` で次へ進め、抽出の修正は `PATCH /api/documents/{id}/recipes/{recipe_id}/review-edits` で保存する。
- `POST /api/search`: 質問の安全チェックと検索範囲（Business Context Pack）の確定の後、回答フロー（質問の理解・質問の拡張・Oracle AI Vector Search と Oracle Text の hybrid 検索（RRF）・Cohere Rerank・根拠の評価と補正検索（CRAG）・small-to-big・回答の生成と監査）で citation-grounded 回答を作り、回答側の安全チェック・trace ID・guardrail warning・回答フローの実行記録を返却。
- `POST /api/evaluation/run`: golden set による precision@k、recall@k、MRR、回答キーワード命中率、groundedness pass rate、case 単位の失敗理由分布を算出。
- `POST /api/evaluation/compare`: 同じ golden set で複数の検索設定を比較し、ranking metric に基づく best experiment を返却。
- `/metrics`: Prometheus metrics を公開。

`evaluation/golden-set.example.json` は評価 API のテンプレートです。実データ投入後に `evaluation/golden-set.json` へコピーして document id と期待キーワードを調整し、CI / staging gate で使います。

Backend は常に OCI Enterprise AI、OCI Generative AI、Oracle AI Database を前提に動作します。開発・staging・本番のいずれも OCI / Oracle 接続情報を共通 `.env`（リポジトリの `platform/.env`、`PLATFORM_*`）または設定画面から注入してください。RAG 固有の設定は `backend/.env`（`RAG_*`）に置きます（#211。詳細と既存環境の移行は [docs/deployment.md](docs/deployment.md)）。

## OCI への配備（Resource Manager）

3製品共通の Terraform stack（monorepo root の `terraform/stack/`）で配備します。Oracle Autonomous AI Database は NL2SQL / Agent と共有し、
RAG は専用の Compute 1 台に NL2SQL / Agent と同じネイティブ配備（uv の venv + systemd + Nginx。Docker は使わない。#286）を作ります
（CPU の parser だけを配備する）。以前の Docker Compose で配備した環境の移行は [docs/deployment.md](./docs/deployment.md) を参照してください。
「配備する製品」で RAG を選び、入力・配備方式・instance 上の構成・制約は [terraform/README.md](../terraform/README.md) を参照してください。

## ドキュメント

- [RAG アーキテクチャ](./docs/rag-architecture.md)
- [AIDB Memory Engineering](./docs/aidb-memory-engineering.md)
- [評価・観測性・ガードレール](./docs/evaluation-observability-guardrails.md)
- [デプロイメント](./docs/deployment.md)
- [参考 RAG プロジェクト](./docs/reference-rag-projects.md)

## シークレット混入防止(gitleaks)

`.env` 等の機微値を扱うため、コミット前に **gitleaks** でシークレット混入を検出する pre-commit hook を用意している。設定は `.gitleaks.toml`(誤検知の test/E2E fixture のみ allowlist)。各開発者は一度だけ以下を実行する。

```bash
# 1. gitleaks バイナリを導入(例)
brew install gitleaks            # macOS
#  Linux は GitHub Releases の tar.gz を展開し PATH へ配置

# 2. pre-commit を導入して hook を有効化
uv tool install pre-commit       # または pipx install pre-commit
pre-commit install

# 任意: 全ファイルを手動走査 / 履歴全体を走査
pre-commit run --all-files
gitleaks git -c .gitleaks.toml .
```

CI(`secret-scan` ジョブ)でも同じ `.gitleaks.toml` で full history を走査するため、hook 未導入の環境からの混入も PR で検出される。

## CI

monorepo root の `.github/workflows/ci.yml`(統合 CI)で secret-scan(gitleaks)と RAG の job(`RAG / Backend`・`RAG / Engine`・`RAG / Frontend`・`RAG / E2E smoke`・`RAG / Compute init script`)を実行する。Pull Request と `main` への push で、gitleaks による full history シークレット走査、backend の format・lint・type check・test・security audit(依存が変わったときは dependency audit)、frontend の unit test・build、Playwright の smoke、配備スクリプト(`init_script.sh`・`scripts/rag-systemd.sh`・`scripts/rag-services.sh`)のテストを実行する。Docker Compose の検証は自前の compose を削除したため無い(#356)。

frontend の lint(`npm run lint`)と dependency audit(`npm audit`)は CI では実行せず、ローカル検証と PR review で確認する。Playwright は PR では smoke だけを実行し、全件は `e2e-nightly.yml` が毎晩実行する(#184)。型検査は `npm run build` の `tsc --noEmit` が CI 上で兼ねる。

`main` の ruleset の必須 check は `CI OK` の 1 つだけ(変更のあった製品の job の結果を集約する。root の [AGENTS.md](../AGENTS.md) の「CI」)。

`.github/workflows/dependabot-auto-merge.yml` は Dependabot PR の patch / minor 更新だけを required checks 成功後に自動 merge する。major 更新と、major を含む grouped PR は自動 merge せず手動レビューで判断する。人間が作成した PR には作用しない。
