"""外部 SDK に依存しない既存データ契約。"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

@dataclass(frozen=True)
class RerankTextRank:
    """rerank 結果の元 document index と relevance score を保持します。"""
    index: int
    relevance_score: float | None = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConditionResultPairOutput(_StrictModel):
    """LLM が返す条件ごとの判定結果ペアを表します。"""
    condition: str
    result: str
    visible_text: str


class DiagramEdgeOutput(_StrictModel):
    """図中の接続元、接続先、条件ラベルを保持します。"""
    source: str
    target: str
    label: str


class ChartSeriesOutput(_StrictModel):
    """Chart の系列名、読み取れる値、傾向を保持します。"""
    name: str
    values: list[str]
    trend: str


class FormFieldOutput(_StrictModel):
    """Form の項目名、表示値、選択状態を保持します。"""
    name: str
    value: str
    state: str


class PictureDescriptionOutput(_StrictModel):
    """Vision 説明で得た可視情報と検索補助 text を保持します。

    生成説明の本文にも回答生成にも使われない field は持ちません (#769)。
    """
    surrounding_context: str = ""
    correction_notes: str = ""
    main_topic: str
    visual_kind: str
    diagram_title: str
    diagram_nodes: list[str]
    diagram_edges: list[DiagramEdgeOutput]
    diagram_summary: str
    chart_title: str
    chart_type: str
    chart_axes: list[str]
    chart_legends: list[str]
    chart_series: list[ChartSeriesOutput]
    chart_summary: str
    table_headers: list[str]
    table_rows: list[list[str]]
    table_summary: str
    form_fields: list[FormFieldOutput]
    form_layout: str
    menu_route: str
    visible_screen_names: list[str]
    visible_buttons: list[str]
    visible_fields: list[str]
    visible_values: list[str]
    codes_and_errors: list[str]
    operation_steps: list[str]
    condition_result_pairs: list[ConditionResultPairOutput]
    exception_or_cautions: list[str]
    answerable_questions: list[str]
    query_rewrites: list[str]
    search_keywords: list[str]
    retrieval_text: str


class QueryRoutingOutput(_StrictModel):
    """検索戦略と独立した、検索前の暫定データ確認観点を保持します。

    routing_data_required の None は旧形式または未判定です。確認項目は実測値や
    最終的な不足判定ではなく、回答時に提供済み根拠と照合する対象です。
    """
    # 戦略は schema の enum で ID に制約する。OpenAI Responses と OCI Generative AI のどちらも strict schema を
    # 強制するため、LLM の出力がこの外に出ない。answer_models の定数を import すると循環するので文字列で書き、
    # answering._ROUTABLE_QUERY_STRATEGIES との一致はテストで固定する (#919)。
    strategy: Literal["simple_retrieval", "rag_fusion", "query_decomposition", "step_back_prompting", "hyde"]
    reason: str
    routing_data_required: bool | None = None
    routing_data_items: list[str] = []
    # 選んだ戦略の検索文。戦略選択と検索文生成を1回の呼出で行う。空なら従来の拡張呼出で補う。
    # 本数は後段の MAX_GENERATED_QUERY_COUNT で切り詰める。ここで上限を課すと超過時に回答全体が中断する。
    queries: list[str] = Field(default_factory=list)


class QueryExpansionOutput(_StrictModel):
    """LLM が生成した検索 query バリエーションを保持します。"""
    queries: list[str]


# CRAG 評価の出力契約は Field(description) に置く。構造化出力の JSON schema としてモデルへ渡るため、prompt に
# 出力例を載せない (#983)。判定規則そのものは CRAG_GRADE_PROMPT_TEMPLATE にある。
class CragAspectCheck(_StrictModel):
    """質問の必要観点ごとの支持と、候補内の一意な出典ID。"""
    aspect: str = Field(description="task_contract.required_aspects の観点名（business_object / requested_result / applicability / procedure など）。")
    status: Literal["supported", "missing", "data_confirmation"] = Field(
        description="supported=候補で説明できる。missing=どの候補にも無い。data_confirmation=一般規則はあり実値の照合だけが残る（requested_result / applicability のみ）。")
    source_ids: list[str] = Field(default_factory=list, description="観点を支持する候補の chunk_uid。supported / data_confirmation では必須。")
    reason: str = Field(description="判定の根拠を短く。data_confirmation では閲覧するデータと目的。")


class CragCandidateVerdict(_StrictModel):
    """候補（parent chunk）ごとの関連判定。CRAG の評価器と同じく文書単位の二値判定を先に行う。"""
    chunk_uid: str = Field(description="候補の chunk_uid。")
    relevant: bool = Field(description="候補が原質問と同じ業務・機能の種類について、要求された操作・規則・確認方法・制限（できない・機能が無い）のいずれかを説明していれば true。質問固有の値が無くてもよい。別の変更項目だけの手順、別のエラー文・コード、話題違いは false。")
    reason: str = Field(description="判定の根拠を一文で。")


class CragEnumeratedItem(_StrictModel):
    """列挙・網羅の問（task_contract.enumeration）で、候補に見える該当の項目 1 つと根拠（#1503）。

    件数・章の範囲の欠けはプログラムが数える（``quote`` が根拠の本文に無い項目は数えない）。
    """
    item: str = Field(description="該当の項目を短く（例: 入室の予約）。")
    source_ids: list[str] = Field(default_factory=list, description="項目が書かれた候補の chunk_uid。")
    quote: str = Field(description="候補の本文から、項目が書かれた箇所を文字を変えずに写した原文（40 字程度まで）。")


# 項目の順序は生成順でもある（strict schema）。候補ごとの判定 → 列挙の項目 → 観点 → 理由 → 結論の順に置き、結論を先に書かせない。
class CragRetrievalGradeOutput(_StrictModel):
    """CRAG retrieval の十分性判定と理由を保持します。"""
    candidate_verdicts: list[CragCandidateVerdict] = Field(default_factory=list, description="全候補の関連判定。候補ごとに独立に判定する。relevant=true の候補を回答に使う。")
    enumerated_items: list[CragEnumeratedItem] = Field(default_factory=list, description="task_contract.enumeration があるときだけ、候補に見える該当の項目をすべて、重ねずに資料の順で挙げる。候補に無い項目を推測で足さない。enumeration が null なら空の配列。")
    aspect_checks: list[CragAspectCheck] = Field(default_factory=list, description="required_aspects ごとの判定。relevant な候補を合わせて確認し、全観点を必ず列挙する。")
    reason: str = Field(description="十分・不足の理由を短く。不足なら欠けている観点と、どの候補が最も近いかを書く。")
    sufficient: bool = Field(description="同じ業務・機能の relevant な候補があり business_object を supported にできるなら true。他の観点の missing は不足ではなく aspect_checks と recovery_action で示す。")
    confidence: float = Field(description="判定の確からしさ（0.0〜1.0）。")
    rewritten_query: str = Field(description="sufficient=false のとき次回検索に使う短い日本語の検索文。十分なら空文字。")
    recovery_action: Literal["rewrite", "neighbor_children", "parent", "neighbor_parents"] = Field(
        default="rewrite", description="不足時の回復手段。rewrite=別の話題・対象を探す。neighbor_children / parent / neighbor_parents=recovery_options にある文脈拡張。")
    recovery_anchor_ids: list[str] = Field(default_factory=list, description="文脈拡張の起点にする、閲覧した適合候補の chunk_uid。rewrite なら空。")


class UsedImageOutput(_StrictModel):
    """回答生成で実際に参照した画像 evidence を表します。"""
    image_id: str
    source: str
    page: str
    look_at: str
    visible_evidence: str


class SectionExtractionQuote(_StrictModel):
    """長い章節の 1 回分から抜き出した原文 1 つ（#1437。抽出してから統合）。"""
    chunk_ref: str = Field(description="原文を取ったブロックの見出しの番号（[C1 …] の C1）をそのまま書く。")
    location: str = Field(description="ブロックの見出しにある場所（p.5、シート「…」A3:D6 など）をそのまま書く。無ければ空文字。")
    quote: str = Field(description="ブロックの本文の連続した一部を、文字・記号・数字・改行を変えずにそのまま写した原文。要約・言い換え・省略・補足をしない。")


class SectionExtractionOutput(_StrictModel):
    """長い章節の 1 回分から、質問に関係する原文を抜き出した結果（#1437）。"""
    quotes: list[SectionExtractionQuote] = Field(default_factory=list, description="質問への回答に必要な原文を、資料の順に。関係する記載が無ければ空の配列。")


class TextOutput(_StrictModel):
    """text-only LLM 呼び出しの構造化出力を表します。"""
    text: str


# 生成・監査の出力契約は Field(description) に置き、構造化出力の JSON schema としてモデルへ渡す (#986)。
# 項目の順序は strict schema の生成順でもあるため、根拠（request_coverage / items、reviews）→ 結論（summary /
# confidence、goal_alignment）の順に並べ、結論を先に書かせない。行動方針は GENERATE_SYSTEM_PROMPT / AUDIT_SYSTEM_PROMPT。
class GroundedItem(_StrictModel):
    """回答の1主張と、それを支える連続した原文引用の対。

    gap 以外は evidence_id と quote が必須。quote は決定的に原文照合され、
    一致しない item は公開されない。text は利用者向けの言い換えで、監査で
    不支持になると破棄され quote だけが逐語表示される。
    """
    request_id: str = Field(default="", description="この item が答える request_units の id。背景（kind=context）の要求には item を作らない。")
    kind: Literal["operation", "rule", "confirmation", "gap"] = Field(
        description="operation=利用者が行う操作（実施順）。rule=規則・可否・原因の説明。confirmation=照合・閲覧の案内。gap=資料から確認できない点（何が確認できないか、閲覧する資料・データと目的）。")
    text: str = Field(min_length=1, max_length=600, description="quote が支持する範囲だけを質問の言葉で説明する。quote にない画面名・ボタン・値・効果・原因・成功保証を足さない。")
    evidence_id: str = Field(default="", description="根拠の先頭にある [E1] 形式の番号（角括弧なし）。gap 以外は必須。")
    quote: str = Field(default="", max_length=600, description="その Evidence の本文から連続した原文をそのまま写す。改変・要約・結合は不可。gap 以外は必須。")
    applies: Literal["matched", "conditional", "unverified"] = Field(
        default="matched", description="matched=通常。conditional=原文に適用条件・限定が明記され、質問からその成立を確認できない。unverified はシステムが付ける値で出力しない。")
    condition: str = Field(default="", description="applies=conditional のとき、原文の条件の語句をそのまま写す。原文にない前提は作らない。")


class RequestCoverage(_StrictModel):
    """要求単位ごとに、どの根拠で答えるかの計画。items を書く前に根拠を選ばせる (#986)。"""
    request_id: str = Field(description="request_units の id。")
    evidence_ids: list[str] = Field(default_factory=list, description="この要求に使う根拠の evidence_id（[E1] 形式の番号）。無ければ空にし、items では kind=gap にする。")


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
                    "user=質問した利用者本人が知っている・決められる（自分のロール・権限を持つか、承認を得たか、どのシステム・申請・"
                    "アカウントのことか、付与先を個別とグループのどちらにするか、何をしたいか）。"
                    "system=業務システム・台帳・ログに記録された値で、システムやデータを読まないと分からない（対象レコードの設定値、"
                    "今の設定値、エラーの時刻のログの行、処理の状態、件数、台帳の実際の値）。"
                    "document=規程・マニュアル・コード表などの資料に書かれる（どの版が有効か・廃止されたか、規則・コードの定義）。"
                    "反例: 自分がロールを持つかは画面で確かめられても user。どの版が有効かは利用者の環境ではなく資料が決めるので document。"
                    "資料の例示値・記載時点の値が今の環境でも同じかは system。")
    already_known: bool = Field(
        description="true=質問・会話・「分かっている条件」・取得した根拠の原文から、値がすでに分かる"
                    "（例: 利用者が「承認はもらっています」と書いた承認の有無、根拠が「第1版は廃止」と書いている適用の版）。"
                    "false=まだ分からない。")
    decides: bool = Field(
        description="true=値によって回答の結論・選ぶ手順・可否が変わる（例: ロールの有無で自分で操作するか依頼するかが変わる）。"
                    "false=値に関わらず同じ手順・結論で、念のための確認・補足にとどまる。")

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
            return {**{key: facts[key] for key in ("who_can_answer", "already_known", "decides")},
                    **{key: item for key, item in value.items() if key != "kind"}}
        return value


