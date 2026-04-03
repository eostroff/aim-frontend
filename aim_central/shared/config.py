"""
AIM — Configuration
====================
Single source of truth for all runtime configuration. Loads a .env file
from the project root on first import, then exposes each setting as a
typed constant. Every other module imports its config values from here
rather than defining its own hardcoded defaults.

Add new settings by:
  1. Adding the variable to .env (and .env.example)
  2. Adding a typed constant below with a sensible fallback default

Requires python-dotenv:
    pip install python-dotenv
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import os
import logging

from dotenv import load_dotenv

# ═════════════════════════════════════════════════════════════════════════════
# ENV LOADING
# ═════════════════════════════════════════════════════════════════════════════

# Walk up from aim_central/shared/ to the project root and load .env.
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))

# ═════════════════════════════════════════════════════════════════════════════
# CAN BUS
# ═════════════════════════════════════════════════════════════════════════════

CAN_CHANNEL: str = os.getenv("AIM_CAN_CHANNEL", "can0")
CAN_BITRATE: int = int(os.getenv("AIM_CAN_BITRATE", "500000"))

# ═════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═════════════════════════════════════════════════════════════════════════════

DB_PATH: str = os.getenv(
    "AIM_DB_PATH",
    os.path.join(_PROJECT_ROOT, "inventory.db"),
)

# ═════════════════════════════════════════════════════════════════════════════
# FLASK
# ═════════════════════════════════════════════════════════════════════════════

FLASK_PORT: int = int(os.getenv("AIM_FLASK_PORT", "3000"))

# ═════════════════════════════════════════════════════════════════════════════
# LOGGING
# ═════════════════════════════════════════════════════════════════════════════

LOG_PATH: str = os.getenv(
    "AIM_LOG_PATH",
    os.path.join(_PROJECT_ROOT, "logs", "aim.log"),
)

# Accepts any standard level name: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL: int = getattr(logging, os.getenv("AIM_LOG_LEVEL", "INFO").upper(), logging.INFO)

LOG_MAX_BYTES: int  = int(os.getenv("AIM_LOG_MAX_BYTES",  "1000000"))
LOG_BACKUP_COUNT: int = int(os.getenv("AIM_LOG_BACKUP_COUNT", "3"))
