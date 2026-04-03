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
# GPS GEOFENCE
# ═════════════════════════════════════════════════════════════════════════════

GPS_PORT: str = os.getenv("AIM_GPS_PORT", "/dev/ttyACM0")
GPS_BAUDRATE: int = int(os.getenv("AIM_GPS_BAUDRATE", "9600"))

# Hayden Hall, Northeastern University, Boston MA
GPS_FENCE_LAT: float = float(os.getenv("AIM_GPS_FENCE_LAT", "42.3396"))
GPS_FENCE_LON: float = float(os.getenv("AIM_GPS_FENCE_LON", "-71.0882"))
GPS_FENCE_RADIUS_M: float = float(os.getenv("AIM_GPS_FENCE_RADIUS_M", "200"))

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
