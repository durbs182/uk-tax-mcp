"""
Production configuration for uk-tax-mcp.

All settings are read from environment variables with sensible defaults so
the server works out-of-the-box in development and can be tuned for production
by setting env vars (in a .env file, Docker --env-file, or platform secrets).

Environment variables
---------------------
SERVICE_NAME    Name tag added to every log record.  Default: "uk-tax-mcp"
LOG_LEVEL       Python log level name.               Default: "INFO"
LOG_FORMAT      "json" for structured output,        Default: "json"
                "text" for human-readable console.
RULES_DIR       Absolute path to the rules directory.
                Default: the bundled registry/rules tree.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

# ---------------------------------------------------------------------------
# Settings (read once at import time)
# ---------------------------------------------------------------------------

SERVICE_NAME: str = os.environ.get("SERVICE_NAME", "uk-tax-mcp")
LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO").upper()
LOG_FORMAT: str = os.environ.get("LOG_FORMAT", "json").lower()
RULES_DIR: str | None = os.environ.get("RULES_DIR")


# ---------------------------------------------------------------------------
# Structured JSON log formatter
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "service": SERVICE_NAME,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in logging.LogRecord.__dict__ and not k.startswith("_")
        }
        if extra:
            payload["extra"] = extra
        return json.dumps(payload, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """
    Configure the root logger once.

    Call this at server startup (before any other logging calls).  Subsequent
    calls are safe but have no effect (idempotent via the _configured guard).
    """
    root = logging.getLogger()
    if getattr(root, "_uk_tax_mcp_configured", False):
        return

    level = getattr(logging, LOG_LEVEL, logging.INFO)
    root.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    if LOG_FORMAT == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s")
        )

    root.handlers.clear()
    root.addHandler(handler)
    root._uk_tax_mcp_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Return a logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
