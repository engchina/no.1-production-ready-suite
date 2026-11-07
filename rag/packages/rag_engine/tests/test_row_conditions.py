"""表の各行の条件の当てはめ（#4349）。実モデル・資格情報・ネットワークは使わない。

録画は合成の題の実呼出し（xai.grok-4.7・推論の量 low）の出力の文だけで、入力は sha256 で照合する
（製品のコードが実験と同じ指示・入力・schema を作ることを確かめる）。
"""

from __future__ import annotations

import ast
import copy
import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from rag_engine.config import ENTERPRISE_AI_ROW_CONDITION_LLM_PROVIDER, get_settings
from rag_engine.generation import row_conditions as rc
from rag_engine.generation import sent_table_projection as projection
from rag_engine.generation.citation_unit_ids import EvidenceUnitSnapshot, SentUnitCatalog
from rag_engine.generation.input_budget import model_input_measurement

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "row_conditions_recorded.json").read_text(encoding="utf-8"))
CASES: dict[str, dict[str, Any]] = FIXTURE["cases"]
GOLD: dict[str, dict[str, Any]] = FIXTURE["gold"]
RECORDED: list[dict[str, Any]] = FIXTURE["recorded"]
ROUNDS = sorted({(row["round"], row["case_id"]) for row in RECORDED})


def _sha(value: Any) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def catalog_for(case: dict[str, Any]) -> SentUnitCatalog:
    """実験と同じ送達の目録。表の題は物理行を引用単位にする合成の資料。"""
    if case["evidence_text"] is None:
        return SentUnitCatalog(
            [
                EvidenceUnitSnapshot.create(snapshot["span"], [tuple(unit) for unit in snapshot["units"]], snapshot["contract"])
                for snapshot in FIXTURE["catalog_snapshots"]
            ]
        )
    text, source = case["evidence_text"], case["source"]
    units = []
    offset = 0
    for line in text.splitlines(keepends=True):
        end = offset + len(line.rstrip("\r\n"))
        if end > offset:
            units.append((offset, end))
        offset += len(line)
    span = {
        "source_id": source,
        "evidence_id": "E-" + source,
        "source": source + ".txt",
        "text": text,
        "section_path": ["合成資料", "値と規則"],
    }
    return SentUnitCatalog([EvidenceUnitSnapshot.create(span, units, "synthetic_physical_lines_v1")])


def table_for(case_id: str) -> dict[str, Any]:
    tables, skipped = projection.sent_tables(catalog_for(CASES[case_id]))
    assert len(tables) == 1 and not skipped
    return tables[0]


def recorded(round_name: str, case_id: str, call: str) -> dict[str, Any]:
    return next(row for row in RECORDED if (row["round"], row["case_id"], row["call"]) == (round_name, case_id, call))


def row_settings(**overrides: Any) -> Any:
    """この段のモデルを渡した設定（backend が段ごとの設定から渡す環境変数と同じ形）。"""
    environ = {
        "OCI_ENTERPRISE_AI_DEFAULT_MODEL": "answer-model",
        "OCI_ENTERPRISE_AI_ROW_CONDITION_MODEL": "xai.grok-4.7",
        "OCI_ENTERPRISE_AI_ROW_CONDITION_REASONING_EFFORT": "high",
        "RAG_ENGINE_ROW_CONDITION_REASONING_EFFORT": "low",
        "OCI_ENTERPRISE_AI_ROW_CONDITION_MAX_INPUT_TOKENS": "128000",
        **overrides,
    }
    return get_settings(environ=environ, dotenv_path=None)


class Replay:
    """録画の出力を順に返す parse_text。送った呼び出しを記録する。"""

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, system: str, user: str, settings: Any, schema: Any, **options: Any) -> Any:
        self.sent.append({"system": system, "user": user, "schema": schema, "settings": settings, **options})
        return schema.model_validate_json(self.outputs.pop(0))


