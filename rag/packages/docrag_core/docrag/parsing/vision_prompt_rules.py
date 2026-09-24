"""Vision の原文保持・文脈帰属・構造上限を提示詞と保存処理で共有する。"""

from __future__ import annotations

import re

MAX_DIAGRAM_NODES = 30
MAX_DIAGRAM_EDGES = 40
MAX_CHART_AXES = 12
MAX_CHART_LEGENDS = 20
MAX_CHART_SERIES = 12
MAX_CHART_SERIES_VALUES = 20
MAX_TABLE_HEADERS = 20
MAX_TABLE_ROWS = 20
MAX_TABLE_COLUMNS = 20
MAX_FORM_FIELDS = 50
VISION_LIST_FIELD_LIMITS = {
    "diagram_nodes": MAX_DIAGRAM_NODES,
    "diagram_edges": MAX_DIAGRAM_EDGES,
    "chart_axes": MAX_CHART_AXES,
    "chart_legends": MAX_CHART_LEGENDS,
    "chart_series": MAX_CHART_SERIES,
    "table_headers": MAX_TABLE_HEADERS,
    "table_rows": MAX_TABLE_ROWS,
    "form_fields": MAX_FORM_FIELDS,
}

# 生成説明の本文で使うグループ見出しの接頭辞と、語の列挙だけの行のラベル (#773)。
# 根拠の判定・監査（result_granularity / operation_audit）はこれらの行を本文として使わない。
VISION_SENTENCE_GROUP_PREFIX = "■ "
VISION_ENUMERATION_LABELS = (
    "主題", "画面/メニュー", "画面名", "ボタン", "項目", "値", "コード・エラー",
    "検索語", "言い換え", "想定質問",
)
_ENUMERATION_LINE = re.compile(r"\s*(?:" + "|".join(re.escape(label) for label in VISION_ENUMERATION_LABELS) + r"):")


# 画面に写っている値そのものを載せる行。項目名は「項目:」に残るため、検索用テキストからは
# この行とその箇条書きを落とす。prompt の接頭辞（「画面例: 」）はモデルが守るとは限らず、
# 実文書で守られない例が出たため、アプリが決定的に判別できる行ラベルで除く (#778)。
VISION_SCREEN_VALUE_LABELS = ("値", "フォームの項目")
_SCREEN_VALUE_LINE = re.compile(r"(?:" + "|".join(re.escape(label) for label in VISION_SCREEN_VALUE_LABELS) + r"):")
_BULLET_LINE = re.compile(r"\s*- ")


def without_vision_screen_values(text: str) -> str:
    """生成説明から画面の値の行（ラベル行とその箇条書き）を除いた text を返します。

    回答用の text には残すため、検索用テキストを作る側だけで使います。
    """
    kept: list[str] = []
    dropping = False
    for line in (text or "").splitlines():
        if _SCREEN_VALUE_LINE.match(line):
            # 「ラベル:」だけの行に続く箇条書きも同じ field の値なので一緒に落とす。
            dropping = line.rstrip().endswith(":")
            continue
        if dropping and _BULLET_LINE.match(line):
            continue
        dropping = False
        kept.append(line)
    return "\n".join(kept)


def is_vision_enumeration_line(line: str) -> bool:
    """生成説明のうち、語の列挙とグループ見出しだけの行かを返します。

    本文として引用・判定に使うと、ボタン名や検索語が根拠の記述として扱われるため除きます。
    """
    return line.lstrip().startswith(VISION_SENTENCE_GROUP_PREFIX) or bool(_ENUMERATION_LINE.match(line))


RULES_START = "[Vision extraction rules v1]"
RULES_END = "[/Vision extraction rules]"
# 版を上げても保存済みテンプレート内の旧ブロックを 1 つに正規化できるよう、版番号は正規表現で受ける。
# 開始マーカーを現在の版で固定すると、v2 では v1 ブロックに一致せず本文が残り、規則が二重に送られる (#766)。
# 終了マーカーは版を持たないため、そのまま対になる。
_ANY_RULES_START = r"\[Vision extraction rules v\d+\]"
# 開始マーカーの手前に孤立した開始マーカーがあっても、その間の独自文面を巻き込まない。
_MANAGED_BLOCK = re.compile(
    _ANY_RULES_START + rf"(?:(?!{_ANY_RULES_START}).)*?" + re.escape(RULES_END), re.S
)
# ブロックを取り除いた後に残る、対になっていないマーカー。
_ORPHAN_MARKER = re.compile(_ANY_RULES_START + "|" + re.escape(RULES_END))

