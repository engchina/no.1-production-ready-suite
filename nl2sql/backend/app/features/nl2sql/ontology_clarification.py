"""Guided QuerySession 向けの決定論的な質問計画と回答反映。

LLM は ``ontology_router._interpret_question`` で構造化 intent を作る責務に限定し、
このモジュールは profile view 内の候補だけを質問へ変換する。回答で受理する ID も
同じ候補から再検証し、client が任意の Ontology ID を注入できないようにする。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .ontology_catalog import SchemaOntology
from .ontology_models import (
    ClarificationAnswer,
    ClarificationAnswerKind,
    ClarificationCategory,
    ClarificationEvidenceSource,
    ClarificationOption,
    ClarificationQuestion,
    ClarificationState,
    ClarificationStatus,
    IntentAmbiguity,
    IntentDimension,
    IntentEntity,
    IntentMetric,
    IntentSummaryItem,
    IntentTimeRange,
    OntologyNode,
    OntologyNodeKind,
    ProfileOntologyView,
    QuerySession,
    QuestionIntentGraph,
)
from .ontology_store import stable_ontology_id

CLARIFICATION_SCHEMA_VERSION = "guided_clarification_v1"
CLARIFICATION_PROMPT_VERSION = "deterministic_first_v2"
MAX_GUIDED_TURNS = 4

_REQUEST_ACTION_MARKERS = (
    "表示",
    "検索",
    "集計",
    "確認",
    "取得",
    "抽出",
    "一覧",
    "教えて",
    "求め",
    "知り",
    "比較",
    "並べ",
    "ランキング",
    "計算",
)
_REQUEST_STEMS = ("表示", "検索", "集計", "確認", "取得", "抽出", "比較", "計算")

_PRIORITY: dict[ClarificationCategory, int] = {
    ClarificationCategory.BUSINESS_MEANING: 0,
    ClarificationCategory.RELATIONSHIP_PATH: 1,
    ClarificationCategory.FILTER_VALUE: 2,
    ClarificationCategory.TIME_RANGE: 3,
    ClarificationCategory.GRANULARITY: 4,
    ClarificationCategory.OUTPUT: 5,
}


def enrich_guided_intent(intent: QuestionIntentGraph) -> QuestionIntentGraph:
    """SQL への影響が大きい不足だけを blocking ambiguity として追加する。"""

    updated = intent.model_copy(deep=True)
    codes = {item.code for item in updated.ambiguities}
    if updated.metrics and updated.time_range is None and "time_range_required" not in codes:
        updated.ambiguities.append(
            IntentAmbiguity(
                id=stable_ontology_id("intent_ambiguity", "time_range_required"),
                code="time_range_required",
                message_ja="集計対象の期間を確認してください。",
                options=["今月", "先月", "今年", "昨年"],
                blocking=True,
            )
        )
    needs_granularity = any(
        token in updated.question_effective for token in ("推移", "別", "ごと", "毎", "内訳")
    )
    if needs_granularity and not updated.granularity and "granularity_required" not in codes:
        updated.ambiguities.append(
            IntentAmbiguity(
                id=stable_ontology_id("intent_ambiguity", "granularity_required"),
                code="granularity_required",
                message_ja="集計結果をどの単位でまとめるか確認してください。",
                options=["日別", "月別", "年別", "集計のみ"],
                blocking=True,
            )
        )
    return updated


def build_clarification_state(
    session: QuerySession,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
) -> ClarificationState:
    intent = session.intents[-1]
    unresolved = [item for item in intent.ambiguities if item.blocking and not item.resolved]
    questions = [_question_for_ambiguity(item, intent, ontology, view) for item in unresolved]
    questions.sort(key=lambda item: (_PRIORITY[item.category], item.id))
    answered_ids = {
        turn.question.ambiguity_id
        for turn in session.clarification_turns
        if turn.question.blocking and turn.question.ambiguity_id
    }
    # LLM / retrieval の診断文は利用者向けではないため、未確認一覧にも
    # 実際に回答できる形へ変換した質問文だけを公開する。
    missing = [question.prompt_ja for question in questions]
    required_total = len(answered_ids) + len(unresolved)
    unanswerable = any(
        question.category == ClarificationCategory.RELATIONSHIP_PATH and not question.options
        for question in questions
    ) or (not view.node_ids and bool(unresolved))
    if unanswerable:
        status = ClarificationStatus.UNANSWERABLE
        message = (
            "この Profile では質問に必要な業務対象または承認済みの関係を確認できません。"
            "管理者に Profile / Ontology の定義を依頼してください。"
        )
        current_question = None
    elif questions:
        status = ClarificationStatus.NEEDS_ANSWER
        message = "SQL を正しく生成するため、必要な条件を確認します。"
        current_question = questions[0]
    else:
        status = ClarificationStatus.READY_TO_CONFIRM
        message = "SQL 生成に必要な情報を確認できました。"
        current_question = None
    return ClarificationState(
        status=status,
        current_question=current_question,
        remaining_questions=questions,
        intent_summary=_intent_summary(session, intent),
        required_total=required_total,
        required_confirmed=len(answered_ids),
        missing_required=missing,
        assumptions=_assumptions(session, intent),
        turn_count=len(session.clarification_turns),
        manual_completion_required=len(session.clarification_turns) >= MAX_GUIDED_TURNS
        and bool(unresolved),
        can_generate_sql=status == ClarificationStatus.READY_TO_CONFIRM,
        schema_version=CLARIFICATION_SCHEMA_VERSION,
        message_ja=message,
    )


def apply_clarification_answer(
    intent: QuestionIntentGraph,
    question: ClarificationQuestion,
    answer: ClarificationAnswer,
    ontology: SchemaOntology,
) -> QuestionIntentGraph:
    """現在表示中の question に対する検証済み回答だけを intent へ反映する。"""

    option_by_id = {item.id: item for item in question.options}
    selected = [
        option_by_id[item_id] for item_id in answer.selected_option_ids if item_id in option_by_id
    ]
    unknown_ids = set(answer.selected_option_ids) - option_by_id.keys()
    if unknown_ids:
        raise ValueError("表示されていない選択肢は回答に利用できません。")
    free_text = answer.free_text.strip()
    if len(answer.selected_option_ids) != len(set(answer.selected_option_ids)):
        raise ValueError("同じ選択肢を重複して指定できません。")
    supplied_kinds = sum((bool(selected), bool(free_text), answer.structured_value is not None))
    if supplied_kinds > 1:
        raise ValueError("選択肢・自由入力・構造化入力のいずれか 1 つだけを回答してください。")
    if question.answer_kind == ClarificationAnswerKind.SINGLE_SELECT and len(selected) > 1:
        raise ValueError("この質問では選択肢を 1 つだけ選んでください。")
    if not selected and not free_text and answer.structured_value is None:
        raise ValueError("選択肢を選ぶか、自由入力を記入してください。")
    if free_text and not question.allow_free_text:
        raise ValueError("この質問では自由入力を利用できません。")

    updated = intent.model_copy(deep=True)
    resolution_parts = [item.label_ja for item in selected]
    if free_text:
        resolution_parts.append(free_text)
    resolution = "、".join(resolution_parts) or str(answer.structured_value)
    for ambiguity in updated.ambiguities:
        if ambiguity.id == question.ambiguity_id:
            ambiguity.resolution = resolution
            ambiguity.resolved = True

    node_by_id = {node.id: node for node in ontology.nodes}
    selected_nodes = [
        node_by_id[node_id]
        for option in selected
        for node_id in option.ontology_node_ids
        if node_id in node_by_id
    ]
    if question.category in {
        ClarificationCategory.BUSINESS_MEANING,
        ClarificationCategory.OUTPUT,
    }:
        _append_selected_concepts(updated, selected_nodes)
    elif question.category == ClarificationCategory.RELATIONSHIP_PATH:
        path_id = next(
            (item.relationship_path_id for item in selected if item.relationship_path_id), ""
        )
        if path_id:
            updated.selected_path_id = path_id
    elif question.category == ClarificationCategory.TIME_RANGE:
        value = next(
            (item.structured_value for item in selected if item.structured_value is not None),
            answer.structured_value,
        )
        if isinstance(value, dict):
            updated.time_range = IntentTimeRange.model_validate(value)
        elif free_text:
            updated.time_range = IntentTimeRange(relative_expression=free_text)
    elif question.category == ClarificationCategory.GRANULARITY:
        value = next(
            (item.structured_value for item in selected if item.structured_value is not None),
            answer.structured_value,
        )
        updated.granularity = "" if value in {None, "none"} else str(value)

    if resolution:
        updated.question_effective = _render_clarified_question(
            updated.question_effective,
            question.category,
            resolution,
        )
    updated.confidence = min(1.0, updated.confidence + 0.1)
    return updated


def _render_clarified_question(
    current_question: str,
    category: ClarificationCategory,
    resolution: str,
) -> str:
    """確認回答を、単独で再検索できる自然な要求文へ反映する。"""

    base = _clean_question_text(current_question)
    answer = resolution.strip().rstrip("。！？!?")
    if not answer:
        return _request_sentence(base)

    is_fragment = not any(marker in base for marker in _REQUEST_ACTION_MARKERS)
    if category == ClarificationCategory.OUTPUT and is_fragment:
        return f"{base}について、検索結果には{answer}を表示してください。"

    request = _request_sentence(base)
    if category == ClarificationCategory.TIME_RANGE:
        return f"{answer}を対象に、{request}"

    detail = {
        ClarificationCategory.BUSINESS_MEANING: f"対象とする業務上の意味は{answer}です。",
        ClarificationCategory.RELATIONSHIP_PATH: (
            f"業務対象の関連付けには{answer}を使用してください。"
        ),
        ClarificationCategory.FILTER_VALUE: f"絞り込み条件は{answer}です。",
        ClarificationCategory.GRANULARITY: (
            "期間全体を一つに集計してください。"
            if answer == "集計のみ"
            else f"集計単位は{answer}です。"
        ),
        ClarificationCategory.OUTPUT: f"検索結果には{answer}を表示してください。",
    }.get(category, "")
    return f"{request}{detail}"


def _clean_question_text(value: str) -> str:
    """旧形式の確認メモと、入力全体を囲む引用符を除去する。"""

    lines = [
        line.strip()
        for line in value.splitlines()
        if line.strip() and not line.strip().startswith("確認事項（")
    ]
    cleaned = " ".join(lines).strip()
    quote_pairs = (('"', '"'), ("'", "'"), ("“", "”"), ("「", "」"))
    for opening, closing in quote_pairs:
        if len(cleaned) >= 2 and cleaned.startswith(opening) and cleaned.endswith(closing):
            cleaned = cleaned[len(opening) : -len(closing)].strip()
            break
    return cleaned.rstrip("。！？!?")


def _request_sentence(value: str) -> str:
    """元入力を、確認結果と連結できる完結した依頼文へ整える。"""

    question = value.strip().rstrip("。！？!?")
    if question.endswith(("ください", "下さい", "したい", "ほしい")):
        return f"{question}。"
    if question.endswith("する"):
        return f"{question[:-2]}してください。"
    if question.endswith(_REQUEST_STEMS):
        return f"{question}してください。"
    return f"{question}を表示してください。"


def merge_free_text_reinterpretation(
    intent: QuestionIntentGraph,
    reinterpreted: QuestionIntentGraph,
    question: ClarificationQuestion,
) -> QuestionIntentGraph:
    """自由入力から再解釈できた現在 slot と依存 ambiguity だけを取り込む。"""

    updated = intent.model_copy(deep=True)

    def append_unique(target: list[Any], candidates: Sequence[Any]) -> None:
        existing = {
            (getattr(item, "ontology_node_id", ""), getattr(item, "name_ja", "")) for item in target
        }
        for item in candidates:
            identity = (getattr(item, "ontology_node_id", ""), getattr(item, "name_ja", ""))
            if identity not in existing:
                target.append(item.model_copy(deep=True))
                existing.add(identity)

    if question.category == ClarificationCategory.BUSINESS_MEANING:
        append_unique(updated.entities, reinterpreted.entities)
        append_unique(updated.metrics, reinterpreted.metrics)
        append_unique(updated.dimensions, reinterpreted.dimensions)
    elif question.category == ClarificationCategory.RELATIONSHIP_PATH:
        approved_paths = [item for item in reinterpreted.candidate_paths if item.approved]
        if len(approved_paths) == 1:
            updated.selected_path_id = approved_paths[0].id
    elif question.category == ClarificationCategory.FILTER_VALUE and reinterpreted.filters:
        updated.filters = [item.model_copy(deep=True) for item in reinterpreted.filters]
    elif question.category == ClarificationCategory.TIME_RANGE and reinterpreted.time_range:
        updated.time_range = reinterpreted.time_range.model_copy(deep=True)
    elif question.category == ClarificationCategory.GRANULARITY and reinterpreted.granularity:
        updated.granularity = reinterpreted.granularity
    elif question.category == ClarificationCategory.OUTPUT:
        append_unique(updated.entities, reinterpreted.entities)
        append_unique(updated.metrics, reinterpreted.metrics)
        append_unique(updated.dimensions, reinterpreted.dimensions)
        updated.sorts = [item.model_copy(deep=True) for item in reinterpreted.sorts]
        if reinterpreted.limit is not None:
            updated.limit = reinterpreted.limit

    existing_codes = {item.code for item in updated.ambiguities}
    updated.ambiguities.extend(
        item.model_copy(deep=True)
        for item in reinterpreted.ambiguities
        if item.blocking and not item.resolved and item.code not in existing_codes
    )
    updated.confidence = max(updated.confidence, reinterpreted.confidence)
    return updated


def _category(code: str) -> ClarificationCategory:
    lowered = code.lower()
    if "join" in lowered or "relationship" in lowered or "path" in lowered:
        return ClarificationCategory.RELATIONSHIP_PATH
    if "time" in lowered or "period" in lowered or "date" in lowered:
        return ClarificationCategory.TIME_RANGE
    if "granularity" in lowered or "grain" in lowered or "aggregate" in lowered:
        return ClarificationCategory.GRANULARITY
    if "filter" in lowered or "value" in lowered:
        return ClarificationCategory.FILTER_VALUE
    if "output" in lowered or "sort" in lowered or "limit" in lowered:
        return ClarificationCategory.OUTPUT
    return ClarificationCategory.BUSINESS_MEANING


def _question_for_ambiguity(
    ambiguity: IntentAmbiguity,
    intent: QuestionIntentGraph,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
) -> ClarificationQuestion:
    category = _category(ambiguity.code)
    candidate_nodes = _candidate_nodes_for_ambiguity(category, ambiguity, ontology, view)
    if (
        category == ClarificationCategory.BUSINESS_MEANING
        and candidate_nodes
        and all(
            node.kind in {OntologyNodeKind.PROPERTY, OntologyNodeKind.COLUMN}
            for node in candidate_nodes
        )
    ):
        # 埋め込み検索の列候補は「意味を一つ選ぶ」質問ではなく、利用者が
        # 検索結果へ必要な項目を選ぶ質問として提示する。
        category = ClarificationCategory.OUTPUT
    options = _options_for(category, ambiguity, intent, candidate_nodes)
    prompt, reason, answer_kind = _question_copy(category, candidate_nodes)
    return ClarificationQuestion(
        id=stable_ontology_id("clarification_question", ambiguity.id),
        ambiguity_id=ambiguity.id,
        category=category,
        prompt_ja=prompt,
        reason_ja=reason,
        answer_kind=answer_kind,
        options=options[:5],
        allow_free_text=category != ClarificationCategory.RELATIONSHIP_PATH,
        blocking=ambiguity.blocking,
    )


def _options_for(
    category: ClarificationCategory,
    ambiguity: IntentAmbiguity,
    intent: QuestionIntentGraph,
    candidate_nodes: Sequence[OntologyNode],
) -> list[ClarificationOption]:
    if category == ClarificationCategory.TIME_RANGE:
        return [
            _value_option("今月", {"relative_expression": "今月"}),
            _value_option("先月", {"relative_expression": "先月"}),
            _value_option("今年", {"relative_expression": "今年"}),
            _value_option("昨年", {"relative_expression": "昨年"}),
        ]
    if category == ClarificationCategory.GRANULARITY:
        return [
            _value_option("日別", "day"),
            _value_option("月別", "month"),
            _value_option("年別", "year"),
            _value_option("集計のみ", "none"),
        ]
    if category == ClarificationCategory.RELATIONSHIP_PATH:
        return [
            ClarificationOption(
                id=stable_ontology_id("clarification_option", ambiguity.id, path.id),
                label_ja=path.name_ja,
                description_ja=path.explanation_ja,
                relationship_path_id=path.id,
                source=ClarificationEvidenceSource.ONTOLOGY,
                evidence_ja="承認済み Ontology 関係",
            )
            for path in intent.candidate_paths
            if path.approved
        ]

    if candidate_nodes:
        return [_node_option(ambiguity, node, category) for node in candidate_nodes]
    # LLM が任意の option を作っても選択肢として公開しない。Ontology / Profile scope
    # 内で検証できない値は自由入力として受け、再解釈・scope 検証を通す。
    return []


def _candidate_nodes_for_ambiguity(
    category: ClarificationCategory,
    ambiguity: IntentAmbiguity,
    ontology: SchemaOntology,
    view: ProfileOntologyView,
) -> list[OntologyNode]:
    if category in {
        ClarificationCategory.TIME_RANGE,
        ClarificationCategory.GRANULARITY,
        ClarificationCategory.RELATIONSHIP_PATH,
    }:
        return []
    visible = [node for node in ontology.nodes if node.id in view.node_ids]
    candidates = [
        node
        for option in ambiguity.options
        for node in visible
        if option
        in {
            node.id,
            node.technical_name,
            node.business_name_ja,
            *node.aliases,
        }
    ]
    if not candidates and category == ClarificationCategory.BUSINESS_MEANING:
        candidates = [
            node
            for node in visible
            if node.kind
            in {
                OntologyNodeKind.BUSINESS_ENTITY,
                OntologyNodeKind.BUSINESS_EVENT,
                OntologyNodeKind.METRIC,
                OntologyNodeKind.TABLE,
                OntologyNodeKind.VIEW,
            }
        ][:5]
    return _unique_nodes(candidates)


def _question_copy(
    category: ClarificationCategory,
    candidate_nodes: Sequence[OntologyNode],
) -> tuple[str, str, ClarificationAnswerKind]:
    if category == ClarificationCategory.TIME_RANGE:
        return (
            "どの期間を対象にしますか？",
            "期間によって集計対象が変わるため、必要な範囲を選んでください。",
            ClarificationAnswerKind.SINGLE_SELECT,
        )
    if category == ClarificationCategory.GRANULARITY:
        return (
            "どの単位で集計しますか？",
            "日別・月別など、選んだ単位で結果のまとまり方が変わります。",
            ClarificationAnswerKind.SINGLE_SELECT,
        )
    if category == ClarificationCategory.RELATIONSHIP_PATH:
        return (
            "業務対象をどの関係で結びますか？",
            "対象同士の関係によって、結果の意味や件数が変わります。",
            ClarificationAnswerKind.SINGLE_SELECT,
        )
    if category == ClarificationCategory.FILTER_VALUE:
        return (
            "どの条件で絞り込みますか？",
            "必要なデータだけを検索するため、絞り込み条件を確認します。",
            ClarificationAnswerKind.SINGLE_SELECT,
        )
    if category == ClarificationCategory.OUTPUT:
        if candidate_nodes:
            return (
                "検索結果に表示する項目を選んでください。",
                "クエリだけでは必要な表示項目を絞れませんでした。必要な項目をすべて選んでください。",
                ClarificationAnswerKind.MULTI_SELECT,
            )
        return (
            "検索結果に何を表示しますか？",
            "必要な結果を作るため、表示内容を確認します。",
            ClarificationAnswerKind.SINGLE_SELECT,
        )

    kinds = {node.kind for node in candidate_nodes}
    if kinds and kinds <= {OntologyNodeKind.METRIC}:
        return (
            "どの指標を使いますか？",
            "似た名前の指標で計算方法が異なるため、必要な指標をすべて選んでください。",
            ClarificationAnswerKind.MULTI_SELECT,
        )
    if kinds and kinds <= {
        OntologyNodeKind.BUSINESS_ENTITY,
        OntologyNodeKind.BUSINESS_EVENT,
        OntologyNodeKind.TABLE,
        OntologyNodeKind.VIEW,
    }:
        return (
            "どの業務対象について調べますか？",
            "検索対象の候補が複数あるため、意図した対象をすべて選んでください。",
            ClarificationAnswerKind.MULTI_SELECT,
        )
    return (
        "検索対象として意図しているものを選んでください。",
        "クエリだけでは必要な候補を絞れなかったため、意図したものをすべて選んでください。",
        ClarificationAnswerKind.MULTI_SELECT,
    )


def _value_option(label: str, value: Any) -> ClarificationOption:
    return ClarificationOption(
        id=stable_ontology_id("clarification_option", label, str(value)),
        label_ja=label,
        structured_value=value,
        source=ClarificationEvidenceSource.DEFAULT,
    )


def _node_option(
    ambiguity: IntentAmbiguity,
    node: OntologyNode,
    category: ClarificationCategory,
) -> ClarificationOption:
    if category == ClarificationCategory.OUTPUT:
        description = f"検索結果に「{node.business_name_ja}」を表示します。"
    elif node.kind == OntologyNodeKind.METRIC:
        description = f"「{node.business_name_ja}」の定義で集計します。"
    else:
        description = f"「{node.business_name_ja}」を検索対象として扱います。"
    return ClarificationOption(
        id=stable_ontology_id("clarification_option", ambiguity.id, node.id),
        label_ja=node.business_name_ja,
        description_ja=description,
        ontology_node_ids=[node.id],
        source=ClarificationEvidenceSource.ONTOLOGY,
        evidence_ja=node.technical_name,
    )


def _unique_nodes(nodes: Sequence[OntologyNode]) -> list[OntologyNode]:
    result: list[OntologyNode] = []
    seen: set[str] = set()
    for node in nodes:
        if node.id not in seen:
            seen.add(node.id)
            result.append(node)
    return result


def _physical_object_ids(node: OntologyNode) -> list[str]:
    if node.kind in {OntologyNodeKind.TABLE, OntologyNodeKind.VIEW}:
        return [node.id]
    return list(
        dict.fromkeys(
            mapping.object_ref.node_id
            for mapping in node.physical_mappings
            if mapping.object_ref.node_id
        )
    )


def _append_selected_concepts(intent: QuestionIntentGraph, nodes: Sequence[OntologyNode]) -> None:
    for node in nodes:
        if node.kind in {
            OntologyNodeKind.BUSINESS_ENTITY,
            OntologyNodeKind.BUSINESS_EVENT,
            OntologyNodeKind.TABLE,
            OntologyNodeKind.VIEW,
        } and all(item.ontology_node_id != node.id for item in intent.entities):
            intent.entities.append(
                IntentEntity(
                    id=stable_ontology_id("intent_entity", node.id),
                    ontology_node_id=node.id,
                    name_ja=node.business_name_ja,
                    role="subject" if not intent.entities else "related",
                    physical_object_ids=_physical_object_ids(node),
                )
            )
        elif node.kind == OntologyNodeKind.METRIC and all(
            item.ontology_node_id != node.id for item in intent.metrics
        ):
            intent.metrics.append(
                IntentMetric(
                    id=stable_ontology_id("intent_metric", node.id),
                    ontology_node_id=node.id,
                    name_ja=node.business_name_ja,
                    aggregation=str(node.metadata.get("aggregation", "")),
                    formula_description_ja=str(node.metadata.get("formula_description_ja", "")),
                )
            )
        elif node.kind in {OntologyNodeKind.PROPERTY, OntologyNodeKind.COLUMN} and all(
            item.ontology_node_id != node.id for item in intent.dimensions
        ):
            intent.dimensions.append(
                IntentDimension(
                    id=stable_ontology_id("intent_dimension", node.id),
                    ontology_node_id=node.id,
                    name_ja=node.business_name_ja,
                )
            )


def _intent_summary(
    session: QuerySession,
    intent: QuestionIntentGraph,
) -> list[IntentSummaryItem]:
    answered_categories = {turn.question.category for turn in session.clarification_turns}
    result: list[IntentSummaryItem] = []

    def add(
        key: str,
        label: str,
        value: str,
        category: ClarificationCategory,
        evidence: str,
    ) -> None:
        confirmed = category in answered_categories
        result.append(
            IntentSummaryItem(
                key=key,
                label_ja=label,
                value_ja=value,
                source=(
                    ClarificationEvidenceSource.USER
                    if confirmed
                    else ClarificationEvidenceSource.ONTOLOGY
                ),
                confirmed=confirmed,
                technical_evidence_ja=evidence,
            )
        )

    if intent.entities:
        add(
            "entities",
            "対象",
            "、".join(item.name_ja for item in intent.entities),
            ClarificationCategory.BUSINESS_MEANING,
            "、".join(item.ontology_node_id for item in intent.entities if item.ontology_node_id),
        )
    if intent.metrics:
        add(
            "metrics",
            "集計する指標",
            "、".join(item.name_ja for item in intent.metrics),
            ClarificationCategory.BUSINESS_MEANING,
            "、".join(item.ontology_node_id for item in intent.metrics if item.ontology_node_id),
        )
    if intent.dimensions:
        add(
            "dimensions",
            "表示する項目",
            "、".join(item.name_ja for item in intent.dimensions),
            ClarificationCategory.OUTPUT,
            "、".join(item.ontology_node_id for item in intent.dimensions if item.ontology_node_id),
        )
    if intent.time_range is not None:
        value = intent.time_range.relative_expression or " ～ ".join(
            filter(None, (intent.time_range.start, intent.time_range.end))
        )
        add("time_range", "期間", value or "指定済み", ClarificationCategory.TIME_RANGE, "")
    if intent.filters:
        add(
            "filters",
            "絞り込み",
            "、".join(f"{item.label_ja} {item.operator} {item.value}" for item in intent.filters),
            ClarificationCategory.FILTER_VALUE,
            "、".join(item.property_node_id for item in intent.filters if item.property_node_id),
        )
    selected_path = next(
        (item for item in intent.candidate_paths if item.id == intent.selected_path_id), None
    )
    if selected_path is not None:
        add(
            "relationship",
            "関係",
            selected_path.name_ja,
            ClarificationCategory.RELATIONSHIP_PATH,
            "、".join(selected_path.edge_ids),
        )
    if intent.granularity:
        add("granularity", "集計単位", intent.granularity, ClarificationCategory.GRANULARITY, "")
    if intent.limit is not None:
        result.append(
            IntentSummaryItem(
                key="limit",
                label_ja="最大件数",
                value_ja=f"{intent.limit} 件",
                source=ClarificationEvidenceSource.USER,
                confirmed=True,
            )
        )
    return result


def _assumptions(session: QuerySession, intent: QuestionIntentGraph) -> list[str]:
    del session, intent
    return []