def replay_for(round_name: str, case_id: str) -> Replay:
    outputs = [recorded(round_name, case_id, "type")["output_text"]]
    if any(row for row in RECORDED if (row["round"], row["case_id"], row["call"]) == (round_name, case_id, "judge")):
        outputs.append(recorded(round_name, case_id, "judge")["output_text"])
    return Replay(outputs)


def evaluate(case_id: str, parse_text: Any, settings: Any = None) -> rc.TableOutcome:
    call_settings, provider_id = rc.row_condition_call_settings(settings or row_settings())
    question = CASES[case_id]["question"]
    return rc.evaluate_table(
        question,
        rc.split_question(question),
        table_for(case_id),
        parse_text=parse_text,
        settings=call_settings,
        provider_id=provider_id,
    )


def assert_matches_gold(outcome: rc.TableOutcome, gold: dict[str, Any]) -> None:
    assert outcome.application is not None, outcome.reason
    rows = {row["row_id"]: row for row in outcome.application["rows"]}
    for rid, expected in gold["rows"].items():
        row = rows[rid]
        cell = row["condition_cell"]["cell_id"] if row["condition_cell"] else None
        assert (row["applicability"], cell, row["condition_type"]) == (
            expected["applicability"],
            expected["condition_cell_id"],
            expected["condition_type"],
        ), rid
        assert set(expected.get("question_ids_include", [])) <= set(row["question_ids"]), rid
    assert outcome.table_result == gold["table_result"]


# ---------------------------------------------------------------- 録画の再生（実験と同じ wire）


@pytest.mark.parametrize("round_name,case_id", ROUNDS)
def test_recorded_wire_is_byte_identical_to_the_verified_experiment(round_name: str, case_id: str) -> None:
    question = CASES[case_id]["question"]
    units = rc.split_question(question)
    table = table_for(case_id)
    [type_call] = rc.build_type_calls(question, units, table)
    expected = recorded(round_name, case_id, "type")
    assert _sha(type_call["system"]) == expected["system_sha256"]
    assert _sha(type_call["user"]) == expected["user_sha256"]
    assert _sha(type_call["schema"]) == expected["schema_sha256"]
    assert type_call["schema_name"] == expected["schema_name"]
    type_result = rc.validate_type(json.loads(expected["output_text"]), type_call["index"])
    judge_calls = rc.build_judge_calls(question, units, table, type_result)
    if not judge_calls:
        return
    [judge_call] = judge_calls
    judge = recorded(round_name, case_id, "judge")
    assert _sha(judge_call["system"]) == judge["system_sha256"]
    assert _sha(judge_call["user"]) == judge["user_sha256"]
    assert _sha(judge_call["schema"]) == judge["schema_sha256"]
    assert judge_call["schema_name"] == judge["schema_name"]


def test_sdk_strict_conversion_keeps_the_built_schema() -> None:
    from openai.lib._parsing._responses import type_to_text_format_param

    question = CASES["reg-period-reversed"]["question"]
    table = table_for("reg-period-reversed")
    [call] = rc.build_type_calls(question, rc.split_question(question), table)
    wire = type_to_text_format_param(rc.wire_model(call["schema_name"], call["schema"]))
    assert wire["strict"] is True and wire["name"] == "RowConditionType"
    assert wire["schema"] == call["schema"]


@pytest.mark.parametrize("round_name,case_id", ROUNDS)
def test_recorded_grok_outputs_match_gold_through_the_product_code(round_name: str, case_id: str) -> None:
    replay = replay_for(round_name, case_id)
    outcome = evaluate(case_id, replay)
    assert not replay.outputs
    assert_matches_gold(outcome, GOLD[case_id])
    assert outcome.calls == len(replay.sent)
    assert [call["provider_id"] for call in replay.sent] == [ENTERPRISE_AI_ROW_CONDITION_LLM_PROVIDER] * len(replay.sent)


def test_rows_unknown_is_not_determined_and_keeps_the_known_rows() -> None:
    outcome = evaluate("rows-unknown", replay_for("reg", "rows-unknown"))
    assert outcome.table_result == "not_determined"
    assert outcome.application is not None
    assert outcome.application["cannot_determine"] == ["R10"]
    assert len(outcome.application["applies"]) == 8
    facts = outcome.facts()
    assert facts["table_result"] == "not_determined"
    assert [row["applicability"] for row in facts["rows"]].count("applies") == 8


