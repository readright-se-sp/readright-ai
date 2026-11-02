"""JSON logs with the job id on every line. Never log passage text, transcripts or full URLs."""

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)

_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "job_id": job_id_var.get(),
        }
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    # Replace only our own earlier handler; leave handlers added by others (e.g. test capture).
    root.handlers[:] = [h for h in root.handlers if not isinstance(h.formatter, JsonFormatter)]
    root.addHandler(handler)
    root.setLevel(level.upper())
