"""
AIM — Logging Configuration
=============================
Configures the root logger to write to both the console and a rotating
log file. Call setup_logging() once from main.py before any other
imports so every module's logger inherits the same handlers.

Log files rotate at 1 MB and keep the three most recent files, so the
total on-disk footprint stays under ~3 MB regardless of uptime.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import logging
import logging.handlers
import os

from aim_central.shared.config import (
    LOG_PATH,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    LOG_BACKUP_COUNT,
)

# ═════════════════════════════════════════════════════════════════════════════
# LOGGING SETUP
# ═════════════════════════════════════════════════════════════════════════════

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    log_path: str = LOG_PATH,
    level: int = LOG_LEVEL,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> None:
    """
    Configure the root logger with a console handler and a rotating file handler.
    Defaults are drawn from shared/config.py (and ultimately from .env).

    Parameters
    ----------
    log_path : str
        Path to the log file. Parent directory is created if it does not exist.
    level : int
        Logging level applied to both handlers (e.g. logging.DEBUG, logging.INFO).
    max_bytes : int
        Maximum size of a single log file in bytes before rotation.
    backup_count : int
        Number of rotated log files to keep alongside the active one.
    """
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid adding duplicate handlers if setup_logging is called more than once.
    if not root.handlers:
        root.addHandler(console_handler)
        root.addHandler(file_handler)