def test_application_keeps_source_positions_and_decider() -> None:
    outcome = evaluate("new-n4-mixed-digits", replay_for("n4-r1", "new-n4-mixed-digits"))
    assert outcome.application is not None
    rows = {row["row_id"]: row for row in outcome.application["rows"]}
    assert rows["R4"]["decided_by"] == "type_call_temporal_compare"
    assert rows["R2"]["decided_by"] == "judge_call_binary_combine"
    text = CASES["new-n4-mixed-digits"]["evidence_text"]
    for row in rows.values():
        a, b = row["row_range"]
        assert text[a:b] == row["row_raw"]
        if row["condition_cell"]:
            a, b = row["condition_cell"]["range"]
            assert text[a:b] == row["condition_cell"]["raw"]


# ---------------------------------------------------------------- ハードコードの禁止


def _descriptions(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "description" and isinstance(item, str):
                found.append(item)
            else:
                found.extend(_descriptions(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_descriptions(item))
    return found


def _case_strings() -> set[str]:
    strings: set[str] = set()
    for case_id, case in CASES.items():
        strings.update(part for part in case["question"].split("。") if part)
        for row in table_for(case_id)["rows"]:
            # 1 字の値（点数の数字など）は schema の範囲の説明の数字と区別できないので照合しない。
            strings.update(cell["text"] for cell in row["cells"] if len(cell["text"]) >= 2)
    return strings


# 表の見出しの行の一般の語で、指示の中でも一般の意味で使うもの（題に合わせた語ではない）。
GENERIC_WORDS = {"条件"}


def shipped_texts() -> list[str]:
    texts = [rc.ROW_TYPE_SYSTEM_PROMPT, rc.ROW_JUDGE_SYSTEM_PROMPT]
    for case_id, case in CASES.items():
        question = case["question"]
        units = rc.split_question(question)
        table = table_for(case_id)
        texts += _descriptions(rc.schema_type(units, table, question))
        texts += _descriptions(rc.schema_judge(units, table))
    return texts


def test_no_case_or_gold_strings_in_prompts_or_schema_descriptions() -> None:
    texts = shipped_texts()
    found = {value for value in _case_strings() for text in texts if value in text}
    assert found <= GENERIC_WORDS, found
    for word in [
        "属性",
        "要素",
        "操作",
        "手続",
        "申請",
        "会員区分",
        "区分",
        "割合",
        "点数",
        "加算",
        "未確定",
        "17時",
        "12時",
        "基準日",
        "有",
        "無",
    ]:
        for text in texts:
            assert word not in text, word


def test_no_examples_in_prompts() -> None:
    for text in [rc.ROW_TYPE_SYSTEM_PROMPT, rc.ROW_JUDGE_SYSTEM_PROMPT]:
        assert "例" not in text and "e.g." not in text


SEMANTIC_FUNCTIONS = [
    "split_question",
    "table_subset",
    "validate_type",
    "validate_judge",
    "combine_calls",
    "combine",
    "_time_point",
    "compare_points",
    "_rows_object",
    "build_judge_calls",
    "_anchor",
    "_span",
    "_expression",
    "effective_anchor",
    "comparable",
    "temporal_verdict",
]


def _body(module: Any, name: str) -> str:
    tree = ast.parse(inspect.getsource(module))
    node = copy.deepcopy(next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name))
    if ast.get_docstring(node):
        node.body = node.body[1:]
    return ast.unparse(node)


@pytest.mark.parametrize("name", SEMANTIC_FUNCTIONS)
def test_no_keyword_or_regex_semantics_in_judging_code(name: str) -> None:
    body = _body(rc, name)
    for word in ["営業日", "営業", "基準", "時", "日", "前", "後", "まで", "未確定", "有", "無", "re.", "regex", "lower", "replace"]:
        assert word not in body, (name, word)


def test_modules_do_not_import_regex() -> None:
    for module in (rc, projection):
        source = inspect.getsource(module)
        assert "import re\n" not in source and "from re " not in source


# ---------------------------------------------------------------- 構造の違反（受理しない）


def _type_output() -> dict[str, Any]:
    return json.loads(recorded("reg", "reg-period-reversed", "type")["output_text"])


def _judge_output() -> dict[str, Any]:
    return json.loads(recorded("reg", "rows-unknown", "judge")["output_text"])


def _type_index(case_id: str) -> dict[str, Any]:
    question = CASES[case_id]["question"]
    return {"question": question, "question_units": rc.split_question(question), "table": table_for(case_id)}


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda t: t["rows"].pop("R2"), "row_missing"),
        (lambda t: t["rows"].__setitem__("R9", dict(t["rows"]["R2"])), "unknown_row_id"),
        (lambda t: t["rows"]["R2"].update(row_id="R2"), "invalid_row_value"),
        (lambda t: t["rows"]["R2"].update(condition_type="date"), "invalid_condition_type"),
        (lambda t: t["rows"]["R2"].update(condition_cell_id=None), "temporal_row_requires_condition_cell"),
        (lambda t: t["rows"]["R2"].update(condition_cell_id="R3C2"), "cell_not_in_row"),
        (lambda t: t["rows"]["R2"].update(condition_cell_id="X1"), "unknown_cell_id"),
        (lambda t: t["rows"]["R2"].update(question_ids=["Q9"]), "invalid_question_ids"),
        (lambda t: t["rows"]["R2"].update(question_ids=[]), "question_id_required_for_observation"),
        (lambda t: t["rows"]["R2"].update(temporal=None), "invalid_temporal_value"),
        (lambda t: t["rows"]["R2"]["temporal"]["threshold"]["time"].update(hour=25), "invalid_temporal_value"),
        (lambda t: t["rows"]["R2"]["temporal"].update(comparator="lt"), "invalid_temporal_value"),
        (lambda t: t["rows"]["R2"]["temporal"]["observation"]["expression"].update(text="後"), "invalid_expression_value"),
        (lambda t: t["rows"]["R2"]["temporal"]["observation"]["expression"].update(start_id="P0"), "invalid_expression_value"),
        (lambda t: t["rows"]["R2"]["temporal"]["observation"]["anchor"].update(present=True), "invalid_anchor_value"),
        (lambda t: t["rows"]["R1"].update(temporal=copy.deepcopy(t["rows"]["R2"]["temporal"])), "temporal_value_on_other_row"),
    ],
)
def test_type_call_violations_rejected(mutate: Any, code: str) -> None:
    output = _type_output()
    mutate(output)
    index = _type_index("reg-period-reversed")
    result = rc.validate_type(output, index)
    assert not result["accepted"] and code in {violation["code"] for violation in result["violations"]}
    assert not rc.combine_calls(index, result, None)["accepted"]
    with pytest.raises(rc.RowConditionRejected):
        rc.build_judge_calls(index["question"], index["question_units"], index["table"], result)


