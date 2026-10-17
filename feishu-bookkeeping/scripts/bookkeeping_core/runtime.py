"""Persistent tool runtime, without a model client or agent loop."""

import fcntl
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, ValidationError

from .activation import can_activate
from .audit import Audit
from .config import default_state_dir, load_settings
from .domain import (
    Decision,
    ExpenseProposal,
    SkippedExpense,
    StrictModel,
    TaskReport,
    locate_source,
)
from .journal import Journal
from .knowledge import Knowledge
from .tools import RunDeps, add_expenses, query_history, validate_decision


class Envelope(StrictModel):
    tool: str
    host: str = Field(default="", max_length=64)
    session_id: str = Field(default="", max_length=200)
    args: dict = Field(default_factory=dict)


class Empty(StrictModel):
    pass


class Activate(StrictModel):
    user_instruction: str = Field(min_length=1, max_length=100000)


class Start(StrictModel):
    message: str = Field(min_length=1, max_length=100000)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)


class Task(StrictModel):
    task_id: str = Field(min_length=1, max_length=100)


class Query(Task):
    keyword: str = Field(max_length=100)
    limit: int = Field(default=20, ge=1, le=50, strict=True)


class Add(Task):
    expenses: list[dict] = Field(min_length=1, max_length=100)


class Finish(Task):
    decision: Decision


class Doctor(StrictModel):
    live: bool = False


INPUTS = {
    "activate_session": Activate,
    "deactivate_session": Empty,
    "session_status": Empty,
    "knowledge": Empty,
    "start_task": Start,
    "query_history": Query,
    "add_expenses": Add,
    "finish_task": Finish,
    "show_result": Task,
    "doctor": Doctor,
}


@contextmanager
def process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("另一个记账调用正在执行，请稍后用原 task_id 继续") from error
        yield
    finally:
        os.close(fd)


def render_report(report: TaskReport, isolated: bool) -> str:
    lines = ["【隔离验收】本地模拟结果，未写入飞书。"] if isolated else []
    for status, heading in [
        ("success", "已记账消费记录"),
        ("failed", "未入账消费记录"),
        ("uncertain", "待核对消费记录"),
    ]:
        entries = [e for e in report.entries if e.outcome.status == status]
        if entries:
            lines.append(heading + "：")
            for entry in entries:
                e = entry.expense
                category = "｜".join(x for x in e.category_path if x is not None)
                line = f"{e.expense_date:%Y/%m/%d}｜{category}｜{e.description}｜{e.amount:f} 元"
                if entry.outcome.reason:
                    line += "；" + entry.outcome.reason
                lines.append(line)
    if report.skipped:
        lines.append("以下消费未提交：")
        lines.extend(f"「{e.source_text}」：{e.reason}" for e in report.skipped)
    if report.no_consumption:
        lines.append("此次未包含消费记录。")
    if report.notice:
        lines.append(report.notice)
    return "\n".join(lines)


