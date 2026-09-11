# SQL 論理構造の再生成と SQL Assist 移植対応

## 対象と参照元

Issue #321。対象は「SQL から質問を生成」の SQL → 物理構造 → 業務論理構造 → 質問と、論理構造 → SQL の往復生成である。

参照元は [engchina/No.1-SQL-Assist](https://github.com/engchina/No.1-SQL-Assist/blob/cacd0959a5fa2a279cb1147de2d04e5a9b01815f/utils/selectai_util.py)、固定 revision は `cacd0959a5fa2a279cb1147de2d04e5a9b01815f`。
この revision では構造分析・逆生成は `utils/query_util.py` ではなく `utils/selectai_util.py` にある。

## 原文の保存と実行時の対応

`backend/app/features/nl2sql/prompts/sql_assist/` に関連する4プロンプトを保存した。静的本文は原文を保持し、実行時の SQL / schema / 生成結果の文字列結合だけをサービス側へ分離した。質問の変数は `{logical_structure}` に改名した。`manifest.json` に revision と SHA-256、`LICENSE` / `NOTICE` に Apache-2.0 の帰属と変更範囲を記録する。

| 参照元の処理 | 保存した原文 | 現在の実行 |
|---|---|---|
| `_SQL_STRUCTURE_ANALYSIS_PROMPT` | `structure_analysis.txt` | 元 SQL から全句・列順・alias・CTE・サブクエリ等を抽出 |
| `_rev_generate_async` の `prompt` | `logical_structure.txt` | 物理構造と元 SQL、schema の論理名・コメントから業務論理構造を生成 |
| 同関数の `question_prompt` | `business_question.txt` | 論理構造から全条件を保持する日本語質問を生成。従来の1文への圧縮制約を廃止 |
| 同関数の `glossary_prompt` | `glossary_question.txt` | `use_glossary=true` の場合だけ逆正規化規則と用語集を適用 |
| 再構築可能な仕様を Text-to-SQL に戻す意図 | `reverse_prompts.py` の `STRUCTURE_TO_SQL_PROMPT` | 専用 `/api/nl2sql/reverse/sql` で編集した論理構造だけを要件にして SQL を生成 |

原版の用語正規化は質問生成後に別 LLM 呼び出しを行う。移植後は同じ原文規則を質問生成時に合成し、追加呼び出しと生成済み条件の再書き換えを削減する。通常は3回、SQL 再生成は明示操作で1回の Enterprise AI 呼び出しとなる。

原文の「100%」は情報保持の目標であり、LLM が全 SQL に対して意味同値性を保証するという意味ではない。Gradio・外部 LLM provider・OCI GenAI chat は移植せず、既定スタックの React / FastAPI / OCI Enterprise AI へ対応付けた。DB・デプロイ・モデル設定等の他領域は本変更の対象外。

## 不具合修正と補正

- `業務質問を生成` 完了後は `SQL分析・質問候補` タブへ移り、表示中の作業ではタブへフォーカスを移す。画面は `SQL入力・生成` / `SQL分析・質問候補` の2タブ・2ステップで、質問候補は論理構造・再生成 SQL の後に表示する（Issue #325）。旧 `result` タブの保存状態は草稿を保持して統合先へ移行する。
- SQL Assist の「AI分析」に相当する第一段階の `sql_structure` を編集欄へ表示する（Issue #421）。SELECT句・FROM句などの Markdown と物理識別子をそのまま保持し、業務名へ変換した第二段階の `logical_structure` は質問生成用に分離する。`sql_structure` が空の簡易応答・旧 API では `logical_structure` を使用する。
- 各生成段階を Pydantic で検証する。構造生成は JSON envelope に加え、原文指定に従うモデルの Markdown（SQL 構造見出しと箇条書きを持つ本文、外側の Markdown fence を含む）を受理する。一般の文章・不正 JSON・空結果は成功として扱わない。後段が失敗しても完成した `sql_structure` は残し、簡易質問であることを警告する。
- 未設定・前段失敗時は簡易構造に元 SQL 全文を保持し、正規表現の要約で CTE・関数・リテラル等が失われないようにする。埋め込まれた元 SQL と編集した要約が矛盾する場合、再生成時に推測で解決しないよう指定する。
- 原版の `LIKE → を含む` の一律変換を補正し、前方/後方/部分一致、`%` / `_` / `ESCAPE` を区別する。括弧、AND/OR/NOT、JOIN ON / WHERE、関数引数、CASE、window frame、bind 変数、引用符等の保存も追加で指定する。
- 用語集は `use_glossary` を尊重し、引用したリテラル内部を置換しない。原文中の英語出力・Markdown 単独出力指定は実行時に日本語説明・JSON envelope へ明示的に置換する。
- SQL 再生成では SELECT を途中から抜き出したり、最初のセミコロンで切断したりせず、全出力を既存 AST / 許可表 / 危険関数の安全検証へ渡す。

## API と状態境界

`POST /api/nl2sql/reverse/sql` は `logical_structure`, `profile_id`, `use_glossary` だけを受け、`sql`, `explanation`, `source`, `warnings` を返す。元 SQL を別 field で送信して生成要件を上書きしない。`menu.sql_to_question` と既存 Profile アクセス検証を適用し、Profile の許可 object 範囲で schema context と生成 SQL を検証する。

この API は DB 実行・結果照合・履歴保存を行わない。未設定・空入力・不正応答・不許可 SQL は失敗として扱い、代替 SELECT を成功として返さない。Enterprise AI 通信失敗は再試行可能な日本語 `502` を返す。

論理構造草稿は既存 `useWorkspaceState` の同一タブ `sessionStorage`（8時間 TTL、ユーザー/DB/ページ/Profile 隔離）に明示登録する。再取得は草稿を上書きしない。SQL 再生成結果はメモリ上のスナップショットで、生成日時と未実行を示す。構造編集後は旧 SQL・質問候補に旧結果表示を付ける。再読込では草稿だけを復元し、LLM/SQL を再送しない。認証/DB context の破棄時はリクエストを中止する。

## 検証と限界

- `test_nl2sql_structure_roundtrip.py`: 原文チェックサム、3段階の情報伝達、編集構造だけの送信、SQL 全文保持、越権表/危険関数/複文拒否、部分失敗、未設定、用語集 ON/OFF、API アクセスと `502`。
- 既存 `test_nl2sql_reverse_deep_steps.py` / `test_nl2sql_reverse_access.py` と legacy の reverse 関連テストを更新。
- `nl2sql-workflows.spec.ts` の desktop / mobile-375: 成功後タブ・フォーカス、構造編集と再生成、失敗時草稿保持、未実行/旧結果表示、再読込、重複送信なし、主要な空/読込/エラー状態、横 overflow、150% zoom。

自動テストは Enterprise AI と API を fake/mock 化した契約検証である。Issue #421 では `select * from employee` の実 Enterprise AI 応答が JSON ではなく原版形式の Markdown で返ることを確認した。修正後の全生成経路でも SELECT句・FROM句を保持し、`source=oci_enterprise_ai`、警告なしで成功した（テスト用 Profile、DB 実行なし）。実モデルと Oracle のデータで結果の同値性を比較する live 受入は別途必要。任意の SQL について「100% 同値」を証明したものではない。
