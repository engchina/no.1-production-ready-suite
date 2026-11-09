"""非資料の確認と結果を、送達する一組の原文単位として束縛する。"""

import copy
import importlib
import json

import pytest

from rag_engine.evaluation.atomic_source_groups import KEY, trusted
from rag_engine.evaluation.final_validation_units import prepare_unit_request


def prepared(items):
    units = importlib.import_module("rag_engine.evaluation.final_validation_units")
    return units.prepare_unit_request({"evidence_items": list(ev._evidence_fragments(items))})


def test_native_document_and_public_marker_do_not_get_atomic_privilege():
    text = "利用者の答え: 値17です。\n実行した SQL: SELECT 4\n" + ("a" * 2100)
    for extra in [{}, {KEY: "premise"}, {KEY: "data"}]:
        p = prepared([{"id": "premise:1", "source": "合成文書", "text": text, **extra}])
        assert len(p.units) > 1
        assert not trusted({"text": text, **extra})


