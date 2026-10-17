"""Independent assertions against the persisted effects, not the evaluator's verdict."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path


def row_key(entry):
    e, o = entry["expense"], entry["outcome"]
    return (
        e["expense_date"],
        e["category_l1"],
        e["category_l2"],
        e.get("category_l3"),
        Decimal(e["amount"]),
        o["status"],
    )


def verify(root, original_cases):
    results = json.loads((root / "agent_results.json").read_text())
    messages = json.loads((root / "scenarios.json").read_text())
    actual = {x["id"]: x for x in results}
    failures = []
    if len(actual) != len(messages):
        failures.append("评估消息数量或ID不匹配")
    db = sqlite3.connect(root / "candidate/.runtime/journal.sqlite3")
    db.row_factory = sqlite3.Row
    reports = {}
    for message in messages:
        item = actual.get(message["id"], {})
        if item.get("task_id"):
            row = db.execute(
                "SELECT * FROM messages WHERE message_id=?", (item["task_id"],)
            ).fetchone()
            if row is None or row["text"] != message["text"] or row["state"] != "done":
                failures.append(message["id"] + ":任务原文或结束状态不符合预期")
                continue
            owner = hashlib.sha256(
                (message["host"] + "\0" + message["session_id"]).encode()
            ).hexdigest()
            if row["chat_id"] != owner:
                failures.append(message["id"] + ":会话归属错误")
            reports[message["id"]] = json.loads(row["report"])
            if "未写入飞书" not in item.get("response", ""):
                failures.append(message["id"] + ":反馈缺少隔离验收标识")
    inactive = [
        "inactive",
        "quoted",
        "negated",
        "attachment",
        "similar",
        "wake_only",
        "after_stop",
        "other_chat",
        "other_host",
    ]
    if "question_suffix" in actual:
        inactive.append("question_suffix")
    for ident in inactive:
        if actual.get(ident, {}).get("task_id"):
            failures.append(ident + ":不应创建记账任务")
    expected = {}
    cases = json.loads(original_cases.read_text())
    for case in cases:
        expected[case["id"]] = Counter(
            (day, l1, l2, l3, Decimal(amount), status)
            for day, l1, l2, l3, amount, status in case["rows"]
        )
        report = reports.get(case["id"], {})
        if Counter(row_key(e) for e in report.get("entries", [])) != expected[case["id"]]:
            failures.append(case["id"] + ":实际条目与原项目预期不一致")
        if len(report.get("skipped", [])) != len(case["skipped"]):
            failures.append(case["id"] + ":未入账数量不一致")
        for source in case["skipped"]:
            if not any(source in s["source_text"] for s in report.get("skipped", [])):
                failures.append(case["id"] + ":缺少原项目规定的未入账项")
        if report.get("no_consumption") != case.get("no_consumption", False):
            failures.append(case["id"] + ":无消费状态错误")
    for ident, rows in {
        "continued": [("2026-10-14", "饮食", "咖啡", None, "18", "success")],
        "named": [("2026-10-14", "饮食", "咖啡", None, "20", "success")],
        "calculation": [
            ("2026-10-14", "饮食", "饮用水", None, "6", "success"),
            ("2026-10-13", "饮食", "咖啡", None, "18", "success"),
        ],
        "history_failure": [("2026-10-14", "饮食", "咖啡", None, "18", "success")],
        "future": [],
    }.items():
        expected[ident] = Counter((d, a, b, c, Decimal(v), s) for d, a, b, c, v, s in rows)
        if (
            Counter(row_key(e) for e in reports.get(ident, {}).get("entries", []))
            != expected[ident]
        ):
            failures.append(ident + ":额外场景结果不一致")
    if reports.get("future", {}).get("no_consumption") is not True:
        failures.append("future:计划消费应为无消费")
    written = db.execute("SELECT * FROM isolated_calls WHERE operation='create'").fetchall()
    total = sum(sum(x.values()) for x in expected.values())
    if len(written) != total:
        failures.append("实际新增调用数量不一致，可能存在重复或遗漏")
    history = db.execute(
        "SELECT count(*) FROM isolated_calls WHERE operation='query' AND session_id='history-failure'"
    ).fetchone()[0]
    if not history:
        failures.append("未覆盖历史查询失败后的有效消费")
    for report in reports.values():
        if report.get("notice"):
            failures.append("某任务未完整完成")
    audit = [
        json.loads(line)
        for line in (root / "candidate/.runtime/events.jsonl").read_text().splitlines()
    ]
    live_events = [e for e in audit if e["event"].startswith("cli_")]
    if live_events or (root / "candidate/scripts/bookkeeping_core/cli.py").exists():
        failures.append("隔离边界不成立")
    summary = {
        "passed": not failures,
        "scenario_count": len(messages),
        "completed_tasks": len(reports),
        "isolated_create_calls": len(written),
        "live_cli_calls": len(live_events),
        "real_bitable_writes": 0,
        "failures": failures,
    }
    (root / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return not failures


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("original_cases", type=Path)
    args = parser.parse_args()
    raise SystemExit(0 if verify(args.root, args.original_cases) else 1)
