"""実行時 glossary/rules を読み込み、質問と prompt へ反映する。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


RUNTIME_KNOWLEDGE_SCHEMA_VERSION = 1
RUNTIME_KNOWLEDGE_FILE_NAME = "runtime_knowledge.json"
RUNTIME_KNOWLEDGE_ACTIVE_STATUSES = {"", "approved", "stale_review_needed"}
MAX_TERMS = 1000
MAX_RULES = 1000
MAX_ALIASES_PER_TERM = 32
MAX_TRIGGERS_PER_RULE = 32
MAX_MATCHED_TERMS = 8
MAX_MATCHED_RULES = 6
MAX_TEXT_CHARS = 800


@dataclass(frozen=True)
class RuntimeTerm:
    """実行時 glossary の 1 用語と別名・説明・review 状態を保持します。"""
    term: str
    aliases: tuple[str, ...] = ()
    description: str = ""
    source: str = ""
    tags: tuple[str, ...] = ()
    status: str = ""
    updated_at: str = ""
    reviewed_at: str = ""
    next_review_at: str = ""

    @property
    def active(self) -> bool:
        """検索や prompt へ反映する有効条件かどうかを返します。"""
        return self.status in RUNTIME_KNOWLEDGE_ACTIVE_STATUSES

    def labels(self) -> tuple[str, ...]:
        """照合に使う代表ラベルと別名を順序付きで返します。"""
        return _ordered_unique((self.term, *self.aliases))

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "term": self.term,
            "aliases": list(self.aliases),
            "description": self.description,
            "source": self.source,
            "tags": list(self.tags),
            "status": self.status,
            "updated_at": self.updated_at,
            "reviewed_at": self.reviewed_at,
            "next_review_at": self.next_review_at,
        }


@dataclass(frozen=True)
class RuntimeRule:
    """質問に一致したとき prompt へ追加する実行時ルールを保持します。"""
    rule_id: str
    title: str
    triggers: tuple[str, ...] = ()
    content: str = ""
    source: str = ""
    tags: tuple[str, ...] = ()
    status: str = ""
    updated_at: str = ""
    reviewed_at: str = ""
    next_review_at: str = ""

    @property
    def active(self) -> bool:
        """検索や prompt へ反映する有効条件かどうかを返します。"""
        return self.status in RUNTIME_KNOWLEDGE_ACTIVE_STATUSES

    def labels(self) -> tuple[str, ...]:
        """照合に使う代表ラベルと別名を順序付きで返します。"""
        return _ordered_unique((self.title, *self.triggers))

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "id": self.rule_id,
            "title": self.title,
            "triggers": list(self.triggers),
            "content": self.content,
            "source": self.source,
            "tags": list(self.tags),
            "status": self.status,
            "updated_at": self.updated_at,
            "reviewed_at": self.reviewed_at,
            "next_review_at": self.next_review_at,
        }


@dataclass(frozen=True)
class RuntimeKnowledge:
    """runtime knowledge JSON から読み込んだ用語・ルールと読込状態を保持します。"""
    path: Path
    terms: tuple[RuntimeTerm, ...] = ()
    rules: tuple[RuntimeRule, ...] = ()
    loaded: bool = False
    error: str = ""


@dataclass(frozen=True)
class RuntimeKnowledgeContext:
    """一致項目・拡張後 query と読込件数を保持する。件数は一致上限を適用する前の登録数。"""
    original_question: str
    expanded_question: str
    path: Path
    matched_terms: tuple[RuntimeTerm, ...] = ()
    matched_rules: tuple[RuntimeRule, ...] = ()
    loaded: bool = False
    error: str = ""
    registered_count: int = 0
    active_count: int = 0
    expansion_decisions: tuple[dict[str, Any], ...] = ()

    @property
    def has_matches(self) -> bool:
        """質問に一致した用語またはルールがあるかを返します。"""
        return bool(self.matched_terms or self.matched_rules)

    @property
    def is_active(self) -> bool:
        """UI に表示すべき runtime knowledge 状態かを返します。"""
        return self.has_matches or bool(self.error)

    @property
    def query_source(self) -> str:
        """検索 query の生成元を UI 表示用ラベルで返します。"""
        if self.expanded_question != self.original_question:
            return "原質問 + 実行時用語・ルール"
        return "原質問のみ"

    def prompt_context(self) -> str:
        """LLM prompt に追加する補助 context 文字列を返します。"""
        if not self.has_matches:
            return ""
        lines = ["[Runtime Glossary / Rules]"]
        if self.matched_terms:
            lines.append("Glossary:")
            for term in self.matched_terms:
                label = term.term
                if term.aliases:
                    label += f" (aliases: {', '.join(term.aliases)})"
                detail = f": {term.description}" if term.description else ""
                source = f" [{term.source}]" if term.source else ""
                lines.append(f"- {label}{detail}{source}")
        if self.matched_rules:
            lines.append("Rules:")
            for rule in self.matched_rules:
                label = rule.title or rule.rule_id
                triggers = f" (triggers: {', '.join(rule.triggers)})" if rule.triggers else ""
                source = f" [{rule.source}]" if rule.source else ""
                lines.append(f"- {label}{triggers}: {rule.content}{source}")
        return "\n".join(lines)

    def to_payload(self) -> dict[str, Any]:
        """UI と trace 保存に使う JSON 互換 payload へ変換します。"""
        return {
            "path": str(self.path),
            "loaded": self.loaded,
            "error": self.error,
            "query_source": self.query_source,
            "expanded_question": self.expanded_question,
            "matched_terms": [term.to_payload() for term in self.matched_terms],
            "matched_rules": [rule.to_payload() for rule in self.matched_rules],
            "registered_count": self.registered_count,
            "active_count": self.active_count,
            "expansion_decisions": list(self.expansion_decisions),
        }


def runtime_knowledge_path(output_dir: str | Path, configured_path: str | Path | None = None) -> Path:
    """runtime knowledge JSON の実効保存パスを決定します。"""
    if configured_path:
        return Path(configured_path).expanduser()
    return Path(output_dir) / RUNTIME_KNOWLEDGE_FILE_NAME


def load_runtime_knowledge(
    output_dir: str | Path,
    configured_path: str | Path | None = None,
) -> RuntimeKnowledge:
    """runtime glossary/rules JSON を読み込み、壊れた内容は error として保持します。"""
    path = runtime_knowledge_path(output_dir, configured_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RuntimeKnowledge(path=path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return RuntimeKnowledge(path=path, loaded=False, error=_clean_text(exc, 240))

    try:
        terms = tuple(_parse_terms(payload)[:MAX_TERMS])
        rules = tuple(_parse_rules(payload)[:MAX_RULES])
    except Exception as exc:
        return RuntimeKnowledge(path=path, loaded=False, error=_clean_text(exc, 240))
    return RuntimeKnowledge(path=path, terms=terms, rules=rules, loaded=True)


def build_runtime_knowledge_context(
    question: str,
    output_dir: str | Path,
    configured_path: str | Path | None = None,
    *,
    term_limit: int = MAX_MATCHED_TERMS,
    rule_limit: int = MAX_MATCHED_RULES,
) -> RuntimeKnowledgeContext:
    """質問に一致した用語・ルールを検索 query と prompt context 用に抽出します。"""
    original_question = _normalize(question)
    knowledge = load_runtime_knowledge(output_dir, configured_path)
    if knowledge.error:
        return RuntimeKnowledgeContext(
            original_question=original_question,
            expanded_question=original_question,
            path=knowledge.path,
            loaded=knowledge.loaded,
            error=knowledge.error,
        )

    matched_terms = _matched_terms(original_question, knowledge.terms, term_limit)
    matched_rules = _matched_rules(original_question, knowledge.rules, rule_limit)
    expanded_question = _expanded_question(original_question, matched_terms, matched_rules)
    decisions = []
    for kind, items, selected in (("term", knowledge.terms, matched_terms), ("rule", knowledge.rules, matched_rules)):
        for item in items:
            if not item.active:
                continue
            matches = [label for label in item.labels() if _matches(original_question, label)]
            weak = _shared_token_count(original_question, item.description if kind == "term" else item.content)
            if matches or weak:
                decisions.append({"source": kind, "key": item.term if kind == "term" else item.rule_id,
                                  "matched_labels": matches, "accepted": item in selected,
                                  "reason": "explicit_label" if item in selected else "limit" if matches else "shared_words_only"})
    return RuntimeKnowledgeContext(
        original_question=original_question,
        expanded_question=expanded_question,
        path=knowledge.path,
        matched_terms=tuple(matched_terms),
        matched_rules=tuple(matched_rules),
        loaded=knowledge.loaded,
        registered_count=len(knowledge.terms) + len(knowledge.rules),
        active_count=sum(item.active for item in (*knowledge.terms, *knowledge.rules)),
        expansion_decisions=tuple(decisions),
    )


def runtime_knowledge_status(context: RuntimeKnowledgeContext) -> str:
    """管理画面と実行記録で未登録・無効・一致なし・読込失敗を区別する。"""
    if context.error:
        return f"読込エラー: {context.error}（補助情報なしで続行）"
    parts = []
    if context.matched_terms:
        parts.append("用語 " + " / ".join(term.term for term in context.matched_terms))
    if context.matched_rules:
        parts.append("ルール " + " / ".join(rule.title or rule.rule_id for rule in context.matched_rules))
    if parts:
        return "一致: " + "、".join(parts)
    if not context.loaded or not context.registered_count:
        return "未登録：管理 → 用語・ルール管理で登録できます"
    if not context.active_count:
        return "有効な登録なし：管理 → 用語・ルール管理で利用状態を確認してください"
    return "一致なし：登録済みの有効な用語・ルールに一致しませんでした"


def format_runtime_knowledge_context(context: RuntimeKnowledgeContext) -> list[str]:
    """runtime knowledge の読込状態と一致内容を UI 表示行へ整形します。"""
    lines = [f"パス: {context.path}"]
    if context.error:
        lines.append(f"エラー: {context.error}")
        return lines
    if not context.loaded:
        lines.append("読込状態: 未読込")
        return lines
    lines.append("読込状態: 読込済み")
    lines.append(f"検索質問ソース: {context.query_source}")
    if context.matched_terms:
        lines.append("用語: " + " / ".join(term.term for term in context.matched_terms))
    if context.matched_rules:
        lines.append("ルール: " + " / ".join(rule.title or rule.rule_id for rule in context.matched_rules))
    if not context.has_matches:
        lines.append("一致: なし")
    return lines


def runtime_retrieval_queries(
    original_question: str,
    expanded_question: str,
    retrieval_queries: Sequence[str],
) -> tuple[str, ...]:
    """runtime knowledge 拡張後の質問を retrieval query 列へ統合します。"""
    return tuple(_ordered_unique((original_question, expanded_question, *retrieval_queries)))


def _parse_terms(payload: Any) -> list[RuntimeTerm]:
    raw_terms = _items(payload, "terms", "glossary", "glossaries")
    terms: list[RuntimeTerm] = []
    for item in raw_terms:
        if isinstance(item, str):
            term = _clean_text(item)
            if term:
                terms.append(RuntimeTerm(term=term))
            continue
        if not isinstance(item, dict):
            continue
        term = _first_text(item, "term", "name", "canonical", "formal", "label")
        aliases = _text_list(
            item.get("aliases")
            or item.get("synonyms")
            or item.get("abbreviations")
            or item.get("abbrev")
            or item.get("alias")
        )[:MAX_ALIASES_PER_TERM]
        description = _first_text(item, "description", "definition", "body", "note")
        source = _first_text(item, "source", "document", "path")
        tags = _text_list(item.get("tags"))
        status = _first_text(item, "status", "lifecycle_status", max_chars=80)
        updated_at = _first_text(item, "updated_at", max_chars=80)
        reviewed_at = _first_text(item, "reviewed_at", max_chars=80)
        next_review_at = _first_text(item, "next_review_at", max_chars=80)
        if term:
            terms.append(
                RuntimeTerm(
                    term=term,
                    aliases=tuple(aliases),
                    description=description,
                    source=source,
                    tags=tuple(tags),
                    status=status,
                    updated_at=updated_at,
                    reviewed_at=reviewed_at,
                    next_review_at=next_review_at,
                )
            )
    return _dedupe_terms(terms)


def _parse_rules(payload: Any) -> list[RuntimeRule]:
    raw_rules = _items(payload, "rules", "rulebook", "rulebooks")
    rules: list[RuntimeRule] = []
    for index, item in enumerate(raw_rules, start=1):
        if isinstance(item, str):
            content = _clean_text(item, MAX_TEXT_CHARS)
            if content:
                rules.append(RuntimeRule(rule_id=f"rule-{index}", title=f"rule-{index}", content=content))
            continue
        if not isinstance(item, dict):
            continue
        rule_id = _first_text(item, "id", "rule_id", "key") or f"rule-{index}"
        title = _first_text(item, "title", "name", "label") or rule_id
        triggers = _text_list(
            item.get("triggers")
            or item.get("terms")
            or item.get("keywords")
            or item.get("aliases")
            or item.get("when")
        )[:MAX_TRIGGERS_PER_RULE]
        content = _first_text(item, "content", "body", "text", "rule", max_chars=MAX_TEXT_CHARS)
        source = _first_text(item, "source", "document", "path")
        tags = _text_list(item.get("tags"))
        status = _first_text(item, "status", "lifecycle_status", max_chars=80)
        updated_at = _first_text(item, "updated_at", max_chars=80)
        reviewed_at = _first_text(item, "reviewed_at", max_chars=80)
        next_review_at = _first_text(item, "next_review_at", max_chars=80)
        if content or title:
            rules.append(
                RuntimeRule(
                    rule_id=rule_id,
                    title=title,
                    triggers=tuple(triggers),
                    content=content,
                    source=source,
                    tags=tuple(tags),
                    status=status,
                    updated_at=updated_at,
                    reviewed_at=reviewed_at,
                    next_review_at=next_review_at,
                )
            )
    return _dedupe_rules(rules)


def _items(payload: Any, *keys: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _matched_terms(question: str, terms: Sequence[RuntimeTerm], limit: int) -> list[RuntimeTerm]:
    scored: list[tuple[int, int, RuntimeTerm]] = []
    for index, term in enumerate(terms):
        if not term.active:
            continue
        score = 0
        for label in term.labels():
            if _matches(question, label):
                score += 3 if label == term.term else 2
        if score and term.description and _shared_token_count(question, term.description):
            score += 1
        if score:
            scored.append((score, index, term))
    return [term for _, _, term in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(1, limit)]]


def _matched_rules(question: str, rules: Sequence[RuntimeRule], limit: int) -> list[RuntimeRule]:
    scored: list[tuple[int, int, RuntimeRule]] = []
    for index, rule in enumerate(rules):
        if not rule.active:
            continue
        score = 0
        for label in rule.labels():
            if _matches(question, label):
                score += 3
        if score and _shared_token_count(question, rule.content):
            score += 1
        if score:
            scored.append((score, index, rule))
    return [rule for _, _, rule in sorted(scored, key=lambda item: (-item[0], item[1]))[: max(1, limit)]]


def _expanded_question(
    question: str,
    matched_terms: Sequence[RuntimeTerm],
    matched_rules: Sequence[RuntimeRule],
) -> str:
    additions: list[str] = []
    for term in matched_terms:
        additions.extend(term.labels())
    for rule in matched_rules:
        # trigger は別名ではない。ある条件の一致だけで他条件や業務IDを検索へ流さない。
        additions.extend(label for label in rule.labels() if _matches(question, label))
        if any(_matches(rule.title, label) for label in rule.labels() if _matches(question, label)) and not re.search(r'[A-Za-z]{2,}[\w-]*\d', rule.title):
            additions.append(rule.title)
    additions = _ordered_unique(additions)
    if not additions:
        return question
    return f"{question}\n" + " ".join(additions)


def _shared_token_count(left: str, right: str) -> int:
    left_tokens = set(_tokens(left))
    if not left_tokens:
        return 0
    return len(left_tokens & set(_tokens(right)))


def _tokens(value: str) -> list[str]:
    return re.findall(r"[0-9a-zA-Z_]{2,}|[ぁ-んァ-ン一-龯々ー]{2,}", _normalize(value))


def _matches(text: str, label: str) -> bool:
    normalized_text = _key(text)
    normalized_label = _key(label)
    if not normalized_text or not normalized_label:
        return False
    if re.fullmatch(r"[0-9a-z_]+", normalized_label):
        pattern = rf"(?<![0-9a-z_]){re.escape(normalized_label)}(?![0-9a-z_])"
        return re.search(pattern, normalized_text) is not None
    return normalized_label in normalized_text


def _first_text(payload: dict[str, Any], *keys: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    for key in keys:
        value = _clean_text(payload.get(key), max_chars)
        if value:
            return value
    return ""


def _text_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = re.split(r"[,、/\n]", value)
    elif isinstance(value, Sequence):
        raw = value
    else:
        raw = []
    return _ordered_unique(_clean_text(item) for item in raw)


def _dedupe_terms(terms: Sequence[RuntimeTerm]) -> list[RuntimeTerm]:
    out: list[RuntimeTerm] = []
    seen: set[str] = set()
    for term in terms:
        key = _key(term.term)
        if not key or key in seen:
            continue
        out.append(term)
        seen.add(key)
    return out


def _dedupe_rules(rules: Sequence[RuntimeRule]) -> list[RuntimeRule]:
    out: list[RuntimeRule] = []
    seen: set[str] = set()
    for rule in rules:
        key = _key(rule.rule_id or rule.title)
        if not key or key in seen:
            continue
        out.append(rule)
        seen.add(key)
    return out


def _ordered_unique(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = _key(text)
        if not text or key in seen:
            continue
        out.append(text)
        seen.add(key)
    return out


def _clean_text(value: Any, max_chars: int = 160) -> str:
    text = _normalize(str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
