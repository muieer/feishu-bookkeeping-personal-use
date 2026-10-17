from typing import Any, Protocol

from .domain import Expense, WriteOutcome


class Ledger(Protocol):
    async def query_history(self, keyword: str, limit: int) -> dict[str, Any]: ...

    async def create(self, expense: Expense) -> WriteOutcome: ...
