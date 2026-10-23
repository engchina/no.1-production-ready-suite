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
- 検出の対象は、個人を特定する・連絡できる値と金融の番号: 氏名・メールアドレス・電話番号・
  クレジットカード番号・IBAN・IP アドレス・地名(NER)と、日本の住所(都道府県＋市区町村＋番地)・
  郵便番号(`〒` か「郵便番号」の直後)・個人番号(検査用の数字が合う 12 桁)。日付・期間と URL は
  文書の事実として残す(#2078)。
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

- これは **取込時**のローカル PII マスクです。質問と回答の安全チェックは、OCI Guardrails の
  `RAG_GUARDRAILS_INPUT_ENABLED` / `RAG_GUARDRAILS_OUTPUT_ENABLED` で個別に選びます。
- 日本語の人名形態素を結合し、NER が姓だけを返す場合も氏名全体をマスクします。
  「項目: 姓 名（補足）」のような二つの名詞からなる単一の値では、未知の姓も名前に含めます。
  自由文の一般名詞へ範囲を広げず、組織として識別された語を人名として追加しません。
- 統計モデルに検出漏れ・誤検出があり、すべての個人情報の削除は保証しません。
  原本を確認し、既存のマスク済み派生物にも変更を適用する場合はファイル準備から再実行してください。

## 起動

```bash
# dev(ホストの uv プロセス。事前に `python -m spacy download ja_core_news_lg` が必要)
uv run --directory services/preprocess/pii_redact uvicorn app.main:app --port 18016

# rag-services.sh(サービスの venv を作り、日本語 NER モデルも入れてから 127.0.0.1:18016 で起動)
scripts/rag-services.sh run preprocess-pii-redact   # rag/ で実行(uv の venv。systemd は install)
```
