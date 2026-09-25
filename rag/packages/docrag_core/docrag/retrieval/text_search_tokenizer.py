"""Oracle Text 検索に渡す日本語・英数字 token を生成する。"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata as importlib_metadata
import importlib.util
import re
import threading
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from docrag.knowledge.domain_keywords import extract_domain_keywords
from docrag.retrieval.definition_evidence import definition_labels


TEXT_SEARCH_TOKENIZER_AUTO = "auto"
TEXT_SEARCH_TOKENIZER_REGEX = "regex"
TEXT_SEARCH_TOKENIZER_SUDACHI = "sudachi"
DEFAULT_TEXT_SEARCH_TOKENIZER = TEXT_SEARCH_TOKENIZER_SUDACHI
DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT = "full"
DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER = "auto"
MAX_TEXT_SEARCH_TOKENS = 24
MAX_ORACLE_TEXT_QUERY_CHARS = 3800
MAX_TEXT_SEARCH_TOKEN_TRACE = 64

_VALID_TOKENIZER_MODES = {
    TEXT_SEARCH_TOKENIZER_AUTO,
    TEXT_SEARCH_TOKENIZER_REGEX,
    TEXT_SEARCH_TOKENIZER_SUDACHI,
}
_VALID_LATIN_STEMMERS = {"auto", "snowball", "light", "none"}
_SUDACHI_MAX_BYTES = 20000
_SAFE_BOUND = set(" \u3000、。！？，．・；：「」『』（）【】\n\t")
_KANA_ONLY = re.compile(r"^[ぁ-ゖー]+$")
_KATAKANA = re.compile(r"^[ァ-ヶー]+$")
_LATIN_ALPHA = re.compile(r"^[a-z]+$")
_KANJI_OR_KATAKANA = re.compile(r"[ァ-ヶ一-龯々]")
_HIRAGANA_PARTICLE_EDGE = set("がをにはへとでもの")
_POLITE_SUFFIXES = ("してください", "下さい", "ください", "します", "したい", "です", "ます")
_REGEX_TERM_PATTERN = re.compile(
    r"[a-z][a-z0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*"
    r"|v?\d+(?:\.\d+)+"
    r"|[a-z]+\d+[a-z0-9]*"
    r"|\d+[a-z][a-z0-9]*"
    r"|[0-9a-z_]{2,}"
    r"|[ぁ-んァ-ン一-龯々ー]{2,}"
)
_EXTRA_PATTERNS = (
    re.compile(r"[a-z][a-z0-9]*-[a-z0-9]+(?:-[a-z0-9]+)*"),
    re.compile(r"[a-z]+\d+[a-z0-9]*"),
    re.compile(r"\d+[a-z][a-z0-9]*"),
    re.compile(r"v?\d+(?:\.\d+)+"),
)
_REWRITE_RULES = (
    (re.compile(r"[〜～]"), "~"),
    (re.compile(r"[－‐‑–—―−]"), "-"),
    (re.compile(r"[　\s]+"), " "),
)

_KEEP_POS1 = {"名詞", "動詞", "形容詞", "形状詞", "副詞"}
_DROP_POS2 = {"非自立可能", "助動詞語幹"}
_ALLOW = {
    "図",
    "表",
    "鍵",
    "税",
    "熱",
    "軸",
    "型",
    "列",
    "行",
}
_DENY: set[str] = set()
_GENERIC_SEARCH_TERMS = {
    "表示",
    "確認",
    "画面",
    "情報",
    "方法",
    "項目",
    "操作",
    "処理",
    "データ",
    "一覧",
    "内容",
    "説明",
    "対象",
    "登録",
    "変更",
    "検索",
    "入力",
    "出力",
    "選択",
    "場合",
    "手順",
    "見る",
    "見",
}
_STOPWORDS_SURFACE = {
    "こと",
    "もの",
    "ため",
    "とき",
    "わけ",
    "はず",
    "うち",
    "する",
    "ある",
    "いる",
    "なる",
    "できる",
    "行う",
    "使う",
    "つく",
    "よう",
    "教える",
    "知る",
    "何",
    "どう",
    "どこ",
    "いつ",
    "可能",
    "必要",
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "i",
    "you",
    "we",
    "they",
    "he",
    "she",
    "my",
    "your",
    "our",
    "their",
    "this",
    "that",
    "these",
    "those",
    "to",
    "of",
    "in",
    "on",
    "at",
    "for",
    "with",
    "from",
    "by",
    "about",
    "and",
    "or",
    "but",
    "not",
    "no",
    "how",
    "what",
    "when",
    "where",
    "why",
    "which",
    "who",
    "can",
    "could",
    "will",
    "would",
    "should",
    "may",
    "might",
    "must",
    "please",
    "me",
    "there",
    "any",
    "some",
}
ORACLE_TEXT_RESERVED_TERMS = {
    "about",
    "accum",
    "and",
    "bt",
    "btg",
    "bti",
    "btp",
    "equiv",
    "fuzzy",
    "haspath",
    "inpath",
    "mdata",
    "minus",
    "near",
    "not",
    "nt",
    "ntg",
    "nti",
    "ntp",
    "or",
    "pattern",
    "pt",
    "rt",
    "sqe",
    "syn",
    "tr",
    "trsyn",
    "tt",
    "within",
}
_EN_IRREGULAR = {
    "ran": "run",
    "went": "go",
    "gone": "go",
    "broke": "break",
    "broken": "break",
    "sent": "send",
    "built": "build",
    "wrote": "write",
    "written": "write",
    "found": "find",
    "lost": "lose",
    "made": "make",
    "took": "take",
    "taken": "take",
    "got": "get",
    "gotten": "get",
    "saw": "see",
    "seen": "see",
}


@dataclass(frozen=True)
class TextSearchTokenizerConfig:
    """Oracle Text 用 tokenizer の実装と辞書設定を保持します。"""
    mode: str = DEFAULT_TEXT_SEARCH_TOKENIZER
    sudachi_dict_type: str = DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT
    sudachi_config_path: str = ""
    latin_stemmer: str = DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER


class TextSearchTokenizerUnavailable(RuntimeError):
    """設定された tokenizer の依存が利用できない状態を表します。"""
    pass


@dataclass(frozen=True)
class TextSearchTokenizationResult:
    """Oracle Text 検索用 token と切り詰め有無を保持します。"""
    tokens: tuple[str, ...]
    candidate_tokens: tuple[str, ...] = ()
    truncated_tokens: tuple[str, ...] = ()
    candidate_count: int = 0
    truncated_count: int = 0
    max_tokens: int = MAX_TEXT_SEARCH_TOKENS
    matched_domain_keywords: tuple[str, ...] = ()
    selected_domain_keywords: tuple[str, ...] = ()
    truncated_domain_keywords: tuple[str, ...] = ()

    @property
    def truncated(self) -> bool:
        """token 上限により検索 token が切り詰められたかを返します。"""
        return self.truncated_count > 0


@dataclass(frozen=True)
class _TokenCandidate:
    term: str
    first_order: int
    priority: int = 0


_dict_lock = threading.Lock()
_dicts: dict[tuple[str, str], Any] = {}
_local = threading.local()


def normalize_text_search_text(text: Any) -> str:
    """Oracle Text token 化前の文字列を NFKC と空白規則で正規化します。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    for pattern, replacement in _REWRITE_RULES:
        normalized = pattern.sub(replacement, normalized)
    normalized = re.sub(r"[\x00-\x1f\x7f]+", " ", normalized)
    return normalized.casefold().strip()


