"""外部 SDK に依存しない既存データ契約。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    StrictInt,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

MAX_ANSWER_PARTS = 40


@dataclass(frozen=True)
class RerankTextRank:
    """rerank 結果の元 document index と relevance score を保持します。

    question_relevance_score は原質問だけとの関連度。question_score_explicit が True の場合は
    None も原質問の未採点として保持し、それ以外の省略時だけ relevance_score を使います。
    """

    index: int
    relevance_score: float | None = None
    question_relevance_score: float | None = None
    question_score_explicit: bool = False


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# 図・画像の読み取りの各項目の意味は Field(description=...) に置く。プロンプトの出力契約とモデルの応答 schema の
# 両方の正本で、プロンプトの本文に同じ説明を重ねない（判断の原則は vision_prompt_rules.py の読取方針）。
class ConditionResultPairOutput(_StrictModel):
    """LLM が返す条件ごとの判定結果ペアを表します。"""

    condition: str = Field(description="条件、状態、前提、選択肢。限定語は原文で。")
    result: str = Field(description="その条件で起きる表示、動作、必要操作。見えていない結論は書かない。")
    visible_text: str = Field(description="根拠の短い可視テキスト。")


class DiagramNodeOutput(_StrictModel):
    """図の要素と、文字を持たない共有線の分岐点を区別する。"""

    id: str = Field(min_length=1, description="図内で一意の参照ID。見えない名称の代わりにはしない。")
    kind: Literal["node", "junction"] = Field(
        description="node=文字や形を持つ要素、junction=線が実際につながる分岐点。交差だけなら接続しない。"
    )
    label: str = Field(description="要素内の可視文字そのもの。文字のない分岐点は空文字。")


class DiagramEdgeOutput(_StrictModel):
    """隣り合う要素・分岐点をつなぐ一つの接続段と、その段だけの可視ラベル。"""

    source: str | None = Field(description="接続段の始点の diagram_nodes.id。画像外などで不明なら null とし direction=uncertain。")
    target: str | None = Field(description="接続段の終点の diagram_nodes.id。画像外などで不明なら null とし direction=uncertain。")
    label: str = Field(description="この接続段に付く可視ラベルの写し。共有段の条件は共有段だけに書き、枝に再配分しない。無ければ空文字。")
    direction: Literal["forward", "both", "none", "uncertain"] = Field(
        description="forward=始点から終点へ矢印、both=双方向、none=接続は明確で矢印なし、uncertain=接続先や向きを確認できない。"
    )


class ChartSeriesOutput(_StrictModel):
    """Chart の系列名、読み取れる値、傾向を保持します。"""

    name: str = Field(description="系列名。")
    values: list[str] = Field(description="確実に読める category=value だけ。目測値は入れない。")
    trend: str = Field(description="可視範囲の傾向。")


# 画面の項目の種類（#2404）。見た目の手がかりで決まる固定の値で、言語資源にはしない（表示の語は言語資源の
# content_labels.form_field_kinds）。判断できなければ unknown。
FORM_FIELD_KINDS = ("select", "text", "checkbox", "radio", "date", "button", "unknown")
FormFieldKind = Literal["select", "text", "checkbox", "radio", "date", "button", "unknown"]


def form_field_kind(value: object) -> str:
    """項目の種類を決まった値にする（大文字・空白の揺れはそろえ、決まった値の外と欠けは unknown）。"""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in FORM_FIELD_KINDS else "unknown"


class FormFieldOutput(_StrictModel):
    """Form の項目名、表示値、選択状態、項目の種類を保持します。"""

    name: str = Field(description="項目名。")
    value: str = Field(description="表示値。空欄なら空文字。")
    state: str = Field(description="selected / unselected / enabled / disabled / required / optional / unknown。")
    kind: FormFieldKind

    @field_validator("kind", mode="before")
    @classmethod
    def _known_kind(cls, value: object) -> str:
        """構造化出力を強制しない接続先の値の揺れ（大文字・空白・決まった値の外）は unknown に倒す。"""
        return form_field_kind(value)


class PictureDescriptionOutput(_StrictModel):
    """Vision 説明で得た可視情報と検索補助 text を保持します。

    生成説明の本文にも回答生成にも使われない field は持ちません (#769)。
    """

    surrounding_context: str = Field(
        default="", description="対象が属する見出し、表、セル/列、操作ステップ、キャプション、同行説明との関係。"
    )
    correction_notes: str = Field(default="", description="周辺文脈や OCR で補足・訂正した点とその根拠、未読箇所、出力の省略。")
    main_topic: str = Field(description="検索の中心になる画面名、操作名、制度名、機能名、概念名、図表名。")
    visual_kind: str = Field(
        description="flowchart / architecture_diagram / chart / table / form / screenshot / photo / logo / watermark / background / decorative / other から一つ。不明なら other。"
    )
    diagram_title: str = Field(description="図のタイトル。")
    diagram_nodes: list[DiagramNodeOutput] = Field(
        description="図の要素と共有線の分岐点。ノード間の接続は隣接する段に分けて diagram_edges へ書く。"
    )
    diagram_edges: list[DiagramEdgeOutput] = Field(description="図で見える接続。")
    diagram_summary: str = Field(description="図の主題の要約。接続・条件の対応は diagram_edges にだけ書く。")
    chart_title: str = Field(description="グラフのタイトル。")
    chart_type: str = Field(description="bar / line / pie / scatter / area / combination / other。")
    chart_axes: list[str] = Field(description="軸名、単位、期間、目盛り。")
    chart_legends: list[str] = Field(description="凡例名と識別情報。")
    chart_series: list[ChartSeriesOutput] = Field(description="系列ごとの読み取り。")
    chart_summary: str = Field(description="主要な傾向と比較。")
    table_headers: list[str] = Field(description="列見出し。")
    table_rows: list[list[str]] = Field(
        description="見出しを除く行を上から下、セルを左から右の順で、同じ行のセルの配列にする。画像セルもその位置に入れ、空セルは空文字で残し、複数行を一行に統合しない。"
    )
    table_summary: str = Field(description="表の主題、比較軸、主要な条件と結果の要約。")
    form_fields: list[FormFieldOutput] = Field(description="入力欄・選択項目の値と状態。")
    form_layout: str = Field(description="Form 名、セクション、列、グループ、配置関係。")
    menu_route: str = Field(description="ナビゲーション経路、メニュー階層、パンくず、ページ遷移。")
    visible_screen_names: list[str] = Field(description="画面名、ページ名、ダイアログ名、フォーム名、帳票名。")
    visible_buttons: list[str] = Field(description="ボタン、リンク、タブ、メニュー項目、ショートカット表示。")
    visible_fields: list[str] = Field(description="入力欄、選択項目、チェックボックス、表の列名、設定項目。")
    visible_values: list[str] = Field(description="読める選択値、状態、日付、件数、設定値、コード。項目名と値をセットで。")
    codes_and_errors: list[str] = Field(description="エラーコード、各種コード、パラメータ名、条件式、API 名、ファイル名、キー名。")
    operation_steps: list[str] = Field(description="読み取れる操作手順。順番が読めれば順番を保つ。")
    condition_result_pairs: list[ConditionResultPairOutput] = Field(description="条件と結果の対応。")
    exception_or_cautions: list[str] = Field(description="例外、対象外、制限、前提条件、禁止事項、未対応、別途必要な資料や外部確認。")
    answerable_questions: list[str] = Field(description="この画像で回答できる問い合わせ文を複数。")
    query_rewrites: list[str] = Field(description="利用者が言いそうな別表現、略称、言い換え。")
    search_keywords: list[str] = Field(description="重要語、正式名、別名、略称、表記ゆれ、コード、エラー、画面名、項目名。")
    retrieval_text: str = Field(
        description="RAG 索引用の本文。最初にこの画像だけで回答できる結論を書き、画面名、操作名、項目名、条件、結果、注意、検索されそうな自然表現を自然文でまとめる。複数ケースは条件→結果を分け、見えていない分岐・値・可否・実施済み結果は書かない。"
    )


class TextOutput(_StrictModel):
    """text-only LLM 呼び出しの構造化出力を表します。"""

    text: str


# 生成の出力契約は Field(description) に置き、構造化出力の JSON schema としてモデルへ渡す (#986)。
# 項目の順序は strict schema の生成順でもあるため、根拠（request_coverage / items、reviews）→ 結論（summary /
# confidence、goal_alignment）の順に並べ、結論を先に書かせない。行動方針は generation_system_prompt（grounded.py）。
class GroundedCitation(_StrictModel):
    """item の出典 1 つ（根拠の番号と引用する行）。引用の原文はシステムが取り出す（#2830）。"""

    # モデル入力ではなく、本検証で毎回再作成する抽出の記録。旧 round の記録も信用しない。
    _extraction: dict[str, str] = PrivateAttr(default_factory=dict)
    # 生成用の整数範囲からの変換記録。None は保存済みの lines 形式で、モデルの出力には含めない。
    _wire_range: tuple[int | None, int | None] | None = PrivateAttr(default=None)
    # この呼び出しで送達した不変の原文単位。モデルの出力や保存済み JSON から設定しない。
    _unit_binding: Any = PrivateAttr(default=None)

    evidence_id: str = Field(description="根拠の先頭にある [E1] 形式の番号（角括弧なし）。")
    lines: str = Field(
        default="",
        max_length=20,
        description="引用する行の範囲。行番号（`3| `）の付いた根拠では必ず、text を支える行の番号か範囲（例: 3、3-5）を書く。"
        "この出典が担う操作・値を含む行にする。複数の出典で text 全体を共同して支えてよい。"
        "行番号の無い根拠は空（根拠の全文が引用になる）。",
    )
    # 引用の原文。モデルには出力させず（応答の schema に出さない）、evidence_id と lines からシステムが取り出す（#2830）。
    quote: SkipJsonSchema[str] = Field(
        default="", description="引用の原文（システムが evidence_id と lines から取り出す。応答の schema には出さない）。"
    )


class GroundedItem(_StrictModel):
    """回答の1主張と、それを支える出典（連続した原文の引用）の対。

    gap 以外は出典が 1 つ以上必須。1 つの主張は、同じ内容を述べた別の文書の原文（規則の条項と、それを説明する
    手順書など）を出典に並べられる（#3526。ALCE・Anthropic Citations の形）。各出典は ID・版・範囲で原文を抽出し、
    抽出できない出典は公開されない。text は利用者向けの言い換え。

    検査の工程（``grounded.verify`` の後）では、1 つの item は出典を 1 つだけ持ち、``evidence_id`` / ``lines`` /
    ``quote`` はその出典を読む。同じ主張の別の出典は ``CheckedItem.also`` に持つ。
    """

    request_id: str = Field(
        default="", description="この item が答える request_units の id。背景（kind=context）の要求には item を作らない。"
    )
    kind: Literal["operation", "rule", "confirmation", "gap"] = Field(
        description="operation=利用者が行う操作（実施順）。rule=規則・可否・原因の説明。confirmation=照合・閲覧の案内。gap=資料から確認できない点（何が確認できないか、閲覧する資料・データと目的）。"
    )
    # コードが選択原文を入れる欄でもある。生成文の長さは生成wireで制限し、原文は切らない。
    text: str = Field(
        min_length=1,
        description="出典の原文が支持する範囲だけを質問の言葉で説明する。原文にない画面名・ボタン・値・効果・原因・成功保証を足さない。",
    )
    citations: list[GroundedCitation] = Field(
        default_factory=list,
        description="text を支える出典。gap 以外は 1 つ以上必須、gap は空。指定した出典の原文の集合で text 全体を支える。"
        "異なる出典が操作・対象・条件・順序の別々の部分を共同して支えてよく、各出典が text 全体を単独で支える必要はない。"
        "実際に指定していない原文で不足を補わない。"
        "同じ文書の同じ原文を重ねない。",
    )
    applies: Literal["matched", "conditional", "unverified"] = Field(
        default="matched",
        description="matched=通常。conditional=原文に適用条件・限定が明記され、質問からその成立を確認できない。unverified はシステムが付ける値で出力しない。",
    )
    condition: str = Field(default="", description="applies=conditional のとき、原文の条件の語句をそのまま写す。原文にない前提は作らない。")

    @property
    def evidence_id(self) -> str:
        """最初の出典の根拠の番号（出典が無ければ空）。"""
        return self.citations[0].evidence_id if self.citations else ""

    @property
    def lines(self) -> str:
        """最初の出典の引用する行の範囲。"""
        return self.citations[0].lines if self.citations else ""

    @property
    def quote(self) -> str:
        """最初の出典の引用の原文。"""
        return self.citations[0].quote if self.citations else ""

    def with_citation(self, **update: str) -> GroundedItem:
        """最初の出典を ``update``（evidence_id / lines / quote）で置き換えた写し。出典が無ければ作る。"""
        first = self.citations[0].model_copy(update=update) if self.citations else GroundedCitation(**{"evidence_id": "", **update})
        return self.model_copy(update={"citations": [first, *self.citations[1:]]})

    def single_citations(self) -> list[GroundedItem]:
        """出典ごとに 1 つの出典だけを持つ写し（gap・出典の無い item は自身だけ）。"""
        if len(self.citations) <= 1:
            return [self]
        return [self.model_copy(update={"citations": [citation]}) for citation in self.citations]


class AnswerTableRow(_StrictModel):
    """回答の表の 1 行。``item_indexes`` はこの行の値を裏付ける items の index（行ごとの根拠。#4796）。"""

    cells: list[str] = Field(default_factory=list)
    item_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(default_factory=list)


class AnswerTable(_StrictModel):
    """回答の表（列名と行）。Markdown の表は ``answer_table.table_markdown`` がこの値から組み立てる（#4796）。"""

    columns: list[str] = Field(min_length=1)
    rows: list[AnswerTableRow] = Field(min_length=1)


class GroundedApplication(_StrictModel):
    """資料の事実・規則から導く結論（計算・条件適用・資料間の組み合わせ）。

    前提は質問の原文の一部、規則は items の index で指し、計算は算式で書く。システムが前提の照合・規則の
    支持・算式の計算を確かめてから公開する。当てはめの正しさは生成のモデルの責務で、生成の後に LLM では確かめない。

    ``table`` は表で示す結論（#4796）。``result`` は表の題で、表の本文は ``table`` の列名と行からコードが組み立てる。
    表の結論は導く方法（reasoning）を持たなくてよい。
    """

    request_id: str = Field(default="", description="この当てはめが答える request_units の id。")
    premises: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="導出に使う質問の事実。使う場合だけ質問の原文の連続した一部を写す。資料だけから導く場合は空。質問に無い前提は書かない。",
    )
    # 選択原文の参照はコードが組み立てる。生成する結論数とは別で、実在はcheck_applicationsで検査する。
    item_indexes: list[int] = Field(
        default_factory=list,
        description="導出の根拠となる資料の事実・規則・操作の items の index（0 始まり）。引用のある item を1件以上指す。",
    )
    reasoning: str = Field(
        default="",
        max_length=600,
        description="規則を前提にどう当てはめたか。計算は「44万円＋180万円÷12＝59万円」のように数と＋−×÷＝の算式で書く。規則と前提に無い値・条件を足さない。",
    )
    result: str = Field(min_length=1, max_length=600, description="当てはめの結果（要求への答え）。1〜2 文。")
    # 表の結論は生成の要点の型（answer_parts_wire の table 型）からコードが作る。モデルに渡す schema には出さない。
    table: SkipJsonSchema[AnswerTable | None] = None

    @model_validator(mode="after")
    def _reasoning_unless_table(self) -> GroundedApplication:
        if not self.reasoning and self.table is None:
            raise ValueError("reasoning（導く方法）が空です。表で示す結論のほかは、依存する原文と既知値から導く方法を書いてください。")
        return self


