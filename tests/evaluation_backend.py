"""Local ledger replacement, distributed only to isolated test copies."""

import json

from bookkeeping_core.domain import WriteOutcome


class IsolatedLedger:
    def __init__(self, runtime, session_id):
        if not runtime.isolated:
            raise RuntimeError("Test ledger requires an evaluation-only bundle")
        self.runtime, self.session_id = runtime, session_id
        runtime.db.execute("""CREATE TABLE IF NOT EXISTS isolated_calls (
            id INTEGER PRIMARY KEY, operation TEXT, session_id TEXT, payload TEXT, status TEXT)""")
        runtime.db.commit()

    async def check_schema(self):
        return None

    async def query_history(self, keyword, limit=20):
        with self.runtime.db:
            self.runtime.db.execute(
                "INSERT INTO isolated_calls(operation,session_id,payload,status) VALUES (?,?,?,?)",
                (
                    "query",
                    self.session_id,
                    json.dumps({"keyword": keyword, "limit": limit}),
                    "read",
                ),
            )
        if self.session_id.startswith("history-failure"):
            return {"ok": False, "reason": "历史查询失败，请按已有知识判断"}
        return {
            "ok": True,
            "records": [],
            "has_more": False,
            "scope": "仅隔离账本样本，不包含真实历史",
        }

    async def create(self, expense):
        status = "success"
        if self.session_id.startswith("faults"):
            if "咖啡" in expense.description:
                status = "uncertain"
            elif "地铁" in expense.description:
                status = "failed"
        with self.runtime.db:
            cursor = self.runtime.db.execute(
                "INSERT INTO isolated_calls(operation,session_id,payload,status) VALUES (?,?,?,?)",
                ("create", self.session_id, expense.model_dump_json(), status),
            )
        reason = {
            "success": "",
            "uncertain": "模拟提交后超时，请核对账本，不会自动重试",
            "failed": "模拟飞书明确拒绝，未入账",
        }[status]
        return WriteOutcome(
            status=status,
            record_id=f"recIsolated{cursor.lastrowid:06d}" if status == "success" else None,
            reason=reason,
        )
