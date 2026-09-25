# 前処理: PII マスク(`pii_redact`)

取込時に原本テキストの **PII(氏名・メール・電話番号・クレジットカード番号 等)を検出して
マスク**し、検索索引に個人情報を載せないようにする前処理マイクロサービス。Microsoft Presidio
(ローカル OSS)+ 日本語 NER(spaCy `ja_core_news_lg`)で完結し、外部 SaaS は呼ばない
(確定スタック非抵触)。

| 項目 | 値 |
|---|---|
| profile | `pii_redact` |
| 主依存 | presidio-analyzer / presidio-anonymizer / spaCy(ja モデル) |
| 既定 URL | `http://preprocess-pii-redact:8000` |
| dev port | 8016 |
| profile 種別 | CPU(dev は uv プロセス) |

## 方針(溯源・非漏洩)

- **原本は必ず保全**し、マスク済みテキストを派生 canonical として後段 parse へ渡す
  (backend が `SourceDerivation` で派生系譜=溯源を残す)。
- warning / ログには **PII の値そのものを載せず**、entity 種別と件数のみ
  (`pii_redacted:PERSON=2` のような非機密証跡)を残す。
- 検出 0 件・非テキスト・空・失敗は **passthrough** へ安全に縮退する。

## 関連

- これは **取込時**(ingest)の PII マスク。**クエリ/回答時**の安全は Guardrail アダプター
  (`rag_guardrail_policy`)が担い、`rag_guardrail_backend=oci_guardrails` で OCI Generative AI
  Guardrails(content moderation / PII / prompt injection)を併用できる。

## 起動

```bash
# dev(ホストの uv プロセス。事前に `python -m spacy download ja_core_news_lg` が必要)
uv run --directory services/preprocess/pii_redact uvicorn app.main:app --port 8016

# Docker(build context = リポジトリ root。モデルは build 時に取得)
docker compose up preprocess-pii-redact
```