class RequestEvidenceScope(_StrictModel):
    """質問の対象と、各根拠が扱う対象の対応（下流の検査と保存の形）。link_quote はコードが番号の範囲から切り出す。"""

    evidence_id: str
    target: str
    relation: Literal["direct", "linked", "unrelated"]
    link_quote: str = ""


class RequestCoverage(_StrictModel):
    """要求単位ごとに、どの根拠で答えるかの計画（下流の検査と保存の形）。evidence_ids はコードが evidence_scope から導く。"""

    request_id: str
    evidence_scope: list[RequestEvidenceScope] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RequestLinkRange(_StrictModel):
    """linked の関係を明記した本文の範囲。原文はコードが番号から切り出す（モデルは写さない）。"""

    evidence_id: str = Field(description="操作同士の関係を明記した本文がある Evidence の id。")
    line_start: StrictInt | None = Field(description="その本文の、行頭「番号|」の開始番号。番号が表示されない根拠は null。")
    line_end: StrictInt | None = Field(
        description="同じ本文の終了番号。開始番号以上。単一の行は開始と同じ値。番号が表示されない根拠は null。"
    )


class RequestEvidenceScopeRange(_StrictModel):
    """モデルが返す照合の行。"""

    evidence_id: str = Field(description="対象を照合する Evidence の id。")
    target: str = Field(description="この根拠が操作・説明する対象。原文の対象名を写す。")
    relation: Literal["direct", "linked", "unrelated"] = Field(
        description="direct=質問の対象・要求について、規則・要件・定義・手順・事実を述べる。linked=別対象だが、質問の操作の前提・後続とする関係が原文に明記されている。unrelated=別の対象・独立した操作。同じ文書や頁にあるだけでは linked にしない。"
    )
    link: RequestLinkRange | None = Field(
        default=None, description="linked のときの、関係を明記した本文の範囲。関係が書かれていなければ unrelated とする。それ以外は null。"
    )


