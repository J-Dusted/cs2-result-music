"""Small, persistent decision trace; never stores raw GSI identities or payloads."""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import data_dir


class _Handler(RotatingFileHandler):
    def handleError(self, record):
        # Surface a disk failure to our caller instead of printing to hidden stderr.
        raise


class ResultLog:
    def __init__(self, path: Path | None = None, warn=None):
        self.path = path
        self.warn = warn or (lambda message: None)
        self.handler = None
        self.failed = False
        self.last_state = None

    def record(self, engine, actions=(), *, received=None, processed=None,
               provider_timestamp=None, cause="state"):
        if self.failed:
            return
        state = engine.result_snapshot()
        if state == self.last_state and not actions and cause == "state":
            return
        self.last_state = state
        processed = time.monotonic() if processed is None else processed
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "processed_monotonic": processed, "received_monotonic": received,
            "queue_delay_ms": round(max(0, processed - received) * 1000, 3) if received is not None else None,
            "provider_timestamp": provider_timestamp if isinstance(provider_timestamp, (int, float)) else None,
            "cause": cause, **state,
            "actions": [{"kind": a.kind, "event": a.event, "reason": a.reason,
                         "when_live": a.when_live} for a in actions],
        }
        try:
            if self.handler is None:
                path = self.path or data_dir() / "gsi-results.jsonl"
                path.parent.mkdir(parents=True, exist_ok=True)
                self.handler = _Handler(path, maxBytes=2 * 1024 * 1024,
                                        backupCount=3, encoding="utf-8", delay=True)
                self.handler.setFormatter(logging.Formatter("%(message)s"))
            record = logging.LogRecord("cs2music.result", logging.INFO, "", 0,
                                       json.dumps(entry, ensure_ascii=False), (), None)
            self.handler.emit(record)  # Each decision is flushed before returning.
        except (OSError, ValueError) as exc:
            self.failed = True
            self.close()
            self.warn(f"结算诊断日志写入失败，音乐继续运行：{exc}")

    def close(self):
        if self.handler is not None:
            try:
                self.handler.close()
            except (OSError, ValueError):
                pass
            self.handler = None
