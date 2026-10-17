#!/usr/bin/env python3
"""One JSON request on stdin, one JSON response on stdout. No argv dispatch."""

import json
import os
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parents[1]


def main():
    if len(sys.argv) != 1:
        print(
            json.dumps(
                {"ok": False, "reason": "仅接受标准输入 JSON，不接受命令行业务参数"},
                ensure_ascii=False,
            )
        )
        return 2
    if sys.version_info < (3, 11):
        print(
            json.dumps(
                {"ok": False, "reason": "需要 Python 3.11 或以上，请先执行安装器"},
                ensure_ascii=False,
            )
        )
        return 2
    if (BUNDLE / ".evaluation-only").exists():
        # Fail closed even if a backend accidentally attempts a real subprocess or socket.
        def deny_external(event, args):
            if event in {
                "subprocess.Popen",
                "os.system",
                "os.exec",
                "os.posix_spawn",
                "socket.connect",
            }:
                raise RuntimeError("隔离验收禁止启动外部进程或连接网络")

        sys.addaudithook(deny_external)
    try:
        import cn2an  # noqa: F401
        import pydantic  # noqa: F401
    except ImportError:
        executable = BUNDLE / ".venv/bin/python"
        if executable.exists() and Path(sys.prefix).resolve() != (BUNDLE / ".venv").resolve():
            os.execv(str(executable), [str(executable), str(Path(__file__).resolve())])
        print(
            json.dumps(
                {"ok": False, "reason": "缺少依赖，请用 Python 3.11+ 运行 scripts/install.py"},
                ensure_ascii=False,
            )
        )
        return 2
    import asyncio

    from bookkeeping_core.runtime import Runtime
    from pydantic import ValidationError

    try:
        raw = json.loads(sys.stdin.read(1000001))
        result = asyncio.run(Runtime(BUNDLE).call(raw))
    except ValidationError:
        result = {
            "ok": False,
            "reason": "请求参数不合法、存在未知字段或超出工具限制；原始校验输入不回传",
        }
    except (ValueError, KeyError, FileNotFoundError) as error:
        from bookkeeping_core.audit import Redactor

        result = {"ok": False, "reason": Redactor([]).text(str(error))}
    except Exception:
        result = {
            "ok": False,
            "reason": "工具运行异常；已预留的提交不会自动重试，请用原 task_id 查看结果",
        }
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