TABLE_EXTRACTION_INSTRUCTION = (
    "実行対象: 表全体。独立 Picture として抽出されなかった表内画像の文字、ダイアログ、"
    "ボタン、条件と操作を読み取り、対応する行の説明と関連付けてください。"
    "existing_table_html は照合用の根拠データです。画像で確認できる既存セル本文を保ち、"
    "missing_picture_regions の画像セルを原本の列位置に補って table_rows を作成してください。"
    "画像部分の補足だけを返すのではなく、照合に必要な同じ行の既存セルも含めてください。"
    "既存 HTML と画像が食い違う場合は画像を優先し、変更箇所と根拠を correction_notes に残してください。"
    # 行の補完は table_rows だけを照合に使うため、先頭行で上限を使い切ると後方の画像セルが失われる。
    f"表が {MAX_TABLE_ROWS} 行を超える場合は、missing_picture_regions の画像セルを含む行を優先して "
    "table_rows に入れ、残りの枠を先頭の行で埋めてください。"
)


def vision_extraction_rules() -> str:
    """型に依存しない抽出規則と、保存処理と一致する構造配列の件数上限を返します。"""
    limits = "、".join(f"{field}={limit}" for field, limit in VISION_LIST_FIELD_LIMITS.items())
    return f'''{RULES_START}
以下は field ごとの抽出契約です。上記の方針は維持し、原文と検索用の言い換えを分離します。

1. 原文保持
- table_headers / table_rows / visible_* / codes_and_errors / visible_text は原文の正式名称、否定、条件、数値、単位、句読点を保持し、要約・同義語置換をしない。個人情報の扱いは上記の規則に従う。
- table_rows は見出しを除く行を上から下、セルを左から右の順で返す。画像セルもその位置に入れ、空セルは空文字として残す。複数行を一行に統合せず、結合セルや行列の帰属を確認できない場合は推測しない。
- table_summary / retrieval_text は要約用、query_rewrites / search_keywords は検索表現用。table_rows の原文を検索向けに書き換えない。画像と OCR が食い違う場合は読める画像を優先し、読めない箇所は空欄にして correction_notes に残す。

2. field の意味
- visual_kind は flowchart / architecture_diagram / chart / table / form / screenshot / photo / logo / watermark / background / decorative / other から一つ。不明なら other。logo 等は純粋な装飾の場合に限り、業務タイトル・画面名・操作情報を含む対象は見た目だけで装飾にせず、その情報を main_topic / visible_screen_names / retrieval_text に保持する。
- screenshot は操作の順序、クリック/入力/選択/確認の対象、表示条件、有効/無効状態、警告や確認ダイアログの結果を分けて書き、画面名、メニュー経路、ボタン、入力欄、選択肢、コード、エラー、パラメータ、制約、注意書きを優先して残す。
- diagram_* は図のタイトル・読めるノード・可視の接続・経路要約（開始点、分岐条件、順序、戻り先、完了条件）。edges の source / target は nodes の名称と一致させ、方向や条件 label が不明なら接続を創作しない。
- chart_* はグラフのタイトル・種別・軸と単位・凡例・系列・傾向。series の values は確実に読める category=value だけで、目測値を確定値にしない。
- table_headers / table_rows / table_summary は列見出し・同じ行のセル配列・表の要約。Q&A、仕様表、変更履歴、比較表では質問と回答、変更前後、条件と結果、対象と対象外の対応関係を崩さない。対象が表全体なら表内画像も含め、独立した小画像なら周囲の表全体を転記しない。
- form_fields は name / value / state（項目名・表示値・選択/有効状態）、form_layout は配置とグループ。対象外の構造は空文字または空配列にする。項目の説明例を抽出結果としてコピーしない。

3. 文脈の帰属
3.1 対象の特定
- 画像1と metadata.bbox が主対象。context 画像では、マゼンタ（明るい紫）の矩形（システムが付けた target_highlight）だけを対象マークとして扱い、その枠は画像1と同じ内容を囲む。原文に元からある赤枠・選択枠・強調色は文書の内容であり、そこから主対象を変更しない。判別に迷う場合は、画像1と内容が一致する枠を対象とする。
- context_source_record_refs の visible_bbox は実際に写る範囲。partially_visible=true のレコードの全内容が写っているとは扱わない。metadata の推定位置や cell_hint は補助情報であり確定した行対応ではない。

3.2 可視情報の帰属
- visible_* と visible_text は主対象内で確認できる情報だけ。周辺だけに見える画面名・値・手順を主対象の可視情報に入れない。
- visible_buttons は主対象内の操作部品（ボタン・リンク・タブ・メニュー項目）を、赤枠・色・矢印などの強調の有無に関係なくすべて列挙する。ボタン自体に印字された文字列だけを使い、無効化された部品もラベルが読めれば含める。アイコンの意味を推測した名称、チェック項目、周辺本文で言及されるだけのボタン名は含めない。原文が説明のために画面へ重ねた注記（丸数字・番号・矢印・吹き出し・囲み線）は操作部品ではないので含めず、operation_steps の手順番号との対応にだけ使う。
- キー・ショートカット・番号などの識別表記が読める場合は「識別表記 部品名」の形で一緒に残す（例: (F3) 検索、Ctrl+S 保存）。部品名が読めなければ識別表記のみ。

3.3 手順と操作部品
- ボタンの名称・操作は画面内の位置、所属パネル、隣接する入力欄と結び付け、同じ形や色のアイコンでも別位置のラベルを借用しない。operation_steps は操作ごとに根拠の場所（主画像 / 周辺説明）、操作部品の位置、読めるラベル、操作と結果を分けて書き、ラベルのないものは「無記名アイコン」と明記して位置・外観で特定する。読めなければ未確認とする。

3.4 文脈と補足
- surrounding_context は所属見出し・表・同行説明などの関係。owning_section は所属節の候補で画像との整合を確認する。next_record の見出しは次節の境界であり所属節ではない（section_role=section_boundary）。section_role=subheading_in_owning_section の見出しは同じ節の小見出しで、その本文は主対象の説明に使える。
- 周辺の説明を retrieval_text に使う場合は「周辺の同行説明では」等と出所を明示し、別行・別節の条件や結果を結び付けない。

3.5 画面の値の扱い
- 画面キャプチャの入力欄・一覧・ダイアログに見える値（年月・番号・コード・氏名・金額・選択状態）は原則として文書の例示であり、実運用システムの値、現在の顧客データ、顧客固有の設定、既定値、推奨の固定値のいずれとしても扱わない。周辺本文・注記・ラベルが「〜と入力します」「固定」「既定値」のように値を指定・限定している場合だけ、その根拠語と一緒に指定値として残す。
- operation_steps / condition_result_pairs / retrieval_text に画面の値を書くときは「画面例では」「入力例として」を付け、実際の値として書かない。visible_values の例示値は「画面例: 項目名=値」の形で項目名とセットにする。

4. 出力と長さ
- 構造配列の上限: {limits}。table_rows の各行は最大 {MAX_TABLE_COLUMNS} セル、chart_series の values は各系列最大 {MAX_CHART_SERIES_VALUES} 件。
- 長表は原本の先頭から最大 {MAX_TABLE_ROWS} 行を保持する。ただし実行対象が表全体で missing_picture_regions がある場合は、その画像セルを含む行を先に確保し、残りの枠を先頭の行で埋めて、上から下の順で返す。超過分は読める根拠に限って table_summary 等へ要約し、correction_notes に省略の有無と範囲を明示する。要約を全行の構造化抽出として扱わない。
- 同義語・質問例は重複を避け、見えている語から自然に導ける根拠のある表現だけ（特定コーパス専用の同義語を作らない）。必須キーを埋めるために情報を創作しない。出力は実行時の JSON schema に従う。
{RULES_END}'''