class RequestCoverageRange(_StrictModel):
    """モデルが返す、要求単位ごとの根拠の計画。items を書く前に根拠を選ばせる (#986)。"""

    request_id: str = Field(description="request_units の id。")
    evidence_scope: list[RequestEvidenceScopeRange] = Field(
        default_factory=list,
        description="この要求の説明に使う根拠（relation=direct）と、原文に関係が明記された前提・後続操作（relation=linked）、関係の判断が要る根拠を行にする。"
        "各 evidence_id（[E1] 形式の番号）を1回ずつ、質問の対象との関係を決める。"
        "同じ内容を述べた根拠が複数の文書にあれば（規則・要件・定義を定めた条項と、それを説明・運用する手順書など）、文書ごとに行にする。"
        "同じ文書・頁にあるだけの別操作を direct・linked にしない。items はこの計画の根拠だけで作る。無ければ空にし kind=gap にする。",
    )


class ExternalDataItem(_StrictModel):
    """回答の確定に確かめる点 1 件と、観察できる事実 (#1453、#1492)。

    モデルには分類（どの対応にするか）を答えさせず、項目ごとに観察できる事実だけを答えさせる: 誰が答えられるか
    （who_can_answer）、すでに分かっているか（already_known）、結論がその値で分かれるか（decides）。対応は
    `answer_envelope.settle_confirmation_items` の規則で決める（system だけが持つ値 → needs_environment_data、
    利用者が答えられる条件 → conditional、別の資料 → 資料の確認）。
    """

    text: str = Field(description="確かめる点と照合の目的（例: 「対象の取引先の支払区分の設定値（どの手順に当たるかを決める）」）。")
    who_can_answer: Literal["user", "system", "document"] = Field(
        description="この点の値を答えられるもの。3 つは排他で、値の出どころで決める。"
        "user=質問した利用者本人が自分の状況として答えられる・決められる（自分のロール・権限を持つか、承認を得たか、"
        "どのシステム・申請・アカウントのことか、対象のアカウントの種類（検証用か・共用か）、付与先を個別とグループの"
        "どちらにするか、何をしたいか）。"
        "document=KB の資料に載っている事実（規程・マニュアル・コード表の規則・定義、台帳・一覧・表の値、どの版が"
        "有効か・廃止されたか）。取得した根拠に該当の行・記載があれば値は分かるので挙げず、無ければ document にする。"
        "値が対象で決まり、対象が質問で特定されていなければ、対象を確かめる user の項目だけを挙げる。"
        "system=稼働中のシステムにだけあり、資料にも利用者の答えにも無い値（今の設定値、エラーの時刻のログの行、"
        "処理の状態・実行の結果、件数・実績のデータ）。資料の例示値・記載時点の値が今の環境でも同じかは system。"
        "反例: 対象のシステムの台帳の担当部署は台帳が資料なので system ではなく document（画面で確かめられても user にしない）。"
    )
    already_known: bool = Field(
        description="true=質問・会話・「分かっている条件」・取得した根拠の原文から、値がすでに分かる"
        "（例: 利用者が「承認はもらっています」と書いた承認の有無、根拠が「第1版は廃止」と書いている適用の版）。"
        "true の点は聞き直さない。false=まだ分からない。"
    )
    decides: bool = Field(
        description="true=値によって回答の結論・選ぶ手順・可否が変わる（例: ロールの有無で自分で操作するか依頼するかが変わる）。"
        "false=値に関わらず同じ手順・結論で、念のための確認・補足にとどまる。"
    )

    @model_validator(mode="before")
    @classmethod
    def _facts_from_legacy_kind(cls, value: Any) -> Any:
        # 構造化出力を強制できない接続の応答・#1492 より前の記録は、文字列だけか種類（kind）だけを持つ。欠けた事実は
        # 保守的に埋める（answer_envelope.confirmation_item_facts と同じ写し）: kind から誰が答えられるかを写し
        # （種類も無ければシステムの値）、未確認・結論を分ける項目とする。モデルが返した値はそのまま検証する。
        from rag_engine.generation.answer_envelope import confirmation_item_facts

        if isinstance(value, str):
            return confirmation_item_facts(value)
        if isinstance(value, dict):
            facts = confirmation_item_facts(value)
            return {
                **{key: facts[key] for key in ("who_can_answer", "already_known", "decides")},
                **{key: item for key, item in value.items() if key != "kind"},
            }
        return value


