"""未確定の属性と別の属性を取り違えた行の復元を、合成資料で検証する。"""

import pytest
from test_answer_flow_single_generation import _run_traced, context, record


@pytest.mark.parametrize("decision", ["除外", "保持", "不明な番号", "応答なし", "明示要求"])
def test_review_removes_only_a_confirmed_unrequested_row(decision):
    source = "設計値は低温時のみ12Ω。材質は銅。実測値は未記載。"
    ask_material = decision == "明示要求"
    question = "設計値と実測値を教えて。分かる点を示して。" + ("材質も教えて。" if ask_material else "")
    attempts = 0

    def reply(units):
        nonlocal attempts
        attempts += 1
        ref = f"[{units[source]}]"

        def fact(asked, phrase, answer):
            return {"text": source, "answers_which_question": asked, "requested_phrase": phrase, "short_answer": answer, "sources": [ref]}

        if attempts == 1:
            rows = [
                {"label": "設計値", "cells": [[fact("設計値はどの条件でいくつ？", "設計値", "低温時のみ12Ω")]]},
                {"label": "実測値", "cells": [{"requested_phrase": "実測値", "missing_input": "測定結果", "sources": [ref]}]},
                {"label": "材質", "cells": [[fact("素子の材質は何？", "材質" if ask_material else "分かる点", "銅")]]},
            ]
        elif decision == "応答なし":
            raise TimeoutError("合成の再試行の中断")
        else:
            rows = [
                {
                    "label": "99" if decision == "不明な番号" else "3",
                    "cells": [[fact("別の問い", None, "別の値")]] if decision == "保持" else [[]],
                },
                {"label": "1", "cells": [[]]},
            ]
        body = {"headers": ["項目", "値"], "gap_wording": "{missing}が必要", "rows": rows}
        return {"answer_body": body, "response_kind": "conditional", "confidence": "high"}

    response, calls, prompts = _run_traced(question, context(record(0, source, "合成表")), reply)
    assert "低温時のみ12Ω" in response.answer_text and "測定結果が必要" in response.answer_text
    assert ("銅" in response.answer_text) is (decision != "除外")
    assert "別の値" not in response.answer_text
    assert response.generation_trace["citation_lines"]
    assert len(calls) == (1 if ask_material else 2)
    if not ask_material:
        assert "指定された行" in prompts[1].removeprefix(prompts[0])
