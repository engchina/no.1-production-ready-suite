# 前処理: PII マスク(`pii_redact`)

取込時に原本テキストの **PII(氏名・メール・電話番号・クレジットカード番号 等)を検出して
マスク**し、検索索引に個人情報を載せないようにする前処理マイクロサービス。Microsoft Presidio
(ローカル OSS)+ 日本語 NER(spaCy `ja_core_news_lg`)で完結し、外部 SaaS は呼ばない
(確定スタック非抵触)。

| 項目 | 値 |
|---|---|
| profile | `pii_redact` |
| 主依存 | presidio-analyzer / presidio-anonymizer / spaCy(ja モデル) |
| 既定 URL | `http://127.0.0.1:18016` |
| dev port | 8016 |
| profile 種別 | CPU(dev は uv プロセス) |

## 方針(溯源・非漏洩)

- **原本は必ず保全**し、マスク済みテキストを派生 canonical として後段 parse へ渡す
  (backend が `SourceDerivation` で派生系譜=溯源を残す)。
- 対象は **テキスト形式の文書だけ**(テキスト・Markdown・CSV・JSON・XML・HTML)。PDF・Office・画像・
  メールは backend が取込の前に止める(原本のまま取り込まない)。
- 置き換えは種別ごとの `［氏名］`・`［メールアドレス］`・`［電話番号］`・`［カード番号］` など
  (全角の括弧。`<…>` は Markdown / HTML の解析でタグとして捨てられるため使わない)。
- 本文は行ごとに解析し、行の区切り(Markdown の表・箇条書き)をそのまま残す。UTF-8 以外の本文は
  日本語の文字コード(Shift_JIS 等)から判定して読み、派生物は UTF-8 で返す。
- warning / ログには **PII の値そのものを載せず**、entity 種別と件数のみ
  (`pii_redacted:PERSON=2` のような非機密証跡)を残す。
- 検出 0 件は原本と同じ内容を派生物として返す(`pii_no_findings`)。非テキスト(`pii_not_text`)・
  空(`pii_empty`)・上限の 100 万文字の超過(`pii_too_large`)・解析の失敗(`pii_redact_failed`)は
  変換せずに理由を返し、backend が取込を止める(原本のまま索引に載せない)。
- 解析器(日本語モデル)は process の中で 1 回だけ作り、同時の要求は 1 つずつ解析する
  (spaCy / Sudachi は thread-safe でない)。

## 関連

- これは **取込時**(ingest)の PII マスク。**クエリ/回答時**の安全は Guardrail アダプター
  (`rag_guardrail_policy`)が担い、`rag_guardrail_backend=oci_guardrails` で OCI Generative AI
  Guardrails(content moderation / PII / prompt injection)を併用できる。

## 起動

```bash
# dev(ホストの uv プロセス。事前に `python -m spacy download ja_core_news_lg` が必要)
uv run --directory services/preprocess/pii_redact uvicorn app.main:app --port 18016

# rag-services.sh(サービスの venv を作り、日本語 NER モデルも入れてから 127.0.0.1:18016 で起動)
scripts/rag-services.sh run preprocess-pii-redact   # rag/ で実行(uv の venv。systemd は install)
```