class GuideRequirementCoverage(_StrictModel):
    requirement_id: str = Field(description="guide_contracts の要件 id。")
    evidence_ids: list[str] = Field(
        max_length=8, description="この要件の説明を支える Evidence の id。ガイドの要件そのものは文書の仕様を示す根拠にしない。"
    )
    gap: str = Field(description="根拠が不足するとき、確認できない操作・条件と確認先。根拠があれば空文字。")


class AnswerPart(_StrictModel):
    """質問契約の要求が含む個別の要点と、その出力への参照。"""

    id: str = Field(min_length=1, max_length=80, description="この回答内で一意の要点ID。")
    request_id: str = Field(description="元の request_units の id。背景の要求は含めない。")
    text: str = Field(
        min_length=1,
        max_length=600,
        description="質問が求める要点。任意の補足は必須にしない。",
    )
    result_kind: Literal["direct", "derived", "gap"] = Field(description="この要点の答えが直接記載、導出、不足のいずれか。")
    item_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list, max_length=40, description="この要点に答える items の index。"
    )
    application_indexes: list[Annotated[int, Field(strict=True, ge=0, lt=MAX_ANSWER_PARTS)]] = Field(
        default_factory=list, max_length=MAX_ANSWER_PARTS, description="この要点に答える applications の index。"
    )