def test_type_call_rows_as_array_rejected() -> None:
    result = rc.validate_type({"rows": []}, _type_index("reg-period-reversed"))
    assert not result["accepted"] and result["violations"] == [{"code": "rows_missing"}]


@pytest.mark.parametrize(
    "mutate,code",
    [
        (lambda j: j["rows"].pop("R10"), "row_missing"),
        (lambda j: j["rows"]["R2"].update(contradicted=True), "entailed_and_contradicted"),
        (lambda j: j["rows"]["R2"].update(entailed="yes"), "invalid_judgment_type"),
        (lambda j: j["rows"]["R2"].update(question_ids=[]), "question_id_required_for_determined"),
        (lambda j: j["rows"]["R2"].update(question_ids=["Q9"]), "invalid_question_ids"),
        (lambda j: j["rows"]["R2"].update(condition_cell_id="R3C2"), "cell_not_in_row"),
        (lambda j: j["rows"]["R1"].update(entailed=True), "judgment_without_condition_cell"),
        (lambda j: j["rows"]["R10"].update(contradicted=True, question_ids=[]), "question_id_required_for_determined"),
    ],
)
def test_judge_call_violations_rejected(mutate: Any, code: str) -> None:
    output = _judge_output()
    mutate(output)
    result = rc.validate_judge(output, _type_index("rows-unknown"))
    assert not result["accepted"] and code in {violation["code"] for violation in result["violations"]}