class Runtime:
    def __init__(self, bundle: Path):
        self.bundle = bundle.resolve()
        self.settings = load_settings(self.bundle)
        self.isolated = (self.bundle / ".evaluation-only").is_file()
        self.state_dir = (
            (self.bundle / ".runtime")
            if self.isolated
            else (default_state_dir() / self.settings.namespace[:16])
        )
        self.knowledge = Knowledge(self.bundle / "private", self.settings.bitable_table_id)

    def open(self):
        self.audit = Audit(
            self.state_dir / "events.jsonl", [self.settings.bitable_base_token.get_secret_value()]
        )
        self.journal = Journal(self.state_dir / "journal.sqlite3", self.settings.namespace)
        self.db = self.journal.connection
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                owner TEXT PRIMARY KEY, active INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS requests (
                owner TEXT NOT NULL, request_id TEXT NOT NULL, task_id TEXT NOT NULL UNIQUE,
                PRIMARY KEY(owner, request_id));
            CREATE TABLE IF NOT EXISTS rejected (
                task_id TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                item TEXT NOT NULL, PRIMARY KEY(task_id, start, end));
        """)
        # The lock proves there is no still-running CLI. Never resubmit an orphan reservation.
        with self.db:
            self.db.execute(
                "UPDATE writes SET state='uncertain', reason=? WHERE state='submitted'",
                ("上次提交中断，结果待核对，不会自动重试",),
            )

    def owner(self, request: Envelope) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", request.host):
            raise ValueError("需要当前宿主的 host 标识")
        if not request.session_id.strip() or request.session_id.lower() in {
            "default",
            "global",
            "shared",
            "unknown",
        }:
            raise ValueError("需要当前会话的独立 session_id，不能使用全局默认值")
        return hashlib.sha256((request.host + "\0" + request.session_id).encode()).hexdigest()

    def active(self, owner: str) -> bool:
        row = self.db.execute("SELECT active FROM sessions WHERE owner=?", (owner,)).fetchone()
        return bool(row and row[0])

    def require_active(self, owner: str):
        if not self.active(owner):
            raise ValueError(
                "当前会话未唤醒；仅接受指定口令或明确指定 feishu-bookkeeping Skill 记账"
            )

    def task(self, owner: str, task_id: str, *, writable: bool = False):
        message = self.journal.message(task_id)
        if message.chat_id != owner:
            raise ValueError("task_id 不属于当前宿主会话")
        if writable:
            self.require_active(owner)
            if message.state == "done":
                raise ValueError("任务已结束，请读取已有结果，不得追加提交")
        return message

    def ledger(self, request: Envelope, message=None):
        if self.isolated:
            # This import lives only in isolated acceptance bundles, never the release bundle.
            from evaluation_backend import IsolatedLedger

            return IsolatedLedger(self, request.session_id)
        from .cli import LarkCliLedger

        return LarkCliLedger(self.settings, self.audit)

    async def deps(self, owner: str, request: Envelope, task_id: str):
        message = self.task(owner, task_id, writable=True)

        async def progress(_text):
            pass

        deps = RunDeps(
            task_id,
            message.text,
            message.execution_date,
            self.journal,
            self.ledger(request, message),
            self.knowledge,
            self.audit,
            progress,
        )
        deps.rejected = {
            (row[0], row[1]): SkippedExpense.model_validate_json(row[2])
            for row in self.db.execute(
                "SELECT start,end,item FROM rejected WHERE task_id=?", (task_id,)
            )
        }
        return deps

    def save_rejected(self, deps):
        with self.db:
            self.db.execute("DELETE FROM rejected WHERE task_id=?", (deps.message_id,))
            self.db.executemany(
                "INSERT INTO rejected VALUES (?,?,?,?)",
                [
                    (deps.message_id, *span, item.model_dump_json())
                    for span, item in deps.rejected.items()
                ],
            )

    def result(self, task_id: str):
        report = self.journal.report(task_id)
        if report is None:
            report = TaskReport(
                entries=self.journal.entries(task_id),
                notice="任务尚未结束，以下为已保存的提交结果。",
            )
        return {
            "task_id": task_id,
            "report": report.model_dump(mode="json"),
            "text": render_report(report, self.isolated),
        }

    async def dispatch(self, request: Envelope, args):
        tool = request.tool
        if tool == "doctor":
            if args.live:
                await self.ledger(request).check_schema()
            return {
                "knowledge_valid": True,
                "category_paths": len(self.knowledge.paths),
                "rules": len(self.knowledge.rule_map),
                "rules_status": self.knowledge.rules.get("status"),
                "live_schema_checked": args.live,
                "session_activated": False,
            }
        owner = self.owner(request)
        if tool == "session_status":
            return {"active": self.active(owner)}
        if tool == "activate_session":
            if not can_activate(args.user_instruction):
                raise ValueError("不是有效唤醒指令；引用、讨论、否定或相似口令不能开启记账")
            with self.db:
                self.db.execute(
                    "INSERT INTO sessions VALUES (?,1) ON CONFLICT(owner) DO UPDATE SET active=1",
                    (owner,),
                )
            return {
                "active": True,
                "text": "记账小助手已开启，本会话可直接发送消费；明确停止记账后关闭。",
            }
        if tool == "deactivate_session":
            with self.db:
                self.db.execute("UPDATE sessions SET active=0 WHERE owner=?", (owner,))
                tasks = self.db.execute(
                    "SELECT message_id FROM messages WHERE chat_id=? AND state!='done'", (owner,)
                ).fetchall()
                for row in tasks:
                    self.journal.finish(
                        row[0],
                        TaskReport(
                            entries=self.journal.entries(row[0]),
                            notice="用户已结束记账，未提交的消费不再处理。",
                        ),
                    )
            return {"active": False, "text": "本会话记账已关闭。"}
        if tool == "show_result":
            self.task(owner, args.task_id)
            return self.result(args.task_id)
        self.require_active(owner)
        if tool == "knowledge":
            return json.loads(self.knowledge.context)
        if tool == "start_task":
            request_id = args.request_id or str(uuid.uuid4())
            old = self.db.execute(
                "SELECT task_id FROM requests WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if old:
                message = self.task(owner, old[0])
                if message.text != args.message:
                    raise ValueError("同一 request_id 的原消息不能改变")
                return {
                    "task_id": old[0],
                    "execution_date": message.execution_date.isoformat(),
                    "already_started": True,
                    "state": message.state,
                }
            await self.ledger(request).check_schema()
            task_id = str(uuid.uuid4())
            today = datetime.now(ZoneInfo(self.settings.timezone)).date()
            if self.isolated:
                from datetime import date

                today = date.fromisoformat((self.bundle / ".evaluation-only").read_text().strip())
            with self.db:
                self.journal.accept(task_id, owner, args.message, today)
                self.journal.start(task_id, today)
                self.db.execute("INSERT INTO requests VALUES (?,?,?)", (owner, request_id, task_id))
            return {
                "task_id": task_id,
                "request_id": request_id,
                "execution_date": today.isoformat(),
                "state": "running",
            }
        deps = await self.deps(owner, request, args.task_id)
        if tool == "query_history":
            return await query_history(deps, args.keyword, args.limit)
        if tool == "add_expenses":
            results = []
            for raw in args.expenses:
                try:
                    proposal = ExpenseProposal.model_validate(raw)
                except ValidationError:
                    result = {
                        "status": "rejected",
                        "submitted": False,
                        "reason": "该笔参数含未知字段或格式错误，请修正尚未提交的消费",
                    }
                    source = raw.get("source_text")
                    if isinstance(source, str):
                        result["source_text"] = source
                        try:
                            span = locate_source(
                                deps.message_text, source, raw.get("source_occurrence", 1)
                            )
                            deps.rejected[span] = SkippedExpense(
                                source_text=source,
                                source_occurrence=raw.get("source_occurrence", 1),
                                reason=result["reason"],
                            )
                        except (ValueError, TypeError):
                            pass
                    results.append(result)
                else:
                    results.extend(await add_expenses(deps, [proposal]))
                self.save_rejected(deps)
            return results
        if tool == "finish_task":
            decision = validate_decision(deps, args.decision)
            skipped = list(decision.skipped)
            spans = {
                locate_source(deps.message_text, x.source_text, x.source_occurrence)
                for x in skipped
            }
            skipped.extend(
                item
                for span, item in deps.rejected.items()
                if span not in spans and not self.journal.overlaps(args.task_id, span)
            )
            report = TaskReport(
                entries=self.journal.entries(args.task_id),
                skipped=skipped,
                no_consumption=decision.no_consumption and not skipped,
            )
            self.journal.finish(args.task_id, report)
            return self.result(args.task_id)
        raise ValueError("不允许的工具")

    async def call(self, raw: dict) -> dict:
        request = Envelope.model_validate(raw)
        if request.tool not in INPUTS:
            raise ValueError("不允许的工具；账本仅支持 query_history 和 add_expenses")
        args = INPUTS[request.tool].model_validate(request.args)
        if request.tool == "doctor" and not args.live:
            return {
                "ok": True,
                "mode": "isolated" if self.isolated else "live",
                "data": {
                    "knowledge_valid": True,
                    "category_paths": len(self.knowledge.paths),
                    "rules": len(self.knowledge.rule_map),
                    "rules_status": self.knowledge.rules.get("status"),
                    "live_schema_checked": False,
                    "session_activated": False,
                },
            }
        with process_lock(self.state_dir / "process.lock"):
            self.open()
            try:
                self.audit.event(
                    "tool_request",
                    tool=request.tool,
                    host=request.host,
                    session_id=request.session_id,
                    args=request.args,
                )
                data = await self.dispatch(request, args)
                self.audit.event("tool_response", tool=request.tool, data=data)
                return {"ok": True, "mode": "isolated" if self.isolated else "live", "data": data}
            finally:
                self.journal.close()
                self.audit.close()
