"""検索・実行フローのテストから回答生成を切り離すための差し替え。

生成そのものは tests/test_grounded_answer.py が検証する。ここでは、テストが
`parse_text_response` に用意した最後の応答を生成結果として受け取り、検索・CRAG・
payload の検証を生成の出力形式から独立させる。
"""
import re
from unittest.mock import patch

from docrag.generation import answering, grounded
from pydantic import BaseModel, ConfigDict

from docrag.models.llm import GroundedAudit, GroundedDraft, UsedImageOutput


class AnswerOutput(BaseModel):
    """テストが生成結果を指定するための入れ物。本番の LLM 出力スキーマではない。"""
    model_config = ConfigDict(extra="forbid")
    answer: str
    confidence: str = ""
    question_type: list[str] = []
    used_images: list[UsedImageOutput] = []
    reasoning_summary: str = ""
    insufficient_reason: str = ""
    needs_human_review: bool | None = None
    external_data_required: bool | None = None
    external_data_items: list[str] = []


def stub_generation(testcase, target: str = "docrag.generation.answering.synthesize_grounded_answer"):
    """生成を1回の LLM 呼出へ置き換える。呼出順・provider・画像添付の検証はそのまま使える。"""
    def fake(question, context, settings, **options):
        provider = options.get("answer_llm_provider")
        images = tuple(options.get("image_evidence") or ())
        mode = options["image_prompt_mode"]
        # 実際に生成へ渡る入力で呼び、根拠・用語補助・質問理解が prompt へ届くことを検証できるようにする。
        spans = answering._grounded_spans(question, context, images, options.get("runtime_knowledge"))
        prompt = answering._grounded_prompt(
            question, spans, context, images, None, options.get("question_plan"), options.get("runtime_knowledge"),
            options.get("inquiry_conditions"), options.get("query_expansion"))
        if mode == "vision_attachments":
            output = answering.parse_multimodal_response(
                grounded.GENERATE_SYSTEM_PROMPT, prompt, [i["prompt_path"] for i in images if i.get("prompt_path")], settings,
                AnswerOutput, provider_id=provider)
        else:
            output = answering.parse_text_response(grounded.GENERATE_SYSTEM_PROMPT, prompt, settings, AnswerOutput, provider_id=provider)
        response = answering._normalize_answer_response(answering.AnswerResponse(
            answer_text=output.answer, confidence=output.confidence, question_type=tuple(output.question_type),
            used_images=tuple(answering._used_image_entries([i.model_dump() for i in output.used_images])),
            reasoning_summary=output.reasoning_summary, insufficient_reason=output.insufficient_reason,
            needs_human_review=output.needs_human_review, external_data_required=output.external_data_required,
            external_data_items=tuple(output.external_data_items)))
        return answering.GroundedAnswer(response, context, images, mode)
    mocked = patch(target, side_effect=fake)
    started = mocked.start()
    testcase.addCleanup(mocked.stop)
    return started


def echo_model(system, prompt, settings, schema, **options):
    """最初の根拠の先頭行をそのまま引用する。実際の生成経路で prompt の内容を確認する用途。"""
    if schema is GroundedDraft:
        match = re.search(r"\[(E[0-9]+)\][^\n]*\n([^\n]+)", prompt)
        items = [{"kind": "rule", "text": match.group(2), "evidence_id": match.group(1), "quote": match.group(2)}] if match else []
        return GroundedDraft.model_validate({"summary": "answer", "items": items, "confidence": "high"})
    if schema is GroundedAudit:
        return GroundedAudit.model_validate({"goal_alignment": "aligned", "summary_supported": True, "reviews": [
            {"index": 0, "support": "supported", "applicability": "matched", "reason": "原文どおり"}]})
    raise AssertionError(f"unexpected schema: {schema.__name__}")