def test_judge_rows_must_equal_other_rows() -> None:
    index = _type_index("reg-period-reversed")
    type_result = rc.validate_type(_type_output(), index)
    judge = {"accepted": True, "violations": [], "rows": {"R2": {}}}
    combined = rc.combine_calls(index, type_result, judge)
    assert not combined["accepted"] and combined["violations"] == [{"code": "judge_rows_mismatch"}]


# ---------------------------------------------------------------- 2 値の組み合わせ・時点の比較


@pytest.mark.parametrize(
    "entailed,contradicted,expected",
    [
        (True, False, "applies"),
        (False, True, "does_not_apply"),
        (False, False, "cannot_determine"),
        (True, True, "inconsistent"),
        ("yes", False, "invalid_judgment"),
        (None, False, "invalid_judgment"),
    ],
)
def test_two_boolean_combination(entailed: Any, contradicted: Any, expected: str) -> None:
    assert rc.combine({"entailed": entailed, "contradicted": contradicted}) == expected


def _reference_compare(left: tuple[int | None, int | None], right: tuple[int | None, int | None], op: str) -> str:
    """区間の比較の参照（時刻の無い日は 0:00〜23:59 の区間。日が無ければ unknown）。"""
    if left[0] is None or right[0] is None:
        return "unknown"

    def interval(point: tuple[int | None, int | None]) -> tuple[int, int]:
        base = int(point[0]) * 1440  # type: ignore[arg-type]
        return (base, base + 1439) if point[1] is None else (base + point[1], base + point[1])

    a, b = interval(left), interval(right)
    if op in {"gt", "ge"}:
        a, b, op = b, a, {"gt": "lt", "ge": "le"}[op]
    if op == "lt":
        return "true" if a[1] < b[0] else "false" if a[0] >= b[1] else "unknown"
    return "true" if a[1] <= b[0] else "false" if a[0] > b[1] else "unknown"


def test_typed_temporal_comparison_matches_the_interval_reference() -> None:
    days = [None, -3, -2, -1, 0]
    clocks = [None, 0, 16 * 60 + 59, 17 * 60, 17 * 60 + 1, 23 * 60 + 59]
    for d1 in days:
        for c1 in clocks:
            for d2 in days:
                for c2 in clocks:
                    for op in ["lt", "le", "gt", "ge"]:
                        left, right = (d1, c1), (d2, c2)
                        expected = _reference_compare(left, right, op)
                        if d1 is not None and d1 == d2 and (c1 is None or c2 is None):
                            expected = "unknown"
                        assert rc.compare_points(left, right, op) == expected, (left, right, op)


def _point(present: bool, day: int, clock: tuple[int, int] | None = None) -> dict[str, Any]:
    return {
        "expression_present": present,
        "business_day_offset": day,
        "time": None if clock is None else {"hour": clock[0], "minute": clock[1]},
        "expression": {"start_id": None, "text": None},
        "anchor": {"present": False, "start_id": None, "text": None},
    }


def test_absent_expression_is_cannot_determine_regardless_of_offset() -> None:
    assert rc._time_point(_point(False, -5)) == (None, None)
    assert rc.compare_points((None, None), (-1, 720), "le") == "unknown"


@pytest.mark.parametrize(
    "point",
    [
        {**_point(True, -1), "business_day_offset": None},
        {**_point(True, -1), "business_day_offset": 1.5},
        {**_point(True, -1), "expression_present": "yes"},
        {**_point(True, -1), "time": {"hour": 9}},
        {**_point(True, -1), "time": {"hour": 9, "minute": 60}},
        {key: value for key, value in _point(True, -1).items() if key != "anchor"},
    ],
)
def test_invalid_point_rejected(point: dict[str, Any]) -> None:
    assert rc._time_point(point) is None