def tokenize_text_search_query(
    query: str,
    *,
    domain_keywords: Sequence[str] | None = None,
    config: TextSearchTokenizerConfig | None = None,
    max_tokens: int = MAX_TEXT_SEARCH_TOKENS,
) -> list[str]:
    """質問文を Oracle Text query に使う token 列へ変換します。"""
    return list(
        tokenize_text_search_query_with_trace(
            query,
            domain_keywords=domain_keywords,
            config=config,
            max_tokens=max_tokens,
        ).tokens
    )


def tokenize_text_search_query_with_trace(
    query: str,
    *,
    domain_keywords: Sequence[str] | None = None,
    config: TextSearchTokenizerConfig | None = None,
    max_tokens: int = MAX_TEXT_SEARCH_TOKENS,
) -> TextSearchTokenizationResult:
    """token 化結果に加えて採用・除外理由の trace を返します。"""
    cfg = normalize_text_search_tokenizer_config(config)
    limit = max(1, int(max_tokens or 1))
    candidates: dict[str, _TokenCandidate] = {}
    normalized_domain_keywords = tuple(domain_keywords or ())
    order = 0

    def add(term: Any, *, priority: int = 0) -> None:
        nonlocal order
        normalized = _normalize_term(term)
        if not normalized or normalized in ORACLE_TEXT_RESERVED_TERMS:
            return
        existing = candidates.get(normalized)
        if existing is None:
            candidates[normalized] = _TokenCandidate(
                term=normalized,
                first_order=order,
                priority=priority,
            )
            order += 1
            return
        if priority > existing.priority:
            candidates[normalized] = _TokenCandidate(
                term=normalized,
                first_order=existing.first_order,
                priority=priority,
            )

    matched_keywords = extract_domain_keywords(
        query, normalized_domain_keywords, limit=max(1, len(normalized_domain_keywords)),
    )
    for keyword in matched_keywords:
        add(keyword, priority=3)
        for token in _content_tokens(keyword, cfg):
            add(token, priority=2)

    # 品詞判定が接頭辞とする一文字でも、質問で明示された列名は失わない。
    for label in definition_labels(query):
        add(label, priority=3)
    for token in _content_tokens(query, cfg):
        add(token)
    ordered_candidates = list(candidates.values())
    if len(ordered_candidates) <= limit:
        selected = sorted(ordered_candidates, key=lambda candidate: candidate.first_order)
    else:
        selected = sorted(ordered_candidates, key=_ranked_token_sort_key)[:limit]
    selected_keys = {candidate.term for candidate in selected}
    ranked_candidates = sorted(ordered_candidates, key=_ranked_token_sort_key)
    truncated_tokens = tuple(
        candidate.term
        for candidate in ranked_candidates
        if candidate.term not in selected_keys
    )
    return TextSearchTokenizationResult(
        tokens=tuple(candidate.term for candidate in selected),
        matched_domain_keywords=tuple(matched_keywords),
        selected_domain_keywords=tuple(k for k in matched_keywords if _normalize_term(k) in selected_keys),
        truncated_domain_keywords=tuple(k for k in matched_keywords if _normalize_term(k) not in selected_keys),
        candidate_tokens=tuple(candidate.term for candidate in ranked_candidates[:MAX_TEXT_SEARCH_TOKEN_TRACE]),
        truncated_tokens=truncated_tokens[:MAX_TEXT_SEARCH_TOKEN_TRACE],
        candidate_count=len(ordered_candidates),
        truncated_count=max(0, len(ordered_candidates) - len(selected)),
        max_tokens=limit,
    )


