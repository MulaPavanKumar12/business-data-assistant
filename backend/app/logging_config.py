"""Logging setup shared by the API and the CLI scripts.

Logs are emitted as single-line ``key=value`` records, which are easy to read
locally and easy to ship to a log aggregator later.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s level=%(levelname)s logger=%(name)s msg=%(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once.

    Args:
        level: Log level name such as ``"DEBUG"`` or ``"INFO"``.
    """
    root = logging.getLogger()
    if getattr(root, "_bda_configured", False):
        root.setLevel(level.upper())
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%Y-%m-%dT%H:%M:%S"))
    root.handlers = [handler]
    root.setLevel(level.upper())
    root._bda_configured = True  # type: ignore[attr-defined]

    # Third-party loggers that are too chatty at INFO.
    logging.getLogger("faker").setLevel(logging.WARNING)