def _temporal(observation: dict[str, Any], threshold: dict[str, Any], comparator: str = "at_or_before") -> dict[str, Any]:
    return {"observation": observation, "threshold": threshold, "comparator": comparator}


def _located(point: dict[str, Any], text: str, source: str, prefix: str, anchor: str | None = None) -> dict[str, Any]:
    value = copy.deepcopy(point)
    value["expression"] = {"start_id": f"{prefix}{source.index(text)}", "text": text}
    if anchor is not None:
        value["anchor"] = {"present": True, "start_id": f"{prefix}{source.index(anchor)}", "text": anchor}
    return value


QUESTION = "対象Xは参照点Aの2単位前に起きた。"
CELL = "参照点Aの1単位前まで"
CELL_ID = "R2C2"


def test_anchor_inside_expression_is_not_explicit_and_compared() -> None:
    observation = _located(_point(True, -2), "2単位前", QUESTION, "P", anchor="2")
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".")
    verdict, computed = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert computed is not None and computed["comparable"] and not computed["observation_anchor"]["explicit"]
    assert verdict == "applies"


def test_both_explicit_same_text_is_compared() -> None:
    observation = _located(_point(True, -2), "2単位前", QUESTION, "P", anchor="参照点A")
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".", anchor="参照点A")
    verdict, computed = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert computed is not None and computed["comparable"] and verdict == "applies"


def test_one_side_explicit_is_cannot_determine() -> None:
    observation = _located(_point(True, -2), "2単位前", QUESTION, "P", anchor="参照点A")
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".")
    verdict, computed = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert computed is not None and not computed["comparable"] and verdict == "cannot_determine"


def test_both_explicit_different_text_is_cannot_determine() -> None:
    observation = _located(_point(True, -2), "2単位前", QUESTION, "P", anchor="対象X")
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".", anchor="参照点A")
    verdict, _ = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert verdict == "cannot_determine"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda e: e.update(text="2単位後"),
        lambda e: e.update(start_id="P0"),
        lambda e: e.update(start_id="R2C2.0"),
        lambda e: e.update(start_id="Px"),
        lambda e: e.update(text=""),
    ],
)
def test_expression_span_verified_exactly(mutate: Any) -> None:
    observation = _located(_point(True, -2), "2単位前", QUESTION, "P")
    mutate(observation["expression"])
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".")
    verdict, computed = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert verdict == "invalid_expression" and computed is None


def test_absent_expression_requires_null_span() -> None:
    observation = _located(_point(False, 0), "2単位前", QUESTION, "P")
    threshold = _located(_point(True, -1), "1単位前", CELL, CELL_ID + ".")
    verdict, _ = rc.temporal_verdict(_temporal(observation, threshold), QUESTION, CELL_ID, CELL)
    assert verdict == "invalid_expression"


# ---------------------------------------------------------------- 分割・上限・入力予算


def _synthetic_rows(count: int, *, width: int = 1) -> dict[str, Any]:
    lines = ["表。", "| 番号 | 条件 | 値 |"] + [f"| Z{index} | {'項' * width}{index}=甲 | {index} |" for index in range(1, count + 1)]
    return {"question": "項目1は甲です。", "evidence_text": "\n".join(lines), "source": "synthetic-wide"}


def _table(case: dict[str, Any]) -> dict[str, Any]:
    tables, _ = projection.sent_tables(catalog_for(case))
    return tables[0]


def test_rows_are_batched_in_table_order_not_truncated() -> None:
    case = _synthetic_rows(40)
    table = _table(case)
    built = rc.build_type_calls(case["question"], rc.split_question(case["question"]), table)
    batches = [[row["row_id"] for row in call["index"]["table"]["rows"]] for call in built]
    assert [len(batch) for batch in batches] == [16, 16, 9]
    assert [rid for batch in batches for rid in batch] == [row["row_id"] for row in table["rows"]]
    for call in built:
        assert list(call["schema"]["properties"]["rows"]["required"]) == [row["row_id"] for row in call["index"]["table"]["rows"]]