def _ranked_token_sort_key(candidate: _TokenCandidate) -> tuple[float, int, str]:
    return (-_token_retrieval_score(candidate), candidate.first_order, candidate.term)


def _token_retrieval_score(candidate: _TokenCandidate) -> float:
    term = candidate.term
    score = float(candidate.priority * 1000)
    length = len(term)
    if _is_code_like_token(term):
        score += 420
    if term in _ALLOW:
        score += 180
    if _KANJI_OR_KATAKANA.search(term):
        score += min(length, 24) * 18
    elif _LATIN_ALPHA.fullmatch(term):
        score += min(length, 24) * 12
    else:
        score += min(length, 24) * 8
    if term in _GENERIC_SEARCH_TERMS:
        score -= 40
    if length == 1 and term not in _ALLOW:
        score -= 120
    return score


def _is_code_like_token(term: str) -> bool:
    if not term:
        return False
    if re.fullmatch(r"[0-9]{2,8}", term):
        return True
    return any(pattern.fullmatch(term) for pattern in _EXTRA_PATTERNS)


def build_oracle_text_query(
    terms: Sequence[str],
    *,
    max_chars: int = MAX_ORACLE_TEXT_QUERY_CHARS,
) -> str:
    """token 列から長さ上限内の Oracle Text query を構築します。"""
    pieces: list[str] = []
    used_chars = 0
    for term in terms:
        escaped = escape_oracle_text_term(term)
        if not escaped:
            continue
        addition = len(escaped) + (4 if pieces else 0)
        if used_chars + addition > max(1, int(max_chars or 1)):
            break
        pieces.append(escaped)
        used_chars += addition
    return " OR ".join(pieces)


def escape_oracle_text_term(term: Any) -> str:
    """Oracle Text query 内で安全に使える term 文字列へ escape します。"""
    normalized = _normalize_term(term)
    if not normalized:
        return ""
    return "{" + normalized.replace("}", "}}") + "}"


