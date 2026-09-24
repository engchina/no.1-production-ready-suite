"""質問から操作すべき画面を、知識ベースの画面目録で選び、その画面の根拠を検索候補に加える (#1108)。

検索は質問文と拡張文の類似度・語句一致で候補を集めるので、症状（「表示されない」「印字されない」）を述べる
質問と、それを解決する設定画面の説明は語が重ならず候補に入らないことがある。画面目録（文書ごとの番号付きの
画面見出し）を質問と一緒に LLM へ渡して画面を選ばせ、その画面の child chunk を候補に加える。

- 目録は知識ベースの chunk の `section_path` から作り、特定の文書の語を持たない。
- 選択結果は目録に実在する（文書、見出し）だけを採る。
- 候補は加えるだけで減らさない。順位は rerank が候補全件で決める。
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Callable, Sequence

from pydantic import BaseModel, Field

# 番号付きの画面見出し（「（２）帳票印字設定」「（Ｄ）請求処理」「(3)…」）。目録に入れる見出しの形。
_NUMBERED_HEADING = re.compile(r"^[（(][0-9０-９Ａ-ＺA-Zａ-ｚ]{1,3}[）)]")
_HEADING_MIN_CHARS = 3
_HEADING_MAX_CHARS = 40
# 1 回の選択で返させる画面の数と、画面ごとに候補へ加える child chunk の上限。
MAX_LINKED_SCREENS = 3
MAX_CHILDREN_PER_SCREEN = 10

LINK_SYSTEM_PROMPT = (
    "あなたは業務システムの問い合わせを、操作説明書の画面へ案内する担当です。JSON だけを返す。\n"
    "画面目録（文書ごとの画面見出し）から、問い合わせを解決するために利用者が操作すべき画面を、可能性の高い順に最大 3 つ選ぶ。\n"
    "症状（表示されない・印字されない・エラーになる）の問い合わせでは、症状が出る画面だけでなく、その動作を決める設定画面も候補にする。\n"
    "目録にある文書名と見出しをそのまま写す。当てはまる画面が無ければ空にする。"
)


class LinkedScreen(BaseModel):
    document: str = Field(description="画面目録の文書名をそのまま写す。")
    screen: str = Field(description="画面目録の見出しをそのまま写す。")


class ScreenLinks(BaseModel):
    candidates: list[LinkedScreen] = Field(default_factory=list, max_length=MAX_LINKED_SCREENS)


def _key(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def _section_path(chunk: Any) -> list[str]:
    metadata = getattr(chunk, "metadata", None) or {}
    path = metadata.get("section_path") if isinstance(metadata, dict) else None
    return [str(h).strip() for h in path] if isinstance(path, list) else []


def build_screen_catalog(chunks: Sequence[Any]) -> dict[str, list[str]]:
    """文書名 → 番号付きの画面見出し（出現の多い順）。知識ベースの chunk の `section_path` から作る。"""
    counts: dict[str, dict[str, int]] = {}
    for chunk in chunks:
        source = str(getattr(chunk, "source_file_name", "") or "")
        if not source:
            continue
        for heading in _section_path(chunk):
            if _NUMBERED_HEADING.match(heading) and _HEADING_MIN_CHARS <= len(heading) <= _HEADING_MAX_CHARS:
                bucket = counts.setdefault(source, {})
                bucket[heading] = bucket.get(heading, 0) + 1
    return {source: sorted(bucket, key=lambda h: -bucket[h]) for source, bucket in counts.items()}


def validated_links(links: ScreenLinks, catalog: dict[str, list[str]]) -> list[tuple[str, str]]:
    """LLM の選択のうち、目録に実在する（文書、見出し）だけを目録の表記で返す。空白・全角半角の違いは許す。"""
    documents = {_key(source): source for source in catalog}
    chosen: list[tuple[str, str]] = []
    for link in links.candidates:
        # 目録は文書名を「[文書名]」の形で渡すので、モデルが括弧ごと写すことがある。
        source = documents.get(_key(link.document).strip("[]"))
        if source is None:
            continue
        headings = {_key(h): h for h in catalog[source]}
        heading = headings.get(_key(link.screen))
        if heading is not None and (source, heading) not in chosen:
            chosen.append((source, heading))
    return chosen[:MAX_LINKED_SCREENS]


def link_screens(question: str, catalog: dict[str, list[str]], parse_text: Callable[..., ScreenLinks],
                 settings: Any) -> list[tuple[str, str]]:
    """質問から操作すべき画面を目録で選ぶ。LLM の呼び出しに失敗したら空（検索は従来どおり続ける）。"""
    if not catalog:
        return []
    text = "\n".join(f"[{source}]\n" + " / ".join(headings) for source, headings in catalog.items())
    prompt = json.dumps({"question": question, "catalog": text}, ensure_ascii=False)
    try:
        return validated_links(parse_text(LINK_SYSTEM_PROMPT, prompt, settings, ScreenLinks), catalog)
    except Exception:
        return []


def screen_candidates(chunks: Sequence[Any], links: Sequence[tuple[str, str]], existing: set[str],
                      *, per_screen: int = MAX_CHILDREN_PER_SCREEN) -> list[Any]:
    """選んだ画面ごとに、同じ文書で `section_path` にその見出しを含む child chunk を読み順に返す。

    既に候補にある chunk（`existing` の chunk_uid）は除く。候補を加えるだけで、既存の順位は変えない。
    """
    added: list[Any] = []
    seen = set(existing)
    for source, heading in links:
        matched = [c for c in chunks
                   if getattr(c, "chunk_level", "") == "child" and getattr(c, "source_file_name", "") == source
                   and heading in _section_path(c) and getattr(c, "chunk_uid", "") not in seen]
        matched.sort(key=lambda c: (int(getattr(c, "page_start", 0) or 0), int(getattr(c, "chunk_seq", 0) or 0)))
        for chunk in matched[:per_screen]:
            seen.add(chunk.chunk_uid)
            added.append(chunk)
    return added


def reserve_linked_screens(records: Sequence[Any], links: Sequence[tuple[str, str]], rank: int) -> list[Any]:
    """選んだ画面ごとの最上位の child を、順位 rank 位（2 つ目の画面は rank+1 位…）より下に置かない。

    rerank は質問と本文の類似度で並べるため、質問の語と重ならない設定画面の説明は、候補に加えても context に
    入る順位まで上がらないことがある。比較実験用に、選んだ画面の根拠へ context の枠を確保する。順位を上げるのは
    各画面の 1 件だけで、ほかの候補の相対順は変えない。rank は 1 始まり。
    """
    ordered = list(records)
    for offset, (source, heading) in enumerate(links):
        target = max(0, rank - 1 + offset)
        index = next((i for i, record in enumerate(ordered)
                      if getattr(record, "source", "") == source and heading in _section_path(record)), None)
        if index is not None and index > target:
            ordered.insert(target, ordered.pop(index))
    return ordered
