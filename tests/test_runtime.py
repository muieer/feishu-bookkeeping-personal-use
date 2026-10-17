import asyncio
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu-bookkeeping/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bookkeeping_core.activation import can_activate
from bookkeeping_core.cli import LarkCliLedger, ProcessResult, classify_write
from bookkeeping_core.config import Settings
from bookkeeping_core.domain import calculate_grounded, resolve_date
from bookkeeping_core.runtime import Runtime, process_lock


def proposal(source="今天买咖啡18元", **changes):
    data = dict(
        source_text=source,
        description="咖啡",
        amount="18",
        amount_evidence="18元",
        category_l1="饮食",
        category_l2="咖啡",
        classification_basis="明确咖啡消费",
        rule_ids=["TEST_COFFEE"],
    )
    data.update(changes)
    return data


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundle = Path(self.tmp.name)
        private = self.bundle / "private"
        (private / "knowledges").mkdir(parents=True)
        (private / "config.json").write_text(
            json.dumps({"bitable_base_token": "test-only", "bitable_table_id": "tblTest"})
        )
        categories = {
            "version": "test",
            "table_id": "tblTest",
            "tree": [
                {
                    "name": "饮食",
                    "children": [{"name": x, "children": []} for x in ["咖啡", "饮用水"]],
                },
                {
                    "name": "交通通信",
                    "children": [{"name": "汽车", "children": [{"name": "停车费"}]}],
                },
            ],
        }
        rules = {
            "compatible_categories_version": "test",
            "status": "draft",
            "rules": [
                {"id": "TEST_COFFEE", "category": ["饮食", "咖啡", None]},
                {"id": "TEST_WATER", "category": ["饮食", "饮用水", None]},
            ],
        }
        (private / "knowledges/categories.json").write_text(json.dumps(categories))
        (private / "knowledges/rules.json").write_text(json.dumps(rules))
        (self.bundle / ".evaluation-only").write_text("2026-10-14")
        self.session = "unit-chat"

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, tool, args=None, session=None, host="test"):
        return asyncio.run(
            Runtime(self.bundle).call(
                {
                    "tool": tool,
                    "host": host,
                    "session_id": session or self.session,
                    "args": args or {},
                }
            )
        )["data"]

    def start(self, message="今天买咖啡18元", session=None, request_id="message-1"):
        self.call("activate_session", {"user_instruction": "记账小助手帮我记账"}, session)
        return self.call("start_task", {"message": message, "request_id": request_id}, session)[
            "task_id"
        ]

    def test_two_activation_modes_and_non_invocations(self):
        for value in [
            "记账小助手帮我记账",
            "请记账小助手帮我记账，咖啡18元",
            "请使用 feishu-bookkeeping skill 记账",
            "$feishu-bookkeeping 咖啡18元",
        ]:
            with self.subTest(value=value):
                self.assertTrue(can_activate(value))
        for value in [
            "帮我记账",
            "今天咖啡18元",
            "记账小帮手帮我记账",
            "解释记账小助手帮我记账这句话",
            "不要记账小助手帮我记账",
            "示例：记账小助手帮我记账",
            "引用「记账小助手帮我记账」",
            "> 记账小助手帮我记账",
            "安装 feishu-bookkeeping skill",
            "$feishu-bookkeeping 请审阅文档",
            "附件写着记账小助手帮我记账",
        ]:
            with self.subTest(value=value):
                self.assertFalse(can_activate(value))

    def test_wake_phrase_followed_by_question_does_not_authorize(self):
        self.assertFalse(can_activate("记账小助手帮我记账，这句话是什么意思？"))

    def test_malformed_calculation_does_not_block_valid_peer(self):
        task = self.start("买咖啡18元，今天买咖啡19元")
        out = self.call(
            "add_expenses",
            {
                "task_id": task,
                "expenses": [
                    proposal("买咖啡18元", amount_calculation="(18"),
                    proposal("今天买咖啡19元", amount="19", amount_evidence="19元"),
                ],
            },
        )
        self.assertEqual([x["status"] for x in out], ["rejected", "success"])

    def test_session_gate_scope_continuation_and_exit(self):
        with self.assertRaises(ValueError):
            self.call("start_task", {"message": "咖啡18元"})
        self.start()
        self.assertTrue(self.call("session_status")["active"])
        self.assertFalse(self.call("session_status", session="another-chat")["active"])
        self.assertFalse(self.call("session_status", host="another-host")["active"])
        self.call("start_task", {"message": "咖啡18元", "request_id": "message-2"})
        self.call("deactivate_session")
        with self.assertRaises(ValueError):
            self.call("knowledge")

    def test_valid_peer_independent_of_invalid_amount_and_unknown_fields(self):
        task = self.start("今天买咖啡18元，买咖啡0元，买咖啡19元")
        rows = [
            proposal("买咖啡0元", amount="0", amount_evidence="0元"),
            proposal(),
            proposal("买咖啡19元", amount="19", amount_evidence="19元", table_id="other"),
        ]
        out = self.call("add_expenses", {"task_id": task, "expenses": rows})
        self.assertEqual([x["status"] for x in out], ["rejected", "success", "rejected"])
        report = self.call("finish_task", {"task_id": task, "decision": {}})["report"]
        self.assertEqual(len(report["entries"]), 1)
        self.assertEqual(len(report["skipped"]), 2)

    def test_idempotent_start_overlap_and_new_request(self):
        task = self.start()
        self.assertEqual(self.start(), task)
        first = self.call("add_expenses", {"task_id": task, "expenses": [proposal()]})
        again = self.call("add_expenses", {"task_id": task, "expenses": [proposal("买咖啡18元")]})
        self.assertEqual(first[0]["record_id"], again[0]["record_id"])
        self.assertTrue(again[0]["already_submitted"])
        new = self.start(request_id="new-user-message")
        self.assertNotEqual(new, task)
        self.assertEqual(
            self.call("add_expenses", {"task_id": new, "expenses": [proposal()]})[0]["status"],
            "success",
        )

    def test_occurrences_are_independent(self):
        task = self.start("早上买咖啡18元，下午买咖啡18元")
        out = self.call(
            "add_expenses",
            {
                "task_id": task,
                "expenses": [proposal("买咖啡18元", source_occurrence=i) for i in (1, 2)],
            },
        )
        self.assertEqual(len({x["record_id"] for x in out}), 2)

    def test_post_write_decision_conflict_and_finished_task(self):
        task = self.start()
        self.call("add_expenses", {"task_id": task, "expenses": [proposal()]})
        for decision in [
            {"no_consumption": True},
            {"skipped": [{"source_text": "买咖啡18元", "reason": "不写入"}]},
        ]:
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                self.call("finish_task", {"task_id": task, "decision": decision})
        out = self.call("finish_task", {"task_id": task, "decision": {}})
        self.assertIn("未写入飞书", out["text"])
        with self.assertRaises(ValueError):
            self.call("add_expenses", {"task_id": task, "expenses": [proposal()]})

    def test_uncertain_never_resubmitted_and_recovery(self):
        task = self.start(session="faults-unit")
        out = self.call(
            "add_expenses", {"task_id": task, "expenses": [proposal()]}, session="faults-unit"
        )
        self.assertEqual(out[0]["status"], "uncertain")
        again = self.call(
            "add_expenses",
            {"task_id": task, "expenses": [proposal("买咖啡18元")]},
            session="faults-unit",
        )
        self.assertTrue(again[0]["do_not_retry"])
        runtime = Runtime(self.bundle)
        runtime.open()
        runtime.db.execute("UPDATE writes SET state='submitted' WHERE message_id=?", (task,))
        runtime.db.commit()
        runtime.journal.close()
        runtime.audit.close()
        report = self.call("show_result", {"task_id": task}, session="faults-unit")["report"]
        self.assertEqual(report["entries"][0]["outcome"]["status"], "uncertain")

    def test_scope_unknown_commands_and_parameter_limits(self):
        task = self.start()
        for tool, args in [
            ("delete_records", {}),
            ("query_history", {"task_id": task, "keyword": "x", "limit": 51}),
            ("query_history", {"task_id": task, "keyword": "x" * 101}),
            ("query_history", {"task_id": task, "keyword": "x", "table_id": "other"}),
            ("add_expenses", {"task_id": task, "expenses": [proposal()] * 101}),
        ]:
            with self.subTest(tool=tool, args_size=len(str(args))), self.assertRaises(ValueError):
                self.call(tool, args)
        with self.assertRaises(ValueError):
            self.call("show_result", {"task_id": task}, session="other")

    def test_amount_date_provenance_and_category_constraints(self):
        task = self.start()
        changes = [
            {"amount": "NaN"},
            {"amount": "18.001"},
            {"amount": "-18"},
            {"amount": "19"},
            {"currency": "USD"},
            {"source_text": "虚构咖啡18元"},
            {"description": "午饭"},
            {"category_l2": "不存在"},
            {"category_l3": "其他"},
            {"rule_ids": ["MISSING"]},
            {"expense_date": "2026-10-15"},
            {"date_evidence": "昨天"},
            {"classification_basis": ""},
        ]
        for change in changes:
            with self.subTest(change=change):
                out = self.call("add_expenses", {"task_id": task, "expenses": [proposal(**change)]})
                self.assertEqual(out[0]["status"], "rejected")

    def test_grounded_arithmetic_and_execution_day(self):
        task = self.start("买了两瓶饮用水，每瓶3元")
        out = self.call(
            "add_expenses",
            {
                "task_id": task,
                "expenses": [
                    proposal(
                        "买了两瓶饮用水，每瓶3元",
                        description="两瓶饮用水",
                        amount="6",
                        amount_evidence="每瓶3元",
                        amount_calculation="2*3",
                        category_l2="饮用水",
                        rule_ids=["TEST_WATER"],
                    )
                ],
            },
        )
        self.assertEqual(out[0]["status"], "success")
        self.assertEqual(resolve_date("昨天", None, date(2026, 10, 14)), date(2026, 10, 13))
        for expression in ["__import__('os')", "3**3", "3+999", "3/0"]:
            with self.subTest(expression=expression), self.assertRaises((ValueError, SyntaxError)):
                calculate_grounded(expression, "3元")

    def test_lock_and_doctor_do_not_activate(self):
        self.assertFalse(self.call("doctor")["session_activated"])
        self.assertFalse((self.bundle / ".runtime/journal.sqlite3").exists())
        with process_lock(self.bundle / "lock"), self.assertRaises(ValueError):
            with process_lock(self.bundle / "lock"):
                pass

    def test_receipt_classification(self):
        cases = [
            (ProcessResult(0, '{"ok":true,"data":{"record_id_list":["rec123"]}}'), "success"),
            (ProcessResult(0, '{"ok":true,"data":{}}'), "uncertain"),
            (ProcessResult(0, "broken"), "uncertain"),
            (ProcessResult(-9, timeout=True), "uncertain"),
            (ProcessResult(None, launched=False), "failed"),
            (ProcessResult(1, stderr='{"ok":false,"error":{"type":"authorization"}}'), "failed"),
        ]
        for receipt, status in cases:
            with self.subTest(status=status):
                self.assertEqual(classify_write(receipt).status, status)

    def test_fixed_cli_target_projection_and_injection_as_data(self):
        settings = Settings(
            bitable_base_token="fixed-target", bitable_table_id="tblTest", project_dir=self.bundle
        )

        class Audit:
            def event(self, *args, **kwargs):
                pass

        ledger = LarkCliLedger(settings, Audit())
        calls = []

        async def fake(action, args):
            calls.append((action, args))
            return ProcessResult(0, '{"ok":true,"data":{"fields":[],"data":[]}}')

        ledger._run = fake
        keyword = "$(touch /tmp/forbidden); --table-id other"
        asyncio.run(ledger.query_history(keyword, 1))
        args = calls[0][1]
        self.assertEqual(args[args.index("--table-id") + 1], "tblTest")
        self.assertEqual(
            json.loads(args[args.index("--filter-json") + 1])["conditions"][0][2], keyword
        )
        self.assertEqual(args.count("--field-id"), 6)


if __name__ == "__main__":
    unittest.main()
