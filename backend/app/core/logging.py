"""Structured JSON logging. Never log message bodies or credentials."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_SECRET_PATTERN = re.compile(r"(Bearer\s+[A-Za-z0-9._~+/=-]+|(?i:password|token|secret)=[^\s&]+)")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = _SECRET_PATTERN.sub("[REDACTED]", record.getMessage())
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": message,
        }
        for key in ("request_id", "project_id", "user_id"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
