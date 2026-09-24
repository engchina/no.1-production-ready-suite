"""根拠を明示して呼び出す回答生成サービス。"""
from docrag.ports import Generator
from docrag.models.contracts import GenerationRequest, AnswerResult


class GenerationService:
    """空検索のときは回答モデルを呼ばず不足を返す。"""
    def __init__(self, generator: Generator):
        self.generator = generator

    def generate(self, request: GenerationRequest) -> AnswerResult:
        """質問と根拠から回答する。空質問は ValueError。"""
        if not request.question.strip():
            raise ValueError("question must not be empty")
        if not request.evidence:
            return AnswerResult(
                text="検索された資料に回答を裏付ける十分な根拠がないため、回答できません。",
                confidence="low", insufficient_reason="retrieval returned no evidence", needs_human_review=True,
            )
        return self.generator.generate(request)