def test_too_many_rows_rejected_not_truncated() -> None:
    case = _synthetic_rows(rc.MAX_ROWS_PER_CALL * rc.MAX_BATCHES - 1)  # 見出しの行を含めてちょうど上限
    assert len(rc.build_type_calls(case["question"], rc.split_question(case["question"]), _table(case))) == rc.MAX_BATCHES
    case = _synthetic_rows(rc.MAX_ROWS_PER_CALL * rc.MAX_BATCHES)
    table = _table(case)
    assert len(table["rows"]) > rc.MAX_ROWS_PER_CALL * rc.MAX_BATCHES
    replay = Replay([])
    outcome = rc.evaluate_table(
        case["question"], rc.split_question(case["question"]), table, parse_text=replay, settings=row_settings(), provider_id="x"
    )
    assert outcome.table_result == "not_determined" and outcome.reason == "table_too_large" and not replay.sent


def test_wide_rows_over_input_budget_rejected_before_sending() -> None:
    case = _synthetic_rows(14, width=1500)
    settings = row_settings(OCI_ENTERPRISE_AI_ROW_CONDITION_MAX_INPUT_TOKENS="40000")
    call_settings, provider_id = rc.row_condition_call_settings(settings)
    table = _table(case)
    [call] = rc.build_type_calls(case["question"], rc.split_question(case["question"]), table)
    assert not model_input_measurement(call_settings, call["system"], call["user"], call["schema"], provider_id=provider_id)["fits"]
    replay = Replay([])
    outcome = rc.evaluate_table(
        case["question"], rc.split_question(case["question"]), table, parse_text=replay, settings=call_settings, provider_id=provider_id
    )
    assert outcome.table_result == "not_determined" and outcome.reason == "input_budget_exceeded" and not replay.sent


def test_batch_failure_stops_remaining_batches() -> None:
    case = _synthetic_rows(40)
    replay = Replay(['{"rows": {}}'] * 3)
    outcome = rc.evaluate_table(
        case["question"], rc.split_question(case["question"]), _table(case), parse_text=replay, settings=row_settings(), provider_id="x"
    )
    assert len(replay.sent) == 1 and outcome.calls == 1
    assert outcome.table_result == "not_determined" and outcome.reason == "type_call_rejected"


def test_call_failure_is_not_determined_with_the_error_type() -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise TimeoutError("合成の失敗")

    outcome = evaluate("reg-period-reversed", fail)
    assert outcome.table_result == "not_determined" and outcome.reason == "call_failed:TimeoutError"


# ---------------------------------------------------------------- 段のモデルの設定


def test_step_model_and_effort_come_from_the_step_setting() -> None:
    settings = row_settings()
    call_settings, provider_id = rc.row_condition_call_settings(settings)
    provider = call_settings.llm_providers[provider_id]
    assert provider_id == ENTERPRISE_AI_ROW_CONDITION_LLM_PROVIDER
    assert provider.model == "xai.grok-4.7" and provider.reasoning_effort == "low"
    # ほかの段の設定（回答のモデル）は変えない。
    assert settings.llm_providers[settings.default_answer_llm].model == "answer-model"


@pytest.mark.parametrize(
    "environ,reason",
    [
        ({"OCI_ENTERPRISE_AI_ROW_CONDITION_MODEL": ""}, "row_condition_model_not_configured"),
        ({"RAG_ENGINE_ROW_CONDITION_UNAVAILABLE": "モデル「xai.grok-4.7」は登録されていません。"}, "登録されていません"),
    ],
)
def test_unavailable_step_model_does_not_fall_back(environ: dict[str, str], reason: str) -> None:
    settings = row_settings(**environ)
    replay = Replay([])
    result = rc.evaluate_tables(
        CASES["reg-period-reversed"]["question"], [table_for("reg-period-reversed")], parse_text=replay, settings=settings
    )
    assert result.status == "model_unavailable" and reason in result.reason and not replay.sent
    assert result.undetermined() == []


