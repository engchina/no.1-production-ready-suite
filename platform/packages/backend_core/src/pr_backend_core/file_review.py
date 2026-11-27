"""質問ごとの資料選択。検索と回答の生成、保存先と閲覧権限は呼び出し元が提供する。"""

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ReviewSource(BaseModel):
    title: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    answer: str = ""
    trace_id: str = ""
    notice: str | None = None

    def documents(self) -> dict[str, str]:
        return {
            str(item["document_id"]): str(item.get("file_name") or item["document_id"])
            for item in self.citations
            if item.get("document_id")
        }

    def restrict(self, permitted: set[str]) -> "ReviewSource":
        hits = [hit for hit in self.citations if hit.get("document_id") in permitted]
        return self.model_copy(
            update={
                "citations": hits,
                "answer": self.answer if len(hits) == len(self.citations) else "",
            }
        )


class ReviewVersion(BaseModel):
    id: str
    answer: str
    notice: str | None = None
    sources: dict[str, ReviewSource]
    rating: Literal["helpful", "not_helpful"] | None = None


class DiscoveryStep(BaseModel):
    """直前に表示していた候補と、その検索だけが変更した選択を保持する。"""

    previous_source: ReviewSource | None = None
    excluded: set[str] = Field(default_factory=set)
    discovered: set[str] = Field(default_factory=set)
    previous_kept: list[str] = Field(default_factory=list)
    removed_from_other_tasks: dict[str, list[str]] = Field(default_factory=dict)


class FileReview(BaseModel):
    revision: int = 0
    versions: list[ReviewVersion]
    kept: dict[str, list[str]] = Field(default_factory=dict)
    excluded: dict[str, str] = Field(default_factory=dict)
    seen: set[str] = Field(default_factory=set)
    pending: dict[str, ReviewSource] = Field(default_factory=dict)

    method_tasks: set[str] = Field(default_factory=set)
    previous_discovery: dict[str, DiscoveryStep] = Field(default_factory=dict)

    def visible(self, permitted: set[str]) -> "FileReview":
        # 操作を元に戻す内部記録を HTTP 応答に出さない。
        state = self.model_copy(update={"previous_discovery": {}}, deep=True)
        state.excluded = {k: v for k, v in state.excluded.items() if k in permitted}
        state.seen &= permitted
        state.kept = {k: [x for x in ids if x in permitted] for k, ids in state.kept.items()}
        for version in state.versions:
            if any(source.documents().keys() - permitted for source in version.sources.values()):
                version.answer = ""
            version.sources = {k: s.restrict(permitted) for k, s in version.sources.items()}
        state.pending = {k: s.restrict(permitted) for k, s in state.pending.items()}
        return state

    def document_ids(self) -> set[str]:
        return (
            self.seen
            | set(self.excluded)
            | {doc for source in self.pending.values() for doc in source.documents()}
            | {
                doc
                for step in self.previous_discovery.values()
                if step.previous_source
                for doc in step.previous_source.documents()
            }
            | {
                doc
                for version in self.versions
                for source in version.sources.values()
                for doc in source.documents()
            }
        )


class ReviewAction(BaseModel):
    revision: int = Field(ge=0)
    action: Literal["answer", "discover", "restore", "reset", "back"]
    task: str = Field(min_length=1, max_length=128)
    selected: list[str] = Field(default_factory=list, max_length=10000)


class StaleReview(ValueError):
    """別の操作が先に保存された。"""


Retrieve = Callable[[str, list[str], list[str], bool], Awaitable[ReviewSource]]
Compose = Callable[[dict[str, ReviewSource]], Awaitable[str]]


