"""
AIM — Database Operations
=========================
SQLite access layer: connection helpers, schema init, stock management,
calibration, and sensor-event recording.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import sqlite3
import logging

from aim_central.shared.config import DB_PATH
from aim_central.shared.events import publish_push_event

logger = logging.getLogger("AIM.DB")


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE — CONNECTION
# ═════════════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Better write concurrency for frequent sensor-event inserts.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE — SCHEMA INIT
# ═════════════════════════════════════════════════════════════════════════════

def database_init():
    statements = [
        """CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL UNIQUE,
            item_weight REAL NOT NULL DEFAULT 0.0
        );""",
        """CREATE TABLE IF NOT EXISTS containers (
            container_id INTEGER PRIMARY KEY,
            item_id INTEGER NOT NULL,
            needed_stock INTEGER NOT NULL DEFAULT 0,
            current_stock INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (item_id) REFERENCES items(item_id)
        );""",
        """CREATE TABLE IF NOT EXISTS container_calibration (
            container_id INTEGER PRIMARY KEY,
            empty_bin_weight_g REAL NOT NULL DEFAULT 0.0,
            scale_factor REAL NOT NULL DEFAULT 1.0,
            min_detectable_weight_g REAL NOT NULL DEFAULT 0.0,
            rounding_mode TEXT NOT NULL DEFAULT 'round',
            FOREIGN KEY (container_id) REFERENCES containers(container_id)
        );""",
        """CREATE TABLE IF NOT EXISTS sensor_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            container_id INTEGER NOT NULL,
            raw_weight_g REAL NOT NULL,
            net_weight_g REAL,
            computed_stock INTEGER,
            sensor_status TEXT NOT NULL,
            decision TEXT NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (container_id) REFERENCES containers(container_id)
        );""",
    ]
    try:
        with get_db() as conn:
            for s in statements:
                conn.execute(s)
            conn.commit()
        logger.info("Database initialized at %s", DB_PATH)
    except sqlite3.OperationalError as e:
        logger.error("Failed to create tables: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE — STOCK QUERIES & MUTATIONS
# ═════════════════════════════════════════════════════════════════════════════

def get_item_weight(container_id):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT i.item_weight FROM containers c
                JOIN items i ON c.item_id = i.item_id
                WHERE c.container_id = ?""", (container_id,))
            row = cur.fetchone()
            return float(row[0]) if row else None
    except sqlite3.OperationalError:
        return None


def find_container(container_id):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT c.container_id, i.item_name, c.needed_stock, c.current_stock
                FROM containers c JOIN items i ON c.item_id = i.item_id
                WHERE c.container_id = ?""", (container_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


def get_stock_level(container_id):
    container = find_container(container_id)
    if container:
        if container["current_stock"] == 0:
            return "Red"
        elif container["current_stock"] <= container["needed_stock"] * 0.5:
            return "Yellow"
    return "Green"


def get_stock(container_id):
    container = find_container(container_id)
    return container["current_stock"] if container else -1


def set_stock(container_id, new_stock):
    if new_stock < 0:
        return False
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE containers SET current_stock = ? WHERE container_id = ?",
                        (new_stock, container_id))
            conn.commit()
            updated = cur.rowcount > 0
            if updated:
                publish_push_event("stock", container_id)
            return updated
    except sqlite3.OperationalError:
        return False


def change_stock(container_id, change_amount):
    container = find_container(container_id)
    if container is None:
        return False
    new_stock = container["current_stock"] + change_amount
    if new_stock < 0:
        return False
    return set_stock(container_id, new_stock)


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE — CALIBRATION
# ═════════════════════════════════════════════════════════════════════════════

def get_container_calibration(container_id):
    defaults = {
        "empty_bin_weight_g": 0.0,
        "scale_factor": 1.0,
        "min_detectable_weight_g": 0.0,
        "rounding_mode": "round",
    }
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT empty_bin_weight_g, scale_factor,
                min_detectable_weight_g, rounding_mode
                FROM container_calibration WHERE container_id = ?""",
                (container_id,))
            row = cur.fetchone()
            return dict(row) if row else defaults
    except sqlite3.OperationalError:
        return defaults


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE — SENSOR EVENTS
# ═════════════════════════════════════════════════════════════════════════════

def record_sensor_event(container_id, raw_weight_g, sensor_status, decision,
                        net_weight_g=None, computed_stock=None, note=None,
                        notify_ui=True):
    try:
        with get_db() as conn:
            conn.execute("""INSERT INTO sensor_events
                (container_id, raw_weight_g, net_weight_g, computed_stock,
                 sensor_status, decision, note)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (container_id, float(raw_weight_g),
                 None if net_weight_g is None else float(net_weight_g),
                 computed_stock, sensor_status, decision, note))
            conn.commit()
            if notify_ui:
                publish_push_event("sensor", container_id)
            return True
    except sqlite3.OperationalError:
        return False


def update_stock_from_weight(container_id, measured_weight_g):
    if measured_weight_g < 0:
        return False
    item_weight_g = get_item_weight(container_id)
    if item_weight_g is None or item_weight_g <= 0:
        return False

    cal = get_container_calibration(container_id)
    empty_bin_weight_g = float(cal["empty_bin_weight_g"])
    scale_factor = float(cal["scale_factor"])
    min_detectable_weight_g = float(cal["min_detectable_weight_g"])
    rounding_mode = cal["rounding_mode"]

    if scale_factor <= 0:
        return False

    net_weight_g = max(0.0, (measured_weight_g - empty_bin_weight_g) * scale_factor)
    if net_weight_g < min_detectable_weight_g:
        net_weight_g = 0.0

    ratio = net_weight_g / item_weight_g
    if rounding_mode == "floor":
        calculated_stock = int(ratio)
    elif rounding_mode == "ceil":
        calculated_stock = int(ratio) if ratio == int(ratio) else int(ratio) + 1
    else:
        calculated_stock = int(round(ratio))

    return set_stock(container_id, calculated_stock)