def test_no_sent_table_is_not_applicable() -> None:
    result = rc.evaluate_tables("質問。", [], parse_text=Replay([]), settings=row_settings())
    assert result.status == "not_applicable" and result.calls == 0


def test_tables_over_the_limit_are_not_determined_without_calls() -> None:
    table = table_for("reg-period-reversed")
    outputs = []
    for _ in range(rc.MAX_TABLES):
        outputs += [
            recorded("reg", "reg-period-reversed", "type")["output_text"],
            recorded("reg", "reg-period-reversed", "judge")["output_text"],
        ]
    replay = Replay(outputs)
    tables = [replace_evidence(table, f"E{index}") for index in range(rc.MAX_TABLES + 1)]
    result = rc.evaluate_tables(CASES["reg-period-reversed"]["question"], tables, parse_text=replay, settings=row_settings())
    assert result.calls == 2 * rc.MAX_TABLES
    assert result.undetermined() == [f"E{rc.MAX_TABLES}"]
    assert result.tables[-1].reason == "too_many_tables"


def replace_evidence(table: dict[str, Any], evidence_id: str) -> dict[str, Any]:
    return {**table, "evidence_id": evidence_id}


def test_projection_skips_ambiguous_snapshots_with_reason() -> None:
    text = "表。\n| a | b |\n| c |\n"
    span = {"source_id": "s", "evidence_id": "E-s", "source": "s.txt", "text": text}
    units = [(0, 2), (3, 12), (13, 18)]
    tables, skipped = projection.sent_tables(SentUnitCatalog([EvidenceUnitSnapshot.create(span, units, "synthetic_physical_lines_v1")]))
    assert not tables and skipped == [{"evidence_id": "E-s", "reason": "ambiguous_column_count"}]


def test_row_settings_helper_is_the_backend_contract() -> None:
    # backend（rag/backend の build_engine_settings）が渡す環境変数の名前と一致する。
    settings = row_settings()
    assert settings.row_condition_llm == ENTERPRISE_AI_ROW_CONDITION_LLM_PROVIDER
    assert replace(settings, row_condition_unavailable="x").row_condition_unavailable == "x"


def test_real_sdk_path_sends_the_step_model_effort_and_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """実 SDK の送信（mock の transport）: この段のモデル・推論の量・固定 key の schema のまま送り、行の object を受ける。"""
    import httpx
    from openai import OpenAI
    from test_bound_audit_transport import response_bytes

    from rag_engine.adapters import oci

    sent: list[dict[str, Any]] = []
    output = recorded("reg", "reg-period-reversed", "type")["output_text"]

    def transport(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        data, headers = response_bytes(output, False)
        return httpx.Response(200, content=data, headers=headers)

    client = OpenAI(
        api_key="mock-only",
        base_url="https://mock.invalid/v1",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    monkeypatch.setattr(oci, "_openai_client_for_provider", lambda *_args: client)
    monkeypatch.setattr(oci, "_stall_detection_enabled", lambda _settings: False)
    settings = row_settings(
        OCI_ENTERPRISE_AI_ROW_CONDITION_ENDPOINT="https://mock.invalid/v1", OCI_ENTERPRISE_AI_ROW_CONDITION_API_KEY="mock-only"
    )
    call_settings, provider_id = rc.row_condition_call_settings(settings)
    question = CASES["reg-period-reversed"]["question"]
    [call] = rc.build_type_calls(question, rc.split_question(question), table_for("reg-period-reversed"))
    try:
        parsed = oci.parse_text_response(
            call["system"], call["user"], call_settings, rc.wire_model(call["schema_name"], call["schema"]), provider_id=provider_id
        )
    finally:
        client.close()
    [request] = sent
    assert request["model"] == "xai.grok-4.7" and request["reasoning"] == {"effort": "low"}
    assert [message["content"] for message in request["input"]] == [call["system"], call["user"]]
    assert request["text"]["format"]["schema"] == call["schema"] and request["text"]["format"]["strict"] is True
    assert rc.validate_type(parsed.model_dump(), call["index"])["accepted"]
