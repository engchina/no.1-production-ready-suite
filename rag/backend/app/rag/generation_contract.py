"""回答スタイルの決定論的な公開前契約検証。"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.config import GenerationProfile
from app.rag.generation_adapter import StructuredAnswer, validate_structured_answer

NO_RELEVANT_EVIDENCE_ANSWER = "提供された根拠には該当する情報がありません。"
JAPANESE_RE = re.compile(r"[ぁ-んァ-ン一-龯々ー]")
ENGLISH_RE = re.compile(r"[A-Za-z]")
SOURCE_ID_RE = re.compile(r"([^\s\[\](){}|,;]+#[A-Za-z0-9._:-]+)")
# 出典 ID の chunk_id 部分に使う文字。許可 ID の直後にこれが続く場合は別 ID の一部とみなす。
SOURCE_ID_TAIL_RE = re.compile(r"[A-Za-z0-9._:-]*")
# 許可 ID の直前に置ける区切り。ファイル名は空白・括弧・読点を含みうるため、許可 ID は
# 正規表現で切り出さず、文字列一致 + 前後の境界で見つける(#276)。
SOURCE_ID_LEADING_BOUNDARY = frozenset(" \t\r\n[](){}|,;:、，：「」（）【】")
CITATION_BLOCK_RE = re.compile(r"\[[^\]]+\]")
SENTENCE_END_RE = re.compile(r"[。！？!?]")


@dataclass(frozen=True)
class GenerationContractViolation(ValueError):
    """再生成可能な 1 回分の契約不一致。"""

    codes: tuple[str, ...]

    def __str__(self) -> str:
        return "回答スタイル契約に一致しません: " + ", ".join(self.codes)


class GenerationContractError(RuntimeError):
    """修復再生成後も契約を満たせず、回答を公開できない。"""

    safe_for_user = True

    def __init__(self, codes: list[str], *, attempt_count: int) -> None:
        self.codes = tuple(dict.fromkeys(codes))
        self.attempt_count = attempt_count
        super().__init__(
            "回答形式の検証に失敗しました。時間をおいて再試行するか、"
            "回答スタイルを変更してください。"
        )


def validate_generation_contract(
    *,
    profile: GenerationProfile,
    answer: str,
    context: str,
    allowed_source_ids: set[str],
) -> str:
    """profile 契約を検証し、必要なら正規化した answer を返す。"""

    text = answer.strip()
    if not text:
        raise GenerationContractViolation(("empty_answer",))
    if profile == "detailed_cited":
        _validate_detailed_cited(text, allowed_source_ids)
    elif profile == "strict_extractive":
        _validate_strict_extractive(text, context)
    elif profile == "structured_json":
        return _validate_structured_json(text, allowed_source_ids)
    elif profile == "bilingual_ja_en":
        _validate_bilingual(text)
    elif profile == "inline_cited":
        _validate_inline_cited(text, allowed_source_ids)
    return text


def structured_answer_json_schema() -> dict[str, object]:
    """OCI Responses text.format へ渡す JSON Schema。"""

    return StructuredAnswer.model_json_schema()


def repair_instruction(codes: list[str]) -> str:
    """同一 profile の完全再生成へ付加する短い修復指示。"""

    joined = ", ".join(dict.fromkeys(codes))
    return (
        "\n\n【再生成】前回の回答は公開前検証に失敗しました。"
        f"不一致コード: {joined}。元の回答を流用せず、同じ質問と context から"
        "指定された回答形式をすべて満たす回答を最初から生成してください。"
    )


def _validate_detailed_cited(text: str, allowed: set[str]) -> None:
    codes = _unknown_source_codes(text, allowed)
    # 見出しだけの段落(Markdown の # 行)は主張を含まないため出典を要求しない(#276)。
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", text)
        if paragraph.strip() and not _is_heading_only(paragraph)
    ]
    if any(not _valid_sources(paragraph, allowed) for paragraph in paragraphs):
        codes.append("missing_paragraph_citation")
    _raise_codes(codes)


def _validate_strict_extractive(text: str, context: str) -> None:
    if text == NO_RELEVANT_EVIDENCE_ANSWER:
        return
    normalized_context = _normalize_match_text(context)
    factual_sentences = _factual_sentences(text)
    normalized_sentences = [
        _normalize_match_text(_remove_citations(sentence)) for sentence in factual_sentences
    ]
    codes = []
    if not factual_sentences:
        codes.append("extractive_sentence_missing")
    elif any(not sentence for sentence in normalized_sentences):
        codes.append("extractive_sentence_empty")
    elif any(sentence not in normalized_context for sentence in normalized_sentences):
        codes.append("extractive_sentence_not_in_context")
    _raise_codes(codes)


def _validate_structured_json(text: str, allowed: set[str]) -> str:
    try:
        normalized = validate_structured_answer(text)
        model = StructuredAnswer.model_validate_json(normalized)
    except ValueError as exc:
        raise GenerationContractViolation(("structured_json_invalid",)) from exc
    codes: list[str] = []
    if not model.answer.strip():
        codes.append("structured_answer_empty")
    if not model.sources:
        codes.append("structured_sources_empty")
    if any(source not in allowed for source in model.sources):
        codes.append("unknown_citation")
    _raise_codes(codes)
    return model.model_dump_json()


def _validate_bilingual(text: str) -> None:
    marker = "English summary:"
    if marker not in text:
        raise GenerationContractViolation(("missing_english_summary",))
    japanese, english = text.split(marker, 1)
    codes: list[str] = []
    if not japanese.strip() or JAPANESE_RE.search(japanese) is None:
        codes.append("missing_japanese_body")
    summary = english.strip()
    if not summary:
        codes.append("missing_english_summary")
    else:
        if ENGLISH_RE.search(summary) is None:
            codes.append("english_summary_not_english")
        sentences = [item for item in re.findall(r"[^.!?]+(?:[.!?]+|$)", summary) if item.strip()]
        if not 1 <= len(sentences) <= 2:
            codes.append("english_summary_sentence_count")
    _raise_codes(codes)


def _validate_inline_cited(text: str, allowed: set[str]) -> None:
    codes = _unknown_source_codes(text, allowed)
    sentences = _factual_sentences(text)
    if not sentences or any(
        not _ends_with_valid_citation(sentence, allowed) for sentence in sentences
    ):
        codes.append("missing_inline_citation")
    if sentences and any(
        not _normalize_match_text(_remove_citations(sentence)) for sentence in sentences
    ):
        codes.append("inline_sentence_empty")
    _raise_codes(codes)


def _is_heading_only(paragraph: str) -> bool:
    lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
    return bool(lines) and all(line.startswith("#") for line in lines)


def _unknown_source_codes(text: str, allowed: set[str]) -> list[str]:
    known_spans = _known_source_spans(text, allowed)
    masked = list(text)
    for start, end, _source_id in known_spans:
        masked[start:end] = " " * (end - start)
    cited = {match.group(1).rstrip("。.!?]") for match in SOURCE_ID_RE.finditer("".join(masked))}
    return ["unknown_citation"] if cited.difference(allowed) else []


def _known_source_spans(text: str, allowed: set[str]) -> list[tuple[int, int, str]]:
    """本文中の許可 ID の出現位置を返す。

    ファイル名に空白・括弧・読点を含む ID(例: ``就業規則 (2024年版).pdf#c1``)も
    そのまま一致させる。前後が ID の一部として続く位置(``xa.pdf#c1`` や ``c10``)は除く。
    """

    spans: list[tuple[int, int, str]] = []
    for source_id in sorted(allowed, key=len, reverse=True):
        if not source_id:
            continue
        start = text.find(source_id)
        while start != -1:
            end = start + len(source_id)
            if _has_leading_boundary(text, start) and _has_trailing_boundary(text, end):
                spans.append((start, end, source_id))
            start = text.find(source_id, start + 1)
    return spans


def _has_leading_boundary(text: str, start: int) -> bool:
    return start == 0 or text[start - 1] in SOURCE_ID_LEADING_BOUNDARY


def _has_trailing_boundary(text: str, end: int) -> bool:
    tail = SOURCE_ID_TAIL_RE.match(text, end)
    # 文末の "." や ":" は区切りとして扱い、"c1-2" や "c10" のような続きは別 ID とみなす。
    return tail is None or not tail.group(0).strip("._:-")


def _valid_sources(text: str, allowed: set[str]) -> set[str]:
    return {source_id for _start, _end, source_id in _known_source_spans(text, allowed)}


def _ends_with_valid_citation(sentence: str, allowed: set[str]) -> bool:
    candidate = sentence.rstrip().rstrip("。！？!?").rstrip()
    blocks = list(CITATION_BLOCK_RE.finditer(candidate))
    if not blocks or blocks[-1].end() != len(candidate):
        return False
    return bool(_valid_sources(blocks[-1].group(0), allowed))


def _factual_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in (line.strip() for line in text.splitlines()):
        if not line or line.startswith(("#", "English summary:")):
            continue
        start = 0
        for match in SENTENCE_END_RE.finditer(line):
            end = match.end()
            while end < len(line) and line[end:].lstrip().startswith("["):
                leading = len(line[end:]) - len(line[end:].lstrip())
                block = CITATION_BLOCK_RE.match(line, end + leading)
                if block is None:
                    break
                end = block.end()
            sentence = line[start:end].strip(" -•\t")
            if sentence:
                sentences.append(sentence)
            start = end
        tail = line[start:].strip(" -•\t")
        if tail:
            sentences.append(tail)
    return sentences


def _remove_citations(text: str) -> str:
    return CITATION_BLOCK_RE.sub("", text)


def _normalize_match_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    normalized = re.sub(r"\s+", "", normalized)
    return normalized.strip("。！？!?.,、:：;；")


def _raise_codes(codes: list[str]) -> None:
    unique = tuple(dict.fromkeys(codes))
    if unique:
        raise GenerationContractViolation(unique)