def tokenizer_fingerprint(config: TextSearchTokenizerConfig | None = None) -> str:
    """tokenizer 設定の変更検知に使う fingerprint を返します。"""
    cfg = normalize_text_search_tokenizer_config(config)
    key = "|".join(
        [
            cfg.mode,
            cfg.sudachi_dict_type,
            cfg.sudachi_config_path,
            cfg.latin_stemmer,
            _package_version("sudachipy"),
            _package_version(f"sudachidict_{cfg.sudachi_dict_type}"),
            _package_version(f"sudachidict-{cfg.sudachi_dict_type}"),
            _package_version("PyStemmer") if _resolve_latin_stemmer(cfg.latin_stemmer) == "snowball" else "-",
            hashlib.sha256("\n".join(sorted(_STOPWORDS_SURFACE)).encode()).hexdigest()[:8],
        ]
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def normalize_text_search_tokenizer_config(
    config: TextSearchTokenizerConfig | None,
) -> TextSearchTokenizerConfig:
    """未指定 tokenizer 設定へ既定値を補完します。"""
    cfg = config or TextSearchTokenizerConfig()
    mode = str(cfg.mode or DEFAULT_TEXT_SEARCH_TOKENIZER).strip().casefold().replace("-", "_")
    if mode in {"", "default"}:
        mode = DEFAULT_TEXT_SEARCH_TOKENIZER
    if mode not in _VALID_TOKENIZER_MODES:
        mode = DEFAULT_TEXT_SEARCH_TOKENIZER
    dict_type = str(cfg.sudachi_dict_type or DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT).strip()
    if not dict_type:
        dict_type = DEFAULT_TEXT_SEARCH_TOKENIZER_SUDACHI_DICT
    config_path = str(cfg.sudachi_config_path or "").strip()
    latin_stemmer = str(cfg.latin_stemmer or DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER).strip().casefold()
    if latin_stemmer not in _VALID_LATIN_STEMMERS:
        latin_stemmer = DEFAULT_TEXT_SEARCH_TOKENIZER_LATIN_STEMMER
    return TextSearchTokenizerConfig(
        mode=mode,
        sudachi_dict_type=dict_type,
        sudachi_config_path=config_path,
        latin_stemmer=latin_stemmer,
    )


def _content_tokens(query: str, config: TextSearchTokenizerConfig) -> list[str]:
    if config.mode == TEXT_SEARCH_TOKENIZER_REGEX:
        return _regex_tokens(query, config)
    try:
        return _sudachi_tokens(query, config)
    except Exception as exc:
        if config.mode == TEXT_SEARCH_TOKENIZER_SUDACHI:
            raise TextSearchTokenizerUnavailable(
                "Sudachi tokenizer is unavailable. Install project requirements "
                "or set TEXT_SEARCH_TOKENIZER=auto/regex for emergency fallback."
            ) from exc
        return _regex_tokens(query, config)


def _regex_tokens(text: str, config: TextSearchTokenizerConfig) -> list[str]:
    normalized = normalize_text_search_text(text)
    tokens: list[str] = []
    stopwords = _regex_stopwords()
    for term in _REGEX_TERM_PATTERN.findall(normalized):
        _push_token(term, tokens, config, stopwords, bridge_latin=False)
    return tokens


def _sudachi_tokens(text: str, config: TextSearchTokenizerConfig) -> list[str]:
    sudachi_tokenizer = _get_sudachi_tokenizer(config.sudachi_dict_type, config.sudachi_config_path)
    split_mode = _sudachi_split_mode("C")
    sub_split_mode = _sudachi_split_mode("A")
    normalized = normalize_text_search_text(text)
    if not normalized:
        return []
    tokens: list[str] = []
    katakana_run: list[str] = []
    stopwords = _sudachi_stopwords(config.sudachi_dict_type, config.sudachi_config_path)

    for pattern in _EXTRA_PATTERNS:
        for matched in pattern.findall(normalized):
            _push_token(matched, tokens, config, stopwords, derive_latin=False)

    def flush_katakana() -> None:
        if len(katakana_run) >= 2:
            _push_token("".join(katakana_run), tokens, config, stopwords)
        katakana_run.clear()

    for chunk in _split_for_sudachi(normalized):
        for morpheme in sudachi_tokenizer.tokenize(chunk, split_mode):
            norm = morpheme.normalized_form().casefold()
            if _KATAKANA.fullmatch(norm):
                katakana_run.append(norm)
            else:
                flush_katakana()
            _emit_morpheme(morpheme, tokens, config, stopwords)
            sub_morphemes = morpheme.split(sub_split_mode)
            if len(sub_morphemes) > 1:
                for sub_morpheme in sub_morphemes:
                    _emit_morpheme(sub_morpheme, tokens, config, stopwords)
        flush_katakana()
    return tokens


def _emit_morpheme(
    morpheme: Any,
    tokens: list[str],
    config: TextSearchTokenizerConfig,
    stopwords: frozenset[str],
) -> None:
    pos = morpheme.part_of_speech()
    if not pos or pos[0] not in _KEEP_POS1 or (len(pos) > 1 and pos[1] in _DROP_POS2):
        return
    surface = morpheme.surface()
    if " " in surface.strip():
        for part in surface.split():
            for sub_morpheme in _get_sudachi_tokenizer(
                config.sudachi_dict_type,
                config.sudachi_config_path,
            ).tokenize(part, _sudachi_split_mode("C")):
                _emit_morpheme(sub_morpheme, tokens, config, stopwords)
        return
    normalized = morpheme.normalized_form().casefold()
    _push_token(normalized, tokens, config, stopwords)
    surface_term = normalize_text_search_text(surface)
    if surface_term != normalized:
        _push_token(surface_term, tokens, config, stopwords)


def _push_token(
    raw_token: Any,
    tokens: list[str],
    config: TextSearchTokenizerConfig,
    stopwords: frozenset[str],
    *,
    derive_latin: bool = True,
    bridge_latin: bool = True,
) -> None:
    token = _normalize_term(raw_token)
    if not _keep_token(token, stopwords):
        return
    tokens.append(token)
    if not derive_latin or not _LATIN_ALPHA.fullmatch(token):
        return
    for derived in _latin_variants(token, config):
        if _keep_token(derived, stopwords):
            tokens.append(derived)
            if bridge_latin:
                for bridged in _bridge_latin_to_sudachi(derived, config, stopwords):
                    tokens.append(bridged)


def _keep_token(token: str, stopwords: frozenset[str]) -> bool:
    if not token or token in _DENY or token in ORACLE_TEXT_RESERVED_TERMS:
        return False
    if token in _ALLOW:
        return True
    if token in stopwords:
        return False
    if len(token) == 1 and _KANA_ONLY.fullmatch(token):
        return False
    return True


def _latin_variants(token: str, config: TextSearchTokenizerConfig) -> list[str]:
    variants: list[str] = []
    irregular = _EN_IRREGULAR.get(token)
    if irregular:
        variants.append(irregular)
    stemmer = _resolve_latin_stemmer(config.latin_stemmer)
    if stemmer == "snowball":
        stem = _get_en_stemmer().stemWord(token)
        if stem != token:
            variants.append(stem)
    elif stemmer == "light":
        variants.extend(_plural_variants(token))
    return _dedupe(variants)


def _plural_variants(token: str) -> list[str]:
    if token.endswith("sses") and len(token) > 5:
        return [token[:-2]]
    if token.endswith("ies") and len(token) > 4:
        return [token[:-3] + "y"]
    if token.endswith(("xes", "zes")) and len(token) > 4:
        return [token[:-2]]
    if token.endswith(("ches", "shes")) and len(token) > 5:
        return [token[:-2], token[:-1]]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")) and len(token) > 3:
        return [token[:-1]]
    return []


def _bridge_latin_to_sudachi(
    token: str,
    config: TextSearchTokenizerConfig,
    stopwords: frozenset[str],
) -> list[str]:
    bridged: list[str] = []
    tokenizer = _get_sudachi_tokenizer(config.sudachi_dict_type, config.sudachi_config_path)
    for morpheme in tokenizer.tokenize(token, _sudachi_split_mode("C")):
        normalized = _normalize_term(morpheme.normalized_form())
        if normalized != token and _KATAKANA.fullmatch(normalized) and _keep_token(normalized, stopwords):
            bridged.append(normalized)
    return _dedupe(bridged)


def _normalize_term(term: Any) -> str:
    normalized = normalize_text_search_text(term)
    normalized = normalized.strip("{}[]()")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if " " in normalized:
        return ""
    return _trim_japanese_particle_edges(normalized)


def _trim_japanese_particle_edges(term: str) -> str:
    if len(term) <= 2 or not _KANJI_OR_KATAKANA.search(term):
        return term
    for suffix in _POLITE_SUFFIXES:
        if term.endswith(suffix) and _KANJI_OR_KATAKANA.search(term[: -len(suffix)]):
            term = term[: -len(suffix)]
            break
    while len(term) > 2 and term[0] in _HIRAGANA_PARTICLE_EDGE and _KANJI_OR_KATAKANA.search(term[1:]):
        term = term[1:]
    while len(term) > 2 and term[-1] in _HIRAGANA_PARTICLE_EDGE and _KANJI_OR_KATAKANA.search(term[:-1]):
        term = term[:-1]
    return term


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _resolve_latin_stemmer(value: str) -> str:
    stemmer = str(value or "auto").strip().casefold()
    if stemmer == "auto":
        return "snowball" if importlib.util.find_spec("Stemmer") else "light"
    if stemmer == "snowball":
        return "snowball"
    if stemmer in {"light", "none"}:
        return stemmer
    return "light"


def _get_en_stemmer() -> Any:
    stemmer = getattr(_local, "en_stemmer", None)
    if stemmer is None:
        import Stemmer

        stemmer = _local.en_stemmer = Stemmer.Stemmer("english")
    return stemmer


@functools.lru_cache(maxsize=16)
def _sudachi_stopwords(dict_type: str, config_path: str) -> frozenset[str]:
    stopwords: set[str] = {normalize_text_search_text(word) for word in _STOPWORDS_SURFACE}
    tokenizer = _get_sudachi_tokenizer(dict_type, config_path)
    for word in _STOPWORDS_SURFACE:
        for morpheme in tokenizer.tokenize(word, _sudachi_split_mode("C")):
            stopwords.add(_normalize_term(morpheme.normalized_form()))
    return frozenset(stopwords)


@functools.lru_cache(maxsize=1)
def _regex_stopwords() -> frozenset[str]:
    return frozenset(normalize_text_search_text(word) for word in _STOPWORDS_SURFACE)


def _get_sudachi_tokenizer(dict_type: str, config_path: str) -> Any:
    key = (dict_type, config_path)
    cache = getattr(_local, "sudachi_tokenizers", None)
    if cache is None:
        cache = _local.sudachi_tokenizers = {}
    if key not in cache:
        cache[key] = _get_sudachi_dictionary(dict_type, config_path).create()
    return cache[key]


def _get_sudachi_dictionary(dict_type: str, config_path: str) -> Any:
    key = (dict_type, config_path)
    if key not in _dicts:
        with _dict_lock:
            if key not in _dicts:
                from sudachipy import dictionary

                kwargs: dict[str, str] = {"dict": dict_type}
                if config_path:
                    kwargs["config_path"] = config_path
                _dicts[key] = dictionary.Dictionary(**kwargs)
    return _dicts[key]


@functools.lru_cache(maxsize=3)
def _sudachi_split_mode(mode: str) -> Any:
    from sudachipy import tokenizer

    return {
        "A": tokenizer.Tokenizer.SplitMode.A,
        "B": tokenizer.Tokenizer.SplitMode.B,
        "C": tokenizer.Tokenizer.SplitMode.C,
    }[mode]


def _split_for_sudachi(text: str, max_bytes: int = _SUDACHI_MAX_BYTES) -> list[str]:
    if len(text.encode("utf-8")) <= max_bytes:
        return [text]
    parts: list[str] = []
    buffer = ""
    for piece in re.split(r"(?<=[。．!?\n])", text):
        if len((buffer + piece).encode("utf-8")) > max_bytes:
            if buffer:
                parts.append(buffer)
                buffer = ""
            while len(piece.encode("utf-8")) > max_bytes:
                cut = _preferred_cut(piece, _fit_chars(piece, max_bytes))
                parts.append(piece[:cut])
                piece = piece[cut:]
        buffer += piece
    if buffer:
        parts.append(buffer)
    return parts


def _fit_chars(text: str, max_bytes: int) -> int:
    lo = 1
    hi = len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(text[:mid].encode("utf-8")) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _preferred_cut(text: str, limit: int) -> int:
    for index in range(limit, max(limit - 200, 1), -1):
        previous = text[index - 1]
        next_char = text[index]
        if previous in _SAFE_BOUND or next_char in _SAFE_BOUND:
            return index
        if _script_class(previous) != _script_class(next_char):
            return index
    return limit


def _script_class(char: str) -> int:
    codepoint = ord(char)
    if 0x3041 <= codepoint <= 0x309F:
        return 1
    if 0x30A0 <= codepoint <= 0x30FF or codepoint == 0x31F0:
        return 2
    if 0x4E00 <= codepoint <= 0x9FFF or codepoint == 0x3005:
        return 3
    if char.isascii() and char.isalnum():
        return 4
    return 0


def _package_version(name: str) -> str:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "not-installed"
