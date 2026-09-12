"""Guided QuerySession 向けの決定論的な質問計画と回答反映。

LLM は ``ontology_router._interpret_question`` で構造化 intent を作る責務に限定し、
このモジュールは profile view 内の候補だけを質問へ変換する。回答で受理する ID も
同じ候補から再検証し、client が任意の Ontology ID を注入できないようにする。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
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

_REQUEST_STEMS = ("表示", "検索", "集計", "確認", "取得", "抽出", "比較", "計算")

_PRIORITY: dict[ClarificationCategory, int] = {
    ClarificationCategory.BUSINESS_MEANING: 0,
    ClarificationCategory.RELATIONSHIP_PATH: 1,
    ClarificationCategory.FILTER_VALUE: 2,
    ClarificationCategory.TIME_RANGE: 3,
    ClarificationCategory.GRANULARITY: 4,
    ClarificationCategory.OUTPUT: 5,
}


def enrich_guided_intent(
    intent: QuestionIntentGraph,
    ontology: SchemaOntology | None = None,
) -> QuestionIntentGraph:
    """SQL への影響が大きい不足だけを blocking ambiguity として追加する。"""

    updated = intent.model_copy(deep=True)
    if ontology is not None:
        updated.entities = _deduplicate_guided_entities(updated.entities, ontology)
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
    intent_summary = _intent_summary(session, intent)
    covered_summary_keys = {question.summary_key for question in questions if question.summary_key}
    questions.extend(
        _confirmation_question(item)
        for item in intent_summary
        if not item.confirmed and item.key not in covered_summary_keys
    )
    questions.sort(key=lambda item: (_PRIORITY[item.category], item.id))
    answered_question_ids = {
        turn.question.id for turn in session.clarification_turns if turn.question.blocking
    }
    # LLM / retrieval の診断文は利用者向けではないため、未確認一覧にも
    # 実際に回答できる形へ変換した質問文だけを公開する。
    missing = [question.prompt_ja for question in questions]
    required_total = len(answered_question_ids) + len(questions)
    unanswerable = any(
        question.category == ClarificationCategory.RELATIONSHIP_PATH and not question.options
        for question in questions
    ) or (not view.node_ids and bool(questions))
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
        intent_summary=intent_summary,
        required_total=required_total,
        required_confirmed=len(answered_question_ids),
        missing_required=missing,
        assumptions=_assumptions(session, intent),
        turn_count=len(session.clarification_turns),
        manual_completion_required=len(session.clarification_turns) >= MAX_GUIDED_TURNS
        and bool(questions),
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
    resolution_parts = [
        (
            item.description_ja
            if question.summary_key and not question.ambiguity_id and item.description_ja
            else item.label_ja
        )
        for item in selected
    ]
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
        # 「はい」は現在値の承認。構造値のない承認で既存の粒度を消さない。
        if value is not None:
            updated.granularity = str(value)
        elif free_text:
            updated.granularity = free_text

    if resolution:
        updated.question_effective = _render_clarified_question(
            updated,
            question.category,
            resolution,
            question.summary_key,
        )
    updated.confidence = min(1.0, updated.confidence + 0.1)
    return updated


def _render_clarified_question(
    intent: QuestionIntentGraph,
    category: ClarificationCategory,
    resolution: str,
    summary_key: str = "",
) -> str:
    """構造化 intent 全体を、利用者が確認できる自然な検索要求へ再構成する。"""

    answer = resolution.strip().rstrip("。！？!?")
    target_names = _unique_business_names(item.name_ja for item in intent.entities)
    output_names = _unique_business_names(
        [
            *(item.name_ja for item in intent.dimensions),
            *(_metric_label(item) for item in intent.metrics),
        ]
    )
    answer_parts = _unique_business_names(part.strip() for part in answer.split("、"))
    if (
        category == ClarificationCategory.BUSINESS_MEANING
        and summary_key == "metrics"
        and not intent.metrics
    ):
        output_names = _unique_business_names([*output_names, *answer_parts])
    elif category == ClarificationCategory.BUSINESS_MEANING and summary_key != "metrics":
        target_names = _unique_business_names([*target_names, *answer_parts])
    elif category == ClarificationCategory.OUTPUT:
        output_names = _unique_business_names([*output_names, *answer_parts])

    conditions = _unique_business_names(
        condition
        for item in intent.filters
        if (condition := _format_filter_condition(item.label_ja, item.operator, item.value))
    )
    resolved_filter_answers = [
        item.resolution or ""
        for item in intent.ambiguities
        if item.resolved
        and item.resolution
        and _category(item.code) == ClarificationCategory.FILTER_VALUE
    ]
    conditions = _unique_business_names([*conditions, *resolved_filter_answers])
    if (
        category == ClarificationCategory.FILTER_VALUE
        and answer
        and (normalized_answer := _normalized_business_text(answer))
        and not any(
            normalized_answer in _normalized_business_text(condition) for condition in conditions
        )
    ):
        conditions.append(answer)

    time_scope = _format_time_scope(intent.time_range)
    if category == ClarificationCategory.TIME_RANGE and answer and not time_scope:
        time_scope = answer
    if (
        intent.time_range is not None
        and time_scope
        and _is_business_name(intent.time_range.label_ja)
        and intent.time_range.label_ja != "期間"
    ):
        conditions.insert(0, f"{intent.time_range.label_ja}が{time_scope}")
        time_scope = ""

    target = _join_natural(target_names)
    scope = f"{time_scope}の{target}" if target and time_scope else target or time_scope
    if conditions:
        condition_text = "、かつ".join(conditions)
        scope = f"{scope}のうち、{condition_text}のデータ" if scope else f"{condition_text}のデータ"

    if output_names:
        request = f"検索結果には{'、'.join(output_names)}を表示してください。"
    else:
        original = _clean_question_text(intent.question_original)
        request = _request_sentence(original) if original else "該当する情報を表示してください。"
    sentences = [f"{scope}を対象に、{request}" if scope else request]

    granularity = _display_granularity(intent.granularity)
    if category == ClarificationCategory.GRANULARITY and answer and not granularity:
        granularity = answer
    if granularity:
        sentences.append(
            "期間全体を一つに集計してください。"
            if granularity == "集計のみ"
            else f"集計単位は{granularity}です。"
        )

    selected_path = next(
        (item for item in intent.candidate_paths if item.id == intent.selected_path_id), None
    )
    relationship = selected_path.name_ja if selected_path is not None else ""
    if category == ClarificationCategory.RELATIONSHIP_PATH and answer and not relationship:
        relationship = answer
    if _is_business_name(relationship):
        sentences.append(f"データの関連付けには「{relationship}」を使用してください。")

    resolved_sorts = _resolved_sorts(intent)
    if resolved_sorts:
        sort_text = "、".join(
            f"{label}の{'昇順' if direction == 'asc' else '降順'}"
            for label, direction in resolved_sorts
        )
        if intent.limit is not None:
            rank_label = "上位" if resolved_sorts[0][1] == "desc" else "先頭"
            sentences.append(
                f"表示結果は{sort_text}で並べ、{rank_label}{intent.limit}件を取得してください。"
            )
        else:
            sentences.append(f"表示結果は{sort_text}で並べてください。")
    elif intent.limit is not None:
        sentences.append(f"表示件数は最大{intent.limit}件にしてください。")
    return "".join(sentences)


def _metric_label(metric: IntentMetric) -> str:
    """SQL に渡す文章にも集計方式を残す（構造化 intent は直接渡されない）。"""
    operation = metric.aggregation.strip().casefold()
    label = {
        "count": "件数",
        "count_distinct": "重複を除いた件数",
        "sum": "合計",
        "avg": "平均",
        "min": "最小値",
        "max": "最大値",
    }.get(operation, "")
    if not label or label in metric.name_ja:
        return metric.name_ja
    return f"{metric.name_ja}の{label}"


def _unique_business_names(values: Iterable[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        label = str(value).strip()
        if _is_business_name(label) and label not in result:
            result.append(label)
    return result


def _is_business_name(value: str) -> bool:
    lowered = value.casefold()
    internal_prefixes = (
        "business_entity_",
        "physical_",
        "property_",
        "intent_",
        "clarification_",
    )
    return bool(value) and not lowered.startswith(internal_prefixes)


def _normalized_business_text(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _join_natural(values: Sequence[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    return "、".join(values[:-1]) + f"と{values[-1]}"


def _display_filter_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "はい" if value else "いいえ"
    if isinstance(value, (list, tuple, set)):
        items = sorted(value, key=str) if isinstance(value, set) else value
        return "、".join(rendered for item in items if (rendered := _display_filter_value(item)))
    if isinstance(value, (int, float)):
        return str(value)
    rendered = str(value).strip()
    if not rendered or not _is_business_name(rendered):
        return ""
    return f"「{rendered}」"


def _format_filter_condition(label: str, operator: str, value: Any) -> str:
    field = label.strip()
    if not _is_business_name(field):
        return ""
    normalized_operator = operator.strip().casefold().replace("_", " ")
    if normalized_operator in {"is null", "null"}:
        return f"{field}が未設定"
    if normalized_operator in {"is not null", "not null"}:
        return f"{field}が設定済み"
    rendered = _display_filter_value(value)
    if not rendered:
        return ""
    templates = {
        "=": "{field}が{value}",
        "==": "{field}が{value}",
        "eq": "{field}が{value}",
        "!=": "{field}が{value}以外",
        "<>": "{field}が{value}以外",
        "ne": "{field}が{value}以外",
        ">": "{field}が{value}より大きい",
        "gt": "{field}が{value}より大きい",
        ">=": "{field}が{value}以上",
        "gte": "{field}が{value}以上",
        "<": "{field}が{value}より小さい",
        "lt": "{field}が{value}より小さい",
        "<=": "{field}が{value}以下",
        "lte": "{field}が{value}以下",
        "in": "{field}が{value}のいずれか",
        "not in": "{field}が{value}のいずれでもない",
        "like": "{field}が{value}に一致",
        "contains": "{field}に{value}を含む",
    }
    template = templates.get(normalized_operator, "{field}の条件値が{value}")
    return template.format(field=field, value=rendered)


def _format_time_scope(time_range: IntentTimeRange | None) -> str:
    if time_range is None:
        return ""
    relative = time_range.relative_expression.strip()
    if relative:
        return relative
    if time_range.start and time_range.end:
        return f"{time_range.start}から{time_range.end}まで"
    if time_range.start:
        return f"{time_range.start}以降"
    if time_range.end:
        return f"{time_range.end}まで"
    return ""


def _display_granularity(value: str) -> str:
    normalized = value.strip().casefold()
    return {
        "day": "日別",
        "month": "月別",
        "year": "年別",
        "none": "集計のみ",
    }.get(normalized, value.strip())


def _resolved_sorts(intent: QuestionIntentGraph) -> list[tuple[str, str]]:
    labels_by_id: dict[str, str] = {}

    def add_label(item_id: str, ontology_node_id: str, name_ja: str) -> None:
        if _is_business_name(name_ja):
            labels_by_id[item_id] = name_ja
            if ontology_node_id:
                labels_by_id[ontology_node_id] = name_ja

    for dimension in intent.dimensions:
        add_label(dimension.id, dimension.ontology_node_id, dimension.name_ja)
    for metric in intent.metrics:
        add_label(metric.id, metric.ontology_node_id, metric.name_ja)
    for entity in intent.entities:
        add_label(entity.id, entity.ontology_node_id, entity.name_ja)
    return [
        (label, item.direction)
        for item in intent.sorts
        if (label := labels_by_id.get(item.target_id))
    ]


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
    resolution: str = "",
) -> QuestionIntentGraph:
    """自由入力から再解釈できた現在 slot と依存 ambiguity だけを取り込む。"""

    updated = intent.model_copy(deep=True)

    # 確認項目が所有する slot だけを置換する。訂正前の推測を append で残さない。
    if question.summary_key in {"entities", "metrics", "dimensions"}:
        field = question.summary_key
        setattr(
            updated, field, [item.model_copy(deep=True) for item in getattr(reinterpreted, field)]
        )
    elif question.category == ClarificationCategory.BUSINESS_MEANING:
        updated.entities = [item.model_copy(deep=True) for item in reinterpreted.entities]
        updated.metrics = [item.model_copy(deep=True) for item in reinterpreted.metrics]
    elif question.category == ClarificationCategory.OUTPUT:
        updated.dimensions = [item.model_copy(deep=True) for item in reinterpreted.dimensions]
        updated.metrics = [item.model_copy(deep=True) for item in reinterpreted.metrics]
        updated.sorts = [item.model_copy(deep=True) for item in reinterpreted.sorts]
        updated.limit = reinterpreted.limit
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

    existing_codes = {item.code for item in updated.ambiguities}
    updated.ambiguities.extend(
        item.model_copy(deep=True)
        for item in reinterpreted.ambiguities
        if item.blocking and not item.resolved and item.code not in existing_codes
    )
    updated.confidence = max(updated.confidence, reinterpreted.confidence)
    updated.question_effective = _render_clarified_question(
        updated,
        question.category,
        (
            ""
            if question.category
            in {ClarificationCategory.BUSINESS_MEANING, ClarificationCategory.OUTPUT}
            else resolution
        ),
        question.summary_key,
    )
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
        summary_key=_summary_key_for_question(category, candidate_nodes),
        category=category,
        prompt_ja=prompt,
        reason_ja=reason,
        answer_kind=answer_kind,
        options=options[:5],
        allow_free_text=category != ClarificationCategory.RELATIONSHIP_PATH,
        blocking=ambiguity.blocking,
    )


def _summary_key_for_question(
    category: ClarificationCategory,
    candidate_nodes: Sequence[OntologyNode],
) -> str:
    if category == ClarificationCategory.BUSINESS_MEANING:
        if candidate_nodes and all(
            node.kind == OntologyNodeKind.METRIC for node in candidate_nodes
        ):
            return "metrics"
        return "entities"
    return {
        ClarificationCategory.RELATIONSHIP_PATH: "relationship",
        ClarificationCategory.FILTER_VALUE: "filters",
        ClarificationCategory.TIME_RANGE: "time_range",
        ClarificationCategory.GRANULARITY: "granularity",
        ClarificationCategory.OUTPUT: "dimensions",
    }.get(category, "")


def _confirmation_question(item: IntentSummaryItem) -> ClarificationQuestion:
    category = {
        "entities": ClarificationCategory.BUSINESS_MEANING,
        "metrics": ClarificationCategory.BUSINESS_MEANING,
        "dimensions": ClarificationCategory.OUTPUT,
        "filters": ClarificationCategory.FILTER_VALUE,
        "time_range": ClarificationCategory.TIME_RANGE,
        "relationship": ClarificationCategory.RELATIONSHIP_PATH,
        "granularity": ClarificationCategory.GRANULARITY,
    }[item.key]
    prompt = {
        "entities": f"検索対象は「{item.value_ja}」で合っていますか？",
        "metrics": f"集計する指標は「{item.value_ja}」で合っていますか？",
        "dimensions": f"表示する項目は「{item.value_ja}」で合っていますか？",
        "filters": f"絞り込み条件は「{item.value_ja}」で合っていますか？",
        "time_range": f"対象期間は「{item.value_ja}」で合っていますか？",
        "relationship": f"データの関係は「{item.value_ja}」で合っていますか？",
        "granularity": f"集計単位は「{item.value_ja}」で合っていますか？",
    }[item.key]
    return ClarificationQuestion(
        id=stable_ontology_id("clarification_question", "confirm", item.key, item.value_ja),
        summary_key=item.key,
        category=category,
        prompt_ja=prompt,
        reason_ja=(
            "AI がクエリから補った解釈です。内容を確認し、"
            "異なる場合は正しい条件を入力してください。"
        ),
        answer_kind=ClarificationAnswerKind.SINGLE_SELECT,
        options=[
            ClarificationOption(
                id=stable_ontology_id("clarification_option", "confirm", item.key, item.value_ja),
                label_ja="はい、この内容で進める",
                description_ja=item.value_ja,
                source=ClarificationEvidenceSource.ONTOLOGY,
            )
        ],
        allow_free_text=True,
        blocking=True,
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
                OntologyNodeKind.OBJECT_TYPE,
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
        OntologyNodeKind.OBJECT_TYPE,
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


def _deduplicate_guided_entities(
    entities: Sequence[IntentEntity],
    ontology: SchemaOntology,
) -> list[IntentEntity]:
    """同じ物理 object を指す業務 node / 物理 node は業務 node へ統合する。"""

    node_by_id = {node.id: node for node in ontology.nodes}
    business_kinds = {
        OntologyNodeKind.BUSINESS_ENTITY,
        OntologyNodeKind.OBJECT_TYPE,
        OntologyNodeKind.BUSINESS_EVENT,
    }
    physical_kinds = {OntologyNodeKind.TABLE, OntologyNodeKind.VIEW}

    def object_ids(entity: IntentEntity) -> set[str]:
        result = set(entity.physical_object_ids)
        node = node_by_id.get(entity.ontology_node_id)
        if node is None:
            return result
        if node.kind in physical_kinds:
            result.add(node.id)
        result.update(
            mapping.object_ref.node_id
            for mapping in node.physical_mappings
            if mapping.object_ref.node_id
        )
        return result

    result: list[IntentEntity] = []
    result_object_ids: list[set[str]] = []
    for entity in entities:
        node = node_by_id.get(entity.ontology_node_id)
        current_ids = object_ids(entity)
        if entity.ontology_node_id and any(
            entity.ontology_node_id == existing.ontology_node_id for existing in result
        ):
            continue
        duplicate_index = next(
            (
                index
                for index, (existing, existing_ids) in enumerate(
                    zip(result, result_object_ids, strict=True)
                )
                if current_ids
                and existing_ids
                and not current_ids.isdisjoint(existing_ids)
                and (
                    node is not None
                    and node.kind in business_kinds
                    and node_by_id.get(existing.ontology_node_id) is not None
                    and node_by_id[existing.ontology_node_id].kind in physical_kinds
                    or node is not None
                    and node.kind in physical_kinds
                    and node_by_id.get(existing.ontology_node_id) is not None
                    and node_by_id[existing.ontology_node_id].kind in business_kinds
                )
            ),
            None,
        )
        if duplicate_index is None:
            result.append(entity)
            result_object_ids.append(current_ids)
            continue
        existing_node = node_by_id.get(result[duplicate_index].ontology_node_id)
        if (
            node is not None
            and node.kind in business_kinds
            and (existing_node is None or existing_node.kind in physical_kinds)
        ):
            result[duplicate_index] = entity
            result_object_ids[duplicate_index] = current_ids
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
            OntologyNodeKind.OBJECT_TYPE,
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
    answered_summary_keys = {
        turn.question.summary_key
        for turn in session.clarification_turns
        if turn.question.summary_key
    }
    legacy_answered_categories = {
        turn.question.category
        for turn in session.clarification_turns
        if not turn.question.summary_key
    }
    result: list[IntentSummaryItem] = []

    def explicitly_stated(*values: object) -> bool:
        normalized_question = "".join(
            character for character in intent.question_original.casefold() if character.isalnum()
        )
        normalized_values = [
            "".join(character for character in str(value).casefold() if character.isalnum())
            for value in values
            if str(value).strip()
        ]
        return bool(normalized_values) and all(
            value in normalized_question for value in normalized_values
        )

    def add(
        key: str,
        label: str,
        value: str,
        category: ClarificationCategory,
        explicit_values: Sequence[object],
    ) -> None:
        confirmed = (
            key in answered_summary_keys
            or category in legacy_answered_categories
            or explicitly_stated(*explicit_values)
        )
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
            )
        )

    if intent.entities:
        add(
            "entities",
            "対象",
            "、".join(item.name_ja for item in intent.entities),
            ClarificationCategory.BUSINESS_MEANING,
            [item.name_ja for item in intent.entities],
        )
    if intent.metrics:
        add(
            "metrics",
            "集計する指標",
            "、".join(_metric_label(item) for item in intent.metrics),
            ClarificationCategory.BUSINESS_MEANING,
            [_metric_label(item) for item in intent.metrics],
        )
    if intent.dimensions:
        add(
            "dimensions",
            "表示する項目",
            "、".join(item.name_ja for item in intent.dimensions),
            ClarificationCategory.OUTPUT,
            [item.name_ja for item in intent.dimensions],
        )
    if intent.time_range is not None:
        value = intent.time_range.relative_expression or " ～ ".join(
            filter(None, (intent.time_range.start, intent.time_range.end))
        )
        add(
            "time_range",
            "期間",
            value or "指定済み",
            ClarificationCategory.TIME_RANGE,
            [value],
        )
    if intent.filters:
        add(
            "filters",
            "絞り込み",
            "、".join(f"{item.label_ja} {item.operator} {item.value}" for item in intent.filters),
            ClarificationCategory.FILTER_VALUE,
            [part for item in intent.filters for part in (item.label_ja, item.value)],
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
            [selected_path.name_ja],
        )
    if intent.granularity:
        add(
            "granularity",
            "集計単位",
            intent.granularity,
            ClarificationCategory.GRANULARITY,
            [intent.granularity],
        )
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
