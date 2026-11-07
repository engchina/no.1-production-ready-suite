"""品質評価の判定のモデルの代わり（schema ごとに応答する決定論のスタブ。#3576）。"""

import json
import threading

from rag_engine.evaluation.answer_eval import (
    QUESTION_EVIDENCE_ID,
    AnswerCorrectnessOutput,
    AnswerEvaluationOutput,
    ClaimJudgement,
    CoverageJudgement,
    CoverageOutput,
    EvidenceCoverageCheck,
    EvidenceCoverageOutput,
    StandardAnswerScope,
)

JUDGED_STATUSES = ("supported", "unsupported", "contradicted", "data_confirmation")
# 質問の対象の抽出（#4351）の録画応答の既定: 名前の提及が無い（役割の段は呼ばれない）。
NO_SUBJECTS = {"QuestionMentionAnchors": {"mentions": []}}
SUBJECT_SCHEMAS = ("QuestionMentionAnchors", "QuestionEntityRoles")


def judge(evaluated: AnswerEvaluationOutput, *, scope=None, correctness="correct", failures=None, calls=None, subjects=None):
    """evaluated（網羅と主張の判定の元）に従って、網羅・主張・正確性・比較範囲の呼び出しに応答する。

    網羅は requirement_index ごとに evaluated.coverage_checks の判定を返す（段落の ID が無ければ引用を含む段落）。
    主張は段落の ID か引用で evaluated.claim_checks の判定を選び、無ければ unsupported。supported / contradicted で
    evidence_id が無ければ最初の資料の根拠を書く。failures は schema の名前 → 先に送出する例外の list、calls には
    (schema, 入力) を残す。subjects は質問の対象の抽出の schema の名前 → 録画応答（既定 ``NO_SUBJECTS``）。
    対象の抽出の呼び出しは calls に残さない（``subject_calls`` で数える）。
    """
    recorded = NO_SUBJECTS if subjects is None else subjects
    pending = dict((name, list(errors)) for name, errors in (failures or dict()).items())
    lock = threading.Lock()

    def coverage(inputs):
        passages = inputs["answer_passages"]
        by_index = dict((check.requirement_index, check) for check in evaluated.coverage_checks)
        items = []
        for requirement in inputs["requirements"]:
            check = by_index.get(requirement["requirement_index"])
            if check is None:
                continue
            passage_id = check.answer_passage_id or next(
                (p["id"] for p in passages if check.answer_quote and check.answer_quote in p["text"]), ""
            )
            if check.status != "missing" and not passage_id and passages:
                passage_id = passages[0]["id"]
            items.append(
                CoverageJudgement(
                    requirement_index=check.requirement_index, reason="理由", status=check.status, answer_passage_id=passage_id
                )
            )
        return CoverageOutput(coverage_checks=items)

    def claim(inputs):
        passage = inputs["passage"]
        found = next(
            (
                c
                for c in evaluated.claim_checks
                if c.answer_passage_id == passage["id"] or (c.answer_quote and c.answer_quote in passage["text"])
            ),
            None,
        )
        documents = [item["evidence_id"] for item in inputs["evidence_items"] if item["evidence_id"] != QUESTION_EVIDENCE_ID]
        status = found.status if found and found.status in JUDGED_STATUSES else "unsupported"
        evidence_id = found.evidence_id if found and found.evidence_id else ""
        if not evidence_id and status in ("supported", "contradicted") and documents:
            evidence_id = documents[0]
        return ClaimJudgement(
            claim_kind=found.claim_kind if found else "ordinary",
            reason=found.reason if found else "理由",
            status=status,
            evidence_id=evidence_id,
        )

    def parse(system, prompt, settings, schema, **kwargs):
        inputs = json.loads(prompt)
        if schema.__name__ in SUBJECT_SCHEMAS:
            parse.subject_calls.append((schema.__name__, inputs))
            return schema.model_validate(recorded[schema.__name__])
        with lock:
            if calls is not None:
                calls.append((schema, inputs))
            queue = pending.get(schema.__name__)
            error = queue.pop(0) if queue else None
        if error is not None:
            raise error
        if schema is StandardAnswerScope and scope is not None:
            return scope
        if schema is AnswerCorrectnessOutput:
            return AnswerCorrectnessOutput(verdict=correctness, reason="理由")
        if schema is CoverageOutput:
            return coverage(inputs)
        if schema is ClaimJudgement:
            return claim(inputs)
        if schema is EvidenceCoverageOutput:
            first = inputs["evidence_items"][0]["text"].split("|", 1)[0]
            return EvidenceCoverageOutput(
                checks=[
                    EvidenceCoverageCheck(requirement_index=item["requirement_index"], reason="理由", status="found", lines=[first])
                    for item in inputs["requirements"]
                ]
            )
        raise AssertionError(schema)

    parse.subject_calls = []
    return parse


def calls_of(calls, schema):
    """記録した呼び出しのうち schema の入力。"""
    return [inputs for called, inputs in calls if called is schema]