def refine_image_retrieval_prompt(template: str) -> str:
    """既存方針を保持して共通規則を適用した文字列を返します。ファイル I/O は行いません。

    既知の旧規則だけを置換し、管理ブロックは再実行時も一つに保ちます。
    対になっていないマーカーと二つ目以降のブロックは取り除きます（間の独自文面は保持）。
    ブロック外の独自文面は既知の旧規則と完全一致する文面以外は変更しません。
    """
    # システムの枠はマゼンタ (#784)。原文の赤枠と色で区別し、「画像1と同じ内容を囲む」ことを補助の判別基準にする。
    highlight_rule = (
        "- 周辺文脈画像では、マゼンタ（明るい紫）の矩形がシステムの付けた対象枠（target_highlight）で、metadata.bbox の位置にあり画像1と同じ内容を囲む。原文に元からある赤枠、ハイライト、選択枠は文書の内容であり、対象の指定ではない。対象外だけの無関係な内容を対象の説明に混ぜない。"
    )
    replacements = {
        "- 周辺文脈画像に赤枠、ハイライト、選択枠などがある場合、その強調領域が対象Pictureです。強調領域外だけに見える無関係な内容を対象の説明に混ぜない。":
            highlight_rule,
        # 判別基準を持たない一世代前の文面。保存済みテンプレートに残っている場合も揃える。
        "- 周辺文脈画像では metadata.bbox とシステムの target_highlight を対象確認に使う。原文内の赤枠、ハイライト、選択枠は対象の指定ではない。対象外だけの無関係な内容を対象の説明に混ぜない。":
            highlight_rule,
        "- `paired_ocr_text` がある場合は読み取り補助として使う。ただしOCR文字列を羅列せず、意味、条件、操作、検索語へ正規化する。":
            "- `paired_ocr_text` は読み取り補助。可視 field と表セルは原文を保ち、意味・条件・操作・検索語への正規化は要約・検索 field で行う。",
        # JSON 例の一文。表モードでは画像セルを含む行を優先するため「先頭から」を外す。
        "表全体では先頭から最大20行とし、残りはtable_summaryへ要約する。":
            "表全体では最大20行とし（行の選び方は共通規則 4 に従う）、残りはtable_summaryへ要約する。",
        # 和文に埋めた英語の否定列挙は not の範囲が決まらず、「既定値として扱う」と逆に読める。
        "not real production system values, current customer data, customer-specific settings, defaults, or recommended fixed values として扱う。":
            "実運用システムの値、現在の顧客データ、顧客固有の設定、既定値、推奨の固定値のいずれとしても扱わない。",
        # 例示値のラベルは日本語 1 系統に統一した (#769)。英語表記が残ると、検索用テキストから
        # 例示値を除く処理と表記が揃わず、生成説明ごとにラベルが混ざる。
        "- 入力欄、検索条件欄、ダイアログ入力欄、ファイル選択欄に既に入っている値は、原則として文書上の入力例・表示例です。実運用システムの値、現在の顧客データ、顧客固有の設定、既定値、推奨の固定値のいずれとしても扱わない。検索に必要なら `input example: field=value` または `display example: field=value` の形で項目名とセットにする。":
            "- 入力欄、検索条件欄、ダイアログ入力欄、ファイル選択欄に既に入っている値の扱いは共通規則 3 に従う。",
        '"visible_values": ["読める選択値、状態、日付、件数、設定値、コード、単位付き数値。項目名と値をセットで書く。事前入力済みUI値は input example: field=value または display example: field=value と明示する"],':
            '"visible_values": ["読める選択値、状態、日付、件数、設定値、コード、単位付き数値。項目名と値をセットで書く"],',
    }
    result = template
    for old, new in replacements.items():
        result = result.replace(old, new)
    rules = vision_extraction_rules()
    match = _MANAGED_BLOCK.search(result)
    if match:
        before, after = result[:match.start()], result[match.end():]
    else:
        before, after = result.rstrip() + "\n\n", "\n"
    # 片方だけ残ったマーカーや二つ目のブロックを残すと、管理ブロックが重複して規則が二重に送られる。
    return _strip_managed_blocks(before) + rules + _strip_managed_blocks(after)


def managed_block_warning(template: str) -> str:
    """保存する内容と実行時に送る内容が異なる場合の利用者向け注意を返します。同じなら空文字。

    管理ブロック内の編集は保存されても実行時に共通規則へ戻るため、保存時に知らせる用途です。
    """
    if refine_image_retrieval_prompt(template).strip() == str(template).strip():
        return ""
    return (
        f"注意: `{RULES_START}` から `{RULES_END}` までは実行時に共通規則へ置き換えられます（無い場合は追加されます）。"
        "ブロック内の編集や対になっていないマーカーは送信内容に反映されません。"
        "編集欄には実際に送信される内容を表示しました。"
    )


def _strip_managed_blocks(text: str) -> str:
    return _ORPHAN_MARKER.sub("", _MANAGED_BLOCK.sub("", text))
