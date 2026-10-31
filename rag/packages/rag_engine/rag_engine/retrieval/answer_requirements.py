"""回答と監査では完全な原質問を追跡する。規則による分解の網羅性を仮定しない。"""


def answer_requirements(question: str) -> list[dict[str, str]]:
    """小問を含む原質問全体を一つの監査単位として保持する。"""
    return [{"id": "Q1", "kind": "request", "text": question}] if question.strip() else []