class DefaultResponseLanguage(_StrictModel):
    kind: Literal["default"] = Field(default="default", description="原質問に回答言語の明示指定が無い。")


class ExplicitResponseLanguage(_StrictModel):
    kind: Literal["explicit"] = Field(default="explicit", description="原質問に回答言語の明示指定がある。")
    quote: str = Field(
        min_length=1,
        description="原質問で回答言語を指定している語句だけを、連続した原文のまま写す。言語コードへの変換や、原質問に無い言語名の追加はしない。",
    )


class ResponseLanguage(_StrictModel):
    """回答言語の指定原文または指定なしを、一度だけ次段へ渡す。"""

    invalid: SkipJsonSchema[bool] = False

    selection: DefaultResponseLanguage | ExplicitResponseLanguage = Field(
        default_factory=DefaultResponseLanguage,
        discriminator="kind",
        description="回答言語の明示指定だけを原質問から選ぶ。質問本文・引用原文・SQLが何語で書かれたかの分類ではない。",
    )

    @model_validator(mode="wrap")
    @classmethod
    def _isolate_language_error(cls, data: Any, handler: ModelWrapValidatorHandler[ResponseLanguage]) -> ResponseLanguage:
        """表示言語の不正だけで、生成済みの事実や推導を捨てない。"""
        try:
            return handler(data)
        except ValidationError:
            return cls.model_construct(selection=DefaultResponseLanguage(), invalid=True)


