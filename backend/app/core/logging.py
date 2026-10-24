"""JSON logging with a request ID on every line.

One JSON object per line is what log tools (Grafana Loki, Datadog, CloudWatch) read
best, and the request ID lets you follow a single request through every log line.
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        http = getattr(record, "http", None)
        if http:
            payload["http"] = http
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Send uvicorn's and arq's own logs through the same JSON handler.
    for name in ("uvicorn", "uvicorn.error", "arq", "arq.worker", "arq.jobs"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # Our middleware writes one access line per request, with timing and request ID.
    logging.getLogger("uvicorn.access").disabled = True
