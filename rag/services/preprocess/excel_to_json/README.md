# preprocess-excel-to-json

parse の **前** に Excel(`.xls` / `.xlsx`)原本を決定論で「行の記録」JSON(シートごとの表頭・元の行番号・
セル範囲・表示の値)へ変換する前処理マイクロサービス(#1221)。読み方は engchina/no.1-rag の前処理
(`utils/office_preprocess_util.py`)に合わせる。

- 出力契約: `rag_parser_core` の `ConvertResponse`(`POST /convert`)。派生物の content type は
  `application/vnd.production-ready.sheet-records+json`(`rag_parser_core.sheet_records`)で、Docling /
  Unstructured の parser のサービスが外部の parser に渡さずに 1 記録 1 要素にする。
- 選択肢: `POST /convert` の form の `options`(JSON)。backend は文書レシピの `excel_options`(無ければ
  `RAG_PREPROCESS_EXCEL_OPTIONS`)を渡す。
- readiness: `GET /health`(openpyxl / xlrd 導入状況)
- 依存未導入・解析失敗・空・選択肢の誤りのときは `converted=false`(passthrough)を返す

## 変換仕様

| 項目 | 挙動 |
|---|---|
| 対応形式 | `.xlsx`(openpyxl。結合セル・書式を読むため read_only にしない)/ `.xls`(xlrd 2.x、formatting_info) |
| 読み方(`mode`) | `table` は 1 行 1 記録。`procedure` は手順書(表頭の無い番号の列と、表頭のある最初の列=題名)を、番号と題名のある行から次の手順までで 1 記録にし、詳細の無い題名の行を章にする。`auto`(既定)は番号と題名の列があれば procedure、無ければデータの行の埋まり方(中央値 60% 以上)で table |
| 表頭 | `header_row`(1 始まり)・`header_row_count`(1〜5)の指定を優先。無ければ先頭 30 行から採点して選び、信頼度と理由を残す(0.6 未満は警告 `excel_header_low_confidence`。抽出の warning にもなり、文書レシピを確認(REVIEW)で止める。#1229)。表頭より上の行は `preamble` |
| 列名 | 複数行の表頭は「 / 」でつなぐ。空は `column_<列>`、重複は `<名前>__<列>` |
| 行・場所 | 空行を除く前の元の行番号(`row_start` / `row_end`)とセル範囲(`A5:F5`) |
| セル値 | 表示の書式(ゼロ埋め `000`・桁区切り・小数桁・百分率)で文字列にする。日付は ISO 8601、真偽は `TRUE` / `FALSE`。`.xls` の日付もシリアル値にしない |
| 数式 | キャッシュ値を読む。キャッシュの無い数式は数式の文字列にし、セルの位置付きの診断(`formula_without_cached_value`)を残す。マクロは実行しない |
| 結合セル | 表頭の中は結合の範囲を埋める。データは同じ行の横の結合だけを埋め、縦の結合の値は後の行へ漏らさない |
| シート・列 | 既定は表示されているシート。`sheets`(読む)・`exclude_sheets`(読まない)・`include_hidden_sheets`・`exclude_columns`(列名か列の記号) |
| 読む範囲 | `ranges`(`A3:F200` は選んだすべてのシート、`シート名!A3:F200` / `'名前 1'!A3:F200` はそのシートだけ。シート名を付けた範囲があるシートはそれだけを使う)。範囲の外のセルは表頭の推定にも記録にも使わない。結合セルは範囲と重なる部分に切り詰める。無いシート名は警告 `excel_range_sheet_not_found` |
| 列の役割(#1281) | 設定値の表の列に役割(`definition` 説明 / `default` 既定値 / `example` 例示 / `current` 記載時点の設定値 / `recommended` 推奨値 / `allowed` 設定できる範囲)を付け、`columns[].role`・`role_method`(`configured` / `detected`)・`role_term` に残す。`column_role_detection=auto`(既定)は表頭の語(既定値・初期値・デフォルト・記入例・設定例・サンプル・現在値・本番値・推奨値・設定可能範囲・説明 など。英語も)で決める。語が完全に一致(括弧の単位は無視)か、3 文字以上の語が表頭の末尾にあるときだけ付け、「設定値」「値」のように資料ごとに意味の違う語(`column_role_uncertain`)・2 つの役割の語を含む表頭(`column_role_ambiguous`)・役割の列が 1 つだけのシート(`column_role_not_corroborated`)・表頭の推定の信頼度が低いシート(`column_roles_skipped_low_header_confidence`)には付けない。`column_roles`(列名か列の記号 → 役割。`none` は付けない)は判定より優先し、どのシートにも無い列は警告 `excel_column_role_target_not_found`。parser は値の列の本文に `既定値［資料の既定値］: 30` のように役割の表示を付ける(`rag_parser_core.sheet_records.COLUMN_ROLE_TEXT_LABELS`) |
| 縮退 | 空・依存欠如・解析失敗・記録 0・選択肢の誤りのときは passthrough |

## テスト

```bash
# CI(Lint / services & scripts)と同じ
uv run --locked --with pytest python -m pytest -q tests
```

## ローカル実行(開発)

```bash
# repo root から(共有 package の path source を解決するため)
uv run --directory services/preprocess/excel_to_json \
  uvicorn app.main:app --host 127.0.0.1 --port 18013
```

## 起動(uv の venv + systemd。#286)

サービスごとの uv の venv(`uv sync --locked --no-dev --python 3.12`)で動き、本番・開発とも systemd の unit
(`production-ready-rag-preprocess-excel-to-json.service`)で起動 / 停止する。Docker は使わない(Dockerfile は #356 で削除した)。

```bash
# rag/ で実行する
scripts/rag-services.sh install preprocess-excel-to-json   # venv を作り unit と sudoers を登録して起動
scripts/rag-services.sh run preprocess-excel-to-json       # systemd の無い環境で前面に起動
```