async def advance_review(
    previous: FileReview,
    command: ReviewAction,
    readable: Iterable[str],
    retrieve: Retrieve,
    compose: Compose,
) -> FileReview:
    """版と選択の更新をまとめて返す。失敗・競合時には保存前の状態を使える。"""
    if command.revision != previous.revision:
        raise StaleReview("資料の選択が更新されました。開き直してください。")
    state = previous.model_copy(deep=True)
    latest = state.versions[-1]
    key = command.task
    if key not in latest.sources:
        raise ValueError("検索タスクが見つかりません。")
    state.revision += 1
    allowed = set(readable)
    selected = set(command.selected)
    if command.action == "back":
        if key not in state.pending or key in state.method_tasks:
            raise ValueError("候補の確認画面から戻ってください。")
        step = state.previous_discovery.pop(key, None)
        state.pending.pop(key)
        if step:
            for doc in step.excluded:
                state.excluded.pop(doc, None)
            state.kept[key] = [
                doc for doc in step.previous_kept if doc in allowed and doc not in state.excluded
            ]
            for task, removed in step.removed_from_other_tasks.items():
                other_kept = state.kept.setdefault(task, [])
                other_kept.extend(
                    doc
                    for doc in removed
                    if doc not in other_kept and doc in allowed and doc not in state.excluded
                )
            if step.previous_source is not None:
                state.pending[key] = step.previous_source
                state.method_tasks.add(key)
            used_elsewhere = {
                doc for source in state.pending.values() for doc in source.documents()
            } | set(state.excluded)
            state.seen -= step.discovered - used_elsewhere
        # 旧形式には操作前の差分がないため、保存済みの除外を維持して回答版の資料へ戻す。
        return state
    if command.action == "restore":
        if not selected <= state.excluded.keys() & allowed:
            raise ValueError("除外中の資料を選んでください。")
        for doc in selected:
            del state.excluded[doc]
        state.seen -= selected
        for step in state.previous_discovery.values():
            step.excluded -= selected
        return state
    if command.action == "reset":
        state.excluded.clear()
        state.kept.clear()
        state.seen.clear()
        state.pending.clear()
        state.previous_discovery.clear()
        state.method_tasks = set(latest.sources)
        for task in latest.sources:
            found = await retrieve(task, [], [], False)
            state.pending[task] = found
            state.seen.update(found.documents())
        return state
    current = state.pending.get(key, latest.sources[key]).restrict(allowed - state.excluded.keys())
    shown = current.documents()
    if not selected <= shown.keys():
        raise ValueError("表示中の資料から選んでください。")
    rejected = {doc: name for doc, name in shown.items() if doc not in selected}
    for other, checkpoint in state.previous_discovery.items():
        if other != key:
            checkpoint.removed_from_other_tasks.pop(key, None)
            checkpoint.excluded.difference_update(rejected)
    if command.action == "discover":
        state.previous_discovery[key] = DiscoveryStep(
            previous_source=state.pending.get(key),
            excluded=set(rejected),
            previous_kept=state.kept.get(key, []),
            removed_from_other_tasks={
                task: [doc for doc in ids if doc in rejected]
                for task, ids in state.kept.items()
                if task != key
            },
        )
    state.method_tasks.discard(key)
    state.excluded.update(rejected)
    state.seen.update(shown)
    for task, ids in state.kept.items():
        state.kept[task] = [doc for doc in ids if doc not in state.excluded]
    state.kept[key] = [doc for doc in shown if doc in selected]
    if command.action == "discover":
        skip = state.seen | state.excluded.keys()
        found = await retrieve(key, [], sorted(skip), False)
        fresh = found.restrict(set(found.documents()) - skip)
        retained = current.restrict(selected)
        state.pending[key] = fresh.model_copy(
            update={"citations": retained.citations + fresh.citations, "answer": ""}
        )
        state.previous_discovery[key].discovered = set(fresh.documents())
        state.seen.update(fresh.documents())
        return state
    if not selected:
        raise ValueError("回答に使う資料を選んでください。")
    sources: dict[str, ReviewSource] = {}
    for task, source in latest.sources.items():
        if task == key:
            ids = state.kept[key]
        elif task in state.pending:
            # 別のタスクで確認待ちの新候補は明示的に選ぶまで回答に渡さない。
            ids = sorted(set(state.kept.get(task, [])) & allowed - state.excluded.keys())
        else:
            usable = source.documents().keys() & allowed - state.excluded.keys()
            if usable == source.documents().keys():
                sources[task] = source
                continue
            ids = sorted(usable)
        if ids:
            result = await retrieve(task, ids, sorted(state.excluded), True)
            if set(result.documents()) - set(ids):
                raise ValueError("選んだ資料の外にある根拠が返されました。")
            if not result.citations and task == key:
                raise ValueError("選んだ資料で根拠が見つかりませんでした。")
            sources[task] = result
        else:
            sources[task] = ReviewSource(title=source.title)
    body = await compose(sources)
    if not body.strip():
        raise ValueError("再回答を生成できませんでした。")
    target = sources[key]
    state.versions.append(
        ReviewVersion(
            id=target.trace_id if len(sources) == 1 and target.trace_id else uuid4().hex,
            answer=body,
            notice=target.notice,
            sources=sources,
        )
    )
    state.pending.pop(key, None)
    state.previous_discovery.pop(key, None)
    return state
