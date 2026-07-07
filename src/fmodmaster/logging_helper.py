"""Logging helper for fModMaster.

Provides a ``get_logger`` factory that configures a standard Python logger with
QsLog-style severity levels and writes output to both ``fModMaster.log`` (next
to the current working directory) and ``stderr``.

QsLog level mapping (0=Trace .. 6=Off):
    0 = TRACE
    1 = DEBUG
    2 = VERBOSE  (between DEBUG and INFO)
    3 = INFO
    4 = WARN
    5 = ERROR
    6 = OFF
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

# QsLog-style level constants.
TRACE = 0
DEBUG = 1
VERBOSE = 2
INFO = 3
WARN = 4
ERROR = 5
OFF = 6

# Map QsLog levels to Python logging levels.
_QSLOG_TO_PY: dict[int, int] = {
    TRACE: logging.DEBUG - 5,
    DEBUG: logging.DEBUG,
    VERBOSE: (logging.DEBUG + logging.INFO) // 2,
    INFO: logging.INFO,
    WARN: logging.WARNING,
    ERROR: logging.ERROR,
    OFF: logging.CRITICAL + 100,
}

# Register custom level names for nicer log output.
logging.addLevelName(TRACE, "TRACE")
logging.addLevelName(VERBOSE, "VERBOSE")
logging.addLevelName(WARN, "WARN")

_LOG_FILE_NAME = "fModMaster.log"


def _log_file_path() -> str:
    """Return the absolute path of the log file in the current directory."""
    return os.path.join(os.getcwd(), _LOG_FILE_NAME)


def get_logger(name: str, level: int = INFO) -> logging.Logger:
    """Create or retrieve a configured logger.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.
        level: QsLog-style level (0=Trace .. 6=Off). Defaults to ``INFO`` (3).

    Returns:
        A ``logging.Logger`` writing to ``fModMaster.log`` and ``stderr``.
    """
    if level not in _QSLOG_TO_PY:
        raise ValueError(f"Invalid QsLog level: {level}")

    py_level = _QSLOG_TO_PY[level]
    logger = logging.getLogger(name)
    logger.setLevel(py_level)

    # Avoid attaching duplicate handlers on repeated calls.
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.FileHandler(_log_file_path(), encoding="utf-8")
    file_handler.setLevel(py_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(py_level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # Do not propagate to the root logger (avoids double stderr output).
    logger.propagate = False
    return logger