class GroundedDraft(_StrictModel):
    """主張と引用の対で構成する回答草稿。本文の体裁は render が決定的に組み立てる。"""

    # SDK が返した本文。モデルの出力schemaや通常のmodel_dumpには入れない。
    _provider_output_text: str | None = PrivateAttr(default=None)
    # 検証の前に最初の 1 件へ寄せた、同じ内容の entry の数（欄ごと。#3849）。原文は _provider_output_text に残る。
    _collapsed_duplicates: dict[str, int] = PrivateAttr(default_factory=dict)
    # 同じ回答の修正では、要求計画の表示番号も送達時の版へ結ぶ（公開の JSON へは含めない）。
    _citation_snapshots: tuple[Any, ...] = PrivateAttr(default=())
    # 新wireの摘要は既出出力の選択だけ。依存が非公開になれば機械的に保留する。
    _composed_summary: bool = PrivateAttr(default=False)
    # 新しい生成の自己申告。本文を公開する条件には使わず、既存の履歴は未宣言のまま読む。
    _response_kind: str | None = PrivateAttr(default=None)

    response_language: ResponseLanguage = Field(
        default_factory=ResponseLanguage, description="最初に決めた回答言語。次段はこの契約を保持する。"
    )

    request_coverage: list[RequestCoverage] = Field(
        default_factory=list, max_length=64, description="要求単位ごとに使う根拠の計画。items より先に書く。"
    )
    guide_coverage: list[GuideRequirementCoverage] = Field(
        default_factory=list,
        max_length=200,
        description="guide_contracts の各要件に使う根拠の計画。全 id を1回ずつ、前提の手順から並べる。kind=evidence は引用元と主張の対応を検査する要件で、その指示の復唱は不要。kind=completion は items で説明する操作・条件・除外・確認先。どちらも足りなければ gap に残す。",
    )
    answer_parts: list[AnswerPart] = Field(
        default_factory=list,
        max_length=64,
        description="各要求の必要な要点を先に分け、直接記載・導出・不足の出力をそれぞれ参照する。同じ要求IDだけで複合要求を完了にしない。",
    )
    items: list[GroundedItem] = Field(description="主張と原文引用の対。機能・前提ごとに分け、見出しは書かない。")
    applications: list[GroundedApplication] = Field(
        default_factory=list,
        max_length=MAX_ANSWER_PARTS,
        description="計算・条件の適用・複数資料の組み合わせで導く結論をすべて置く。items は資料の直接記載。必要な根拠と前提がそろえば、結果そのものが原文に無くてもここで答える。",
    )
    external_data_required: bool = Field(
        default=False,
        description="システムだけが持つ値（who_can_answer=system）に依存する結論が残るなら true。項目を external_data_items に挙げたときは、システムは項目の事実から決める。",
    )
    external_data_items: list[ExternalDataItem] = Field(
        default_factory=list,
        max_length=10,
        description="回答の確定に確かめる点と照合の目的。項目ごとに誰が答えられるか・すでに分かっているか・結論がその値で分かれるかを書く。資料名は reference_materials に書く。",
    )
    reference_materials: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="回答の確定に閲覧が必要な別の資料（規程・コード定義表・マスタ一覧・別マニュアルなど）と照合の目的。資料名は根拠の原文か質問に出た名前だけを「」で囲んで書き、無ければ資料の種類で書く（例: 料金表）。名称・版・所在・URL・内容を創作しない。",
    )
    question_type: list[str] = Field(default_factory=list, description="質問の種類の分類語（任意）。")
    summary: str = Field(
        description="出力済みの items と applications の結果だけを1〜2文に要約する。依存先をそれぞれの index 欄で明示し、新たな導出を加えない。根拠が無ければ空文字。見出し・番号・出典・Markdown は書かない。"
    )
    summary_item_indexes: list[Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=list,
        max_length=40,
        description="summary の全事実を支える items の 0 始まりの index。条件・否定・例外を保ち、本文にない事実を加えない。summary が空なら空配列。",
    )
    summary_application_indexes: list[Annotated[int, Field(strict=True, ge=0, lt=MAX_ANSWER_PARTS)]] = Field(
        default_factory=list,
        max_length=MAX_ANSWER_PARTS,
        description="summary が要約する applications の0始まりの index。導出結果への依存は items の代わりにこちらへ書く。summary が空なら空配列。",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="high=主要な説明と適用条件に直接の根拠がある。medium=説明・適用性の一部に不確実性がある。low=案内自体に根拠が乏しい。"
        "個案判断に必要な実値や別資料が未閲覧という理由だけで low にしない。案内の適用業務・画面・版が未確定なら high にしない。high は個案の最終解決や操作の実行済みを意味しない。"
    )

    @model_validator(mode="wrap")
    @classmethod
    def _collapse_duplicate_entries(cls, data: Any, handler: ModelWrapValidatorHandler[GroundedDraft]) -> GroundedDraft:
        """同じ内容の計画・説明の繰り返しを最初の 1 件に寄せてから検証する（#3849）。

        長い出力では、モデルが同じ要求の計画を内容を変えずに上限まで繰り返すことがある。寄せるのは内容がすべて
        同じ entry だけで、内容の違う entry は 1 件も落とさない（件数の上限は寄せた後の件数で判定するので、内容の
        違う entry が上限を超える出力は従来どおり検証で不合格になる）。items を寄せたら、当てはめが指す index を
        寄せた先に付け替える。寄せた数は ``_collapsed_duplicates`` に残す。
        """
        collapsed: dict[str, int] = {}
        if isinstance(data, Mapping):
            data = dict(data)
            for field in ("request_coverage", "guide_coverage"):
                kept, _index, removed = _collapse_identical(data.get(field))
                if removed:
                    data[field] = kept
                    collapsed[field] = removed
            kept, index, removed = _collapse_identical(data.get("items"))
            if removed:
                data["items"] = kept
                collapsed["items"] = removed
                if "applications" in data:
                    data["applications"] = _reindex_applications(data["applications"], index)
                if "answer_parts" in data:
                    data["answer_parts"] = _reindex_applications(data["answer_parts"], index)
                indexes = data.get("summary_item_indexes")
                if isinstance(indexes, list) and all(type(value) is int for value in indexes):
                    data["summary_item_indexes"] = list(dict.fromkeys(index.get(value, value) for value in indexes))
        draft = handler(data)
        if collapsed:
            draft._collapsed_duplicates = collapsed
        return draft

    def confirmation_items(self) -> list[dict[str, Any]]:
        """確認の項目の事実（`answer_envelope.settle_confirmation_items` の入力。#1492）。"""
        return [item.model_dump() for item in self.external_data_items]


