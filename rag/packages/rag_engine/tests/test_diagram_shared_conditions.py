"""共有線のラベルを同じ構造から投影し、枝の条件や不明な接続を混ぜない。"""

import copy
import json
from pathlib import Path

import pytest

from rag_engine.models.llm import PictureDescriptionOutput
from rag_engine.parsing.diagram_graph import graph_projection, normalize_graph
from rag_engine.parsing.picture_descriptions import _visual_structure, vision_description_text, vision_text_projection
from rag_engine.parsing.vision_checkpoints import _has_current_contract

FIXTURES = Path(__file__).with_name("fixtures")
CASES = json.loads((FIXTURES / "diagram_shared_conditions.json").read_text())


def payload(case):
    base = json.loads((FIXTURES / "vision_metadata_recorded_response.json").read_text())
    return base | {key: value for key, value in case.items() if key.startswith("diagram_")} | {"visual_kind": "flowchart"}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_shared_graph_survives_validation_storage_and_projection(case):
    description = PictureDescriptionOutput.model_validate(payload(case)).model_dump()
    restored = json.loads(json.dumps(description))
    assert _has_current_contract(restored)
    structure = _visual_structure(restored, "flowchart")
    assert structure["diagram_nodes"] == description["diagram_nodes"]
    assert structure["diagram_edges"] == description["diagram_edges"]
    paths, uncertain = graph_projection(restored)
    assert paths is not None
    if case["id"] == "shared-junction":
        assert len(paths) == 2
        assert {line[-1] for line in paths} == {"B", "C"}
        assert all("mode-1 / mode-2" in line for line in paths)
    elif case["id"] == "branch-local-labels":
        assert len(paths) == 2
        assert "alpha" in next(line for line in paths if line.endswith("B"))
        assert "beta" not in next(line for line in paths if line.endswith("B"))
        assert "alpha" not in next(line for line in paths if line.endswith("C"))
        assert "beta" in next(line for line in paths if line.endswith("C"))
    elif case["id"] == "crossing-without-junction":
        assert len(paths) == 2
        assert any(line.startswith("A") and line.endswith("B") for line in paths)
        assert any(line.startswith("C") and line.endswith("D") for line in paths)
    elif case["id"] == "arrow-direction-and-return":
        assert len(paths) == 2
        assert any(line.startswith("A") and line.endswith("B") and "forward" in line for line in paths)
        assert any(line.startswith("B") and line.endswith("A") and "return" in line for line in paths)
    else:
        assert not paths and uncertain
        assert "?" in uncertain[0]
    projection = vision_text_projection(restored)
    assert projection.answer_description == vision_description_text(restored, for_answer=True)
    assert all(line in projection.answer_description for line in paths)


def test_free_summary_cannot_reassign_shared_labels_in_another_projection_field():
    description = payload(CASES[0]) | {
        "diagram_summary": "合成の誤った経路説明",
        "retrieval_text": "合成の別の誤った経路",
        "condition_result_pairs": [{"condition": "合成の誤条件", "result": "合成の誤結果", "visible_text": ""}],
        "operation_steps": ["合成の誤った順序"],
    }
    for text in (vision_description_text(description), vision_description_text(description, for_answer=True)):
        assert "合成の誤" not in text
        assert "mode-1 / mode-2" in text


@pytest.mark.parametrize("change", ["unknown_id", "duplicate_id", "unresolved_directed"])
def test_ambiguous_graph_references_mark_only_affected_edges_unconfirmed(change):
    raw = copy.deepcopy(payload(CASES[0]))
    if change == "unknown_id":
        raw["diagram_edges"][0]["target"] = "not-in-image"
    elif change == "duplicate_id":
        raw["diagram_nodes"].append(raw["diagram_nodes"][0])
    else:
        raw["diagram_edges"][0]["target"] = None
        raw["diagram_edges"][0]["direction"] = "forward"
    description = PictureDescriptionOutput.model_validate(raw).model_dump()
    normalized = normalize_graph(description)
    assert normalized["diagram_edges"][0]["direction"] == "uncertain"
    paths, uncertain = graph_projection(normalized)
    assert not paths
    assert uncertain
    assert normalized["diagram_nodes"] == description["diagram_nodes"]


def test_old_flat_diagram_is_not_reused_as_current_checkpoint_contract():
    raw = payload(CASES[0]) | {"diagram_nodes": ["A", "B"], "diagram_edges": [{"source": "A", "target": "B", "label": "m"}]}
    assert not _has_current_contract(raw)


def test_junction_combinations_without_a_receiver_are_bounded_and_marked_incomplete():
    nodes = [{"id": "A", "kind": "node", "label": "A"}] + [{"id": f"J{i}", "kind": "junction", "label": ""} for i in range(17)]
    edges = [{"source": "A", "target": "J0", "direction": "forward", "label": ""}] + [
        {"source": f"J{i}", "target": f"J{target}", "direction": "forward", "label": ""}
        for i in range(17)
        for target in (i + 1, i + 2)
        if target < 17
    ]
    paths, uncertain = graph_projection({"diagram_nodes": nodes, "diagram_edges": edges})
    assert not paths
    assert "…" in uncertain
