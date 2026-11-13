"""同じ生成の通信・構造・回答単位の復旧で、やり直しの枠を共有する。"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class LlmRetryBudget:
    used: int = 0
    max_retries: int = 1
    group_id: str = field(default_factory=lambda: uuid4().hex)

    @property
    def available(self) -> bool:
        return self.used < self.max_retries

    def take(self) -> bool:
        if not self.available:
            return False
        self.used += 1
        return True


retry_budget_context: ContextVar[LlmRetryBudget | None] = ContextVar("rag_engine_llm_retry_budget", default=None)


@contextmanager
def llm_retry_scope(max_retries: int) -> Iterator[LlmRetryBudget]:
    budget = retry_budget_context.get() or LlmRetryBudget(max_retries=max_retries)
    token = retry_budget_context.set(budget)
    try:
        yield budget
    finally:
        retry_budget_context.reset(token)
