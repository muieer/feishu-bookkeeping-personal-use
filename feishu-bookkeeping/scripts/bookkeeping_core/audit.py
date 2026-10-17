import contextvars
import json
import logging
import os
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from pydantic import BaseModel

message_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "message_id", default=None
)


class Redactor:
    def __init__(self, secrets: list[str]):
        self.secrets = sorted([value for value in secrets if value], key=len, reverse=True)

    def text(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)Bearer\s+[^\s\"']+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}", "[REDACTED]", value)
        value = re.sub(r"wss?://[^\s\"']+", "[REDACTED_WS_URL]", value)
        return re.sub(
            r"(?i)((?:app[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"authorization|password)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+",
            r"\1[REDACTED]",
            value,
        )

    def clean(self, value: Any) -> Any:
        if isinstance(value, BaseModel):
            value = value.model_dump(mode="json")
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if re.search(
                    r"secret|api.?key|authorization|password|access.?token|refresh.?token",
                    str(key),
                    re.I,
                )
                else self.clean(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        if value is None or isinstance(value, (int, float, bool)):
            return value
        return self.text(str(value))


class PrivateRotatingHandler(RotatingFileHandler):
    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:
        # Standard logging prints the original, unredacted record on I/O errors.
        try:
            os.write(2, b"bookkeeping audit log write failed\n")
        except OSError:
            pass

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding)


class SafeFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor):
        super().__init__("%(message)s")
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        safe = self.redactor.text(super().format(record))
        if record.name.startswith("bookkeeping.audit."):
            return safe
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "event": "sdk_log",
                "logger": record.name,
                "level": record.levelname,
                "message": safe,
            },
            ensure_ascii=False,
        )


class Audit:
    def __init__(self, path: Path, secrets: list[str]):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        self.redactor = Redactor(secrets)
        self.logger = logging.getLogger(f"bookkeeping.audit.{id(self)}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = PrivateRotatingHandler(
            path, maxBytes=10_000_000, backupCount=3, encoding="utf-8"
        )
        self.handler.setFormatter(SafeFormatter(self.redactor))
        self.logger.addHandler(self.handler)

    def event(self, event: str, **data: Any) -> None:
        payload = {
            "time": datetime.now(UTC).isoformat(),
            "event": event,
            "message_id": message_context.get(),
            **data,
        }
        self.logger.info(json.dumps(self.redactor.clean(payload), ensure_ascii=False))

    def close(self) -> None:
        self.handler.close()
        self.logger.removeHandler(self.handler)