class GroundedDraft(_StrictModel):
    """主張と引用の対で構成する回答草稿。本文の体裁は render が決定的に組み立てる。"""
    request_coverage: list[RequestCoverage] = Field(default_factory=list, max_length=64, description="要求単位ごとに使う根拠の計画。items より先に書く。")
    items: list[GroundedItem] = Field(max_length=40, description="主張と原文引用の対。機能・前提ごとに分け、見出しは書かない。")
    external_data_required: bool = Field(default=False, description="システムだけが持つ値（who_can_answer=system）に依存する結論が残るなら true。項目を external_data_items に挙げたときは、システムは項目の事実から決める。")
    external_data_items: list[ExternalDataItem] = Field(default_factory=list, max_length=10, description="回答の確定に確かめる点と照合の目的。項目ごとに誰が答えられるか・すでに分かっているか・結論がその値で分かれるかを書く。資料名は reference_materials に書く。")
    reference_materials: list[str] = Field(default_factory=list, max_length=10, description="回答の確定に閲覧が必要な別の資料（規程・コード定義表・マスタ一覧・別マニュアルなど）と照合の目的。資料名は根拠の原文か質問に出た名前だけを「」で囲んで書き、無ければ資料の種類で書く。")
    question_type: list[str] = Field(default_factory=list, description="質問の種類の分類語（任意）。")
    summary: str = Field(description="items で裏付けた結論だけを1〜2文で。引用できる根拠が無ければ空文字。見出し・番号・出典・Markdown は書かない。")
    confidence: Literal["high", "medium", "low"] = Field(description="high=主要な説明と適用条件に直接の根拠がある。medium=一部に不確実性がある。low=案内自体に根拠が乏しい。")

    def confirmation_items(self) -> list[dict[str, Any]]:
        """確認の項目の事実（`answer_envelope.settle_confirmation_items` の入力。#1492）。"""
        return [item.model_dump() for item in self.external_data_items]