def _entry_key(entry: Any) -> str | None:
    """entry の内容の比較の鍵（JSON の object だけ。比べられない値は None で、寄せない）。"""
    if not isinstance(entry, Mapping):
        return None
    try:
        return json.dumps(entry, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _collapse_identical(entries: Any) -> tuple[Any, dict[int, int], int]:
    """内容がすべて同じ entry を最初の 1 件に寄せた一覧、元の index → 寄せた後の index、寄せた数。"""
    if not isinstance(entries, list):
        return entries, {}, 0
    kept: list[Any] = []
    first: dict[str, int] = {}
    index: dict[int, int] = {}
    for position, entry in enumerate(entries):
        key = _entry_key(entry)
        if key is not None and key in first:
            index[position] = first[key]
            continue
        if key is not None:
            first[key] = len(kept)
        index[position] = len(kept)
        kept.append(entry)
    return kept, index, len(entries) - len(kept)


def _reindex_applications(applications: Any, index: Mapping[int, int]) -> Any:
    """当てはめの ``item_indexes`` を、寄せた後の items の index にする（範囲外の値はそのまま検査に回す）。"""
    if not isinstance(applications, list):
        return applications
    result: list[Any] = []
    for application in applications:
        indexes = application.get("item_indexes") if isinstance(application, Mapping) else None
        if not isinstance(indexes, list):
            result.append(application)
            continue
        moved: list[Any] = []
        for value in indexes:
            value = index.get(value, value) if type(value) is int else value
            # 寄せた 2 つの item を指していたら 1 回にする（同じ item を 2 回当てはめない）。
            if type(value) is int and value in moved:
                continue
            moved.append(value)
        result.append({**application, "item_indexes": moved})
    return result