class GroundedItemReview(_StrictModel):
    """1 item の局所監査。引用が言い換えを支持するかと、質問への適用性を分ける。"""
    index: int = Field(description="監査対象 items の index。")
    support: Literal["supported", "unsupported", "contradicted"] = Field(
        description="supported=quote が text を裏付ける。unsupported=text が quote（と function_context）にない操作・値・効果・原因を足している。contradicted=quote と逆。")
    applicability: Literal["matched", "conditional", "not_applicable", "unknown"] = Field(
        description="matched=質問の対象の種類に当てはまる（一般手順でよい）。conditional=原文の条件の成立が質問から確認できない。not_applicable=別の対象・項目・業務向け。unknown=対象の種類が同じか判断できない。")
    condition: str = Field(default="", description="conditional のとき原文の条件。")
    reason: str = Field(description="判定の根拠を短く。")


class GroundedRequestReview(_StrictModel):
    """原質問の要求単位ごとの充足。"""
    request_id: str = Field(description="request_units の id。")
    status: Literal["addressed", "partial", "missing", "context"] = Field(
        description="addressed=items が答えている。partial=一部。missing=無い。context=背景で答える対象ではない。")
    reason: str = Field(description="質問と同じ言語で短く。")


class GroundedAudit(_StrictModel):
    """監査は降格と不足の指摘だけを行い、原文一致した内容を削除しない。"""
    reviews: list[GroundedItemReview] = Field(max_length=40, description="各 item の局所監査。")
    request_reviews: list[GroundedRequestReview] = Field(default_factory=list, max_length=64, description="request_units の各 id の充足。")
    unused_evidence_ids: list[str] = Field(default_factory=list, max_length=8, description="回答に必要なのに items で使われていない根拠の evidence_id。重要な順。")
    summary_supported: bool = Field(description="summary が items の範囲を超える断定をしていなければ true。")
    goal_alignment: Literal["aligned", "partial", "off_target"] = Field(
        description="aligned=items 全体が目的に答えている。partial=一部だけ。off_target=別の対象・業務・目的に答えている。")
