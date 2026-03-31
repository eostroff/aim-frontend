"""
AIM Dashboard — All-in-One Standalone
======================================
Single file that runs everything:
  - SQLite database (creates tables on startup)
  - CAN driver (receives STM32 messages, sends commands)
  - CAN→DB bridge (decodes weight, updates stock, logs sensor events)
  - Flask dashboard server (touch UI on port 3000)

The CAN bridge runs in a background thread. If the CAN bus isn't
available (no MCP2515, no can0 interface), the dashboard still works
— you just won't get live sensor data.

Usage:
    pip install flask python-can
    python server.py

Then open http://localhost:3000 on the Pi touchscreen.
"""

import os
import math
import sqlite3
import struct
import logging
import time
import threading
from collections import defaultdict, deque
from flask import Flask, jsonify, send_file, request

# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")
DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
CAN_CHANNEL = "can0"
CAN_BITRATE = 500000
FLASK_PORT = 3000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("AIM")


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE OPERATIONS
# (mirrors aim_central/logic/DatabaseOperations.py exactly)
# ═════════════════════════════════════════════════════════════════════════════

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
            return cur.rowcount > 0
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


def record_sensor_event(container_id, raw_weight_g, sensor_status, decision,
                        net_weight_g=None, computed_stock=None, note=None):
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


# ═════════════════════════════════════════════════════════════════════════════
# CAN DRIVER
# (mirrors aim_central/drivers/canDriver.py)
# ═════════════════════════════════════════════════════════════════════════════

# CAN IDs
STM32_TO_PI_ID = 0x100
PI_TO_STM32_ID = 0x200

# Status codes
STATUS_OK = 0x00
STATUS_ERROR = 0x01
STATUS_NOT_TARED = 0x02

# Tare flags
TARE_NONE = 0x00
TARE_SUCCESS = 0x01
TARE_FAIL = 0x02

# LED states
LED_OFF = 0x00
LED_GREEN = 0x01
LED_YELLOW = 0x02
LED_RED = 0x03

# Buzzer
BUZZER_OFF = 0x00
BUZZER_ON = 0x01

STATUS_MAP = {STATUS_OK: "ok", STATUS_ERROR: "error", STATUS_NOT_TARED: "not_tared"}
TARE_MAP = {TARE_NONE: "none", TARE_SUCCESS: "success", TARE_FAIL: "fail"}


class CANDriver:
    def __init__(self, channel='can0', bitrate=500000):
        self.channel = channel
        self.bitrate = bitrate
        self.bus = None
        self.logger = logging.getLogger("CANDriver")

    def connect(self):
        import can
        self.bus = can.interface.Bus(
            channel=self.channel, bustype='socketcan', bitrate=self.bitrate)
        self.logger.info("Connected to CAN bus on %s", self.channel)

    def disconnect(self):
        if self.bus:
            self.bus.shutdown()
            self.logger.info("CAN bus disconnected.")

    def receive(self, timeout=1.0):
        if not self.bus:
            raise RuntimeError("CAN bus not connected.")
        msg = self.bus.recv(timeout=timeout)
        if msg is None:
            return None
        if not (0x100 <= msg.arbitration_id <= 0x1FF):
            return None
        return self._parse(msg)

    def _parse(self, msg):
        if len(msg.data) < 7:
            return None
        bin_id = msg.data[0]
        weight_g = struct.unpack('f', bytes(msg.data[1:5]))[0]
        status = msg.data[5]
        tare = msg.data[6] if len(msg.data) > 6 else 0
        return {
            "bin_id": bin_id,
            "weight_g": round(weight_g, 2),
            "status": STATUS_MAP.get(status, "unknown"),
            "tare_flag": TARE_MAP.get(tare, "none"),
        }

    def send_command(self, bin_id, tare=False, led=LED_OFF, buzzer=BUZZER_OFF):
        if not self.bus:
            raise RuntimeError("CAN bus not connected.")
        import can
        data = [bin_id, 0x01 if tare else 0x00, led, buzzer, 0, 0, 0, 0]
        msg = can.Message(arbitration_id=PI_TO_STM32_ID + bin_id,
                          data=data, is_extended_id=False)
        self.bus.send(msg)

    def tare_bin(self, bin_id):
        self.send_command(bin_id, tare=True)

    def set_led(self, bin_id, state):
        self.send_command(bin_id, led=state)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()


# ═════════════════════════════════════════════════════════════════════════════
# CAN → DATABASE BRIDGE
# (mirrors aim_central/logic/CanDatabaseBridge.py)
# ═════════════════════════════════════════════════════════════════════════════

class CanDatabaseBridge:
    def __init__(self, can_channel='can0', bitrate=500000,
                 publish_led_feedback=True, stability_window=3,
                 stability_tolerance_g=2.0):
        self.driver = CANDriver(channel=can_channel, bitrate=bitrate)
        self.publish_led_feedback = publish_led_feedback
        self.stability_window = max(1, int(stability_window))
        self.stability_tolerance_g = float(stability_tolerance_g)
        self._weight_windows = defaultdict(lambda: deque(maxlen=self.stability_window))
        self.logger = logging.getLogger("CanDatabaseBridge")

    def _stable_weight(self, bin_id, latest_weight_g):
        window = self._weight_windows[bin_id]
        window.append(float(latest_weight_g))
        if len(window) < self.stability_window:
            return None
        spread = max(window) - min(window)
        if spread > self.stability_tolerance_g:
            return None
        return sum(window) / len(window)

    def _stock_level_to_led(self, level):
        if level == "Red": return LED_RED
        if level == "Yellow": return LED_YELLOW
        return LED_GREEN

    def process_one_message(self, timeout=1.0):
        msg = self.driver.receive(timeout=timeout)
        if msg is None:
            return False

        bin_id = msg["bin_id"]
        weight_g = msg["weight_g"]
        status = msg["status"]
        tare_flag = msg["tare_flag"]

        if status == "not_tared":
            self._weight_windows[bin_id].clear()
            record_sensor_event(bin_id, weight_g, status, "rejected_not_tared",
                                note="bin not tared since boot")
            return True

        if status == "error":
            record_sensor_event(bin_id, weight_g, status, "rejected_error",
                                note="sensor reported hardware error")
            return True

        if tare_flag == "success":
            self._weight_windows[bin_id].clear()
            record_sensor_event(bin_id, weight_g, status, "tare_confirmed",
                                note="tare success — stability window reset")
            return True

        stable_weight_g = self._stable_weight(bin_id, weight_g)
        if stable_weight_g is None:
            record_sensor_event(bin_id, weight_g, status, "deferred_unstable",
                                note="collecting stability window or spread too high")
            return True

        updated = update_stock_from_weight(bin_id, stable_weight_g)
        if not updated:
            record_sensor_event(bin_id, weight_g, status, "failed_update",
                                note="update_stock_from_weight returned False")
            return True

        record_sensor_event(bin_id, weight_g, status, "accepted",
                            net_weight_g=stable_weight_g,
                            computed_stock=get_stock(bin_id),
                            note=f"stable window={self.stability_window}")

        if self.publish_led_feedback:
            level = get_stock_level(bin_id)
            self.driver.set_led(bin_id, self._stock_level_to_led(level))

        return True

    def run_forever(self, timeout=1.0, idle_sleep_s=0.05):
        with self.driver:
            self.logger.info("CAN→DB bridge started.")
            while True:
                try:
                    processed = self.process_one_message(timeout=timeout)
                    if not processed:
                        time.sleep(idle_sleep_s)
                except Exception as e:
                    self.logger.error("Bridge error: %s", e)
                    time.sleep(1.0)


# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND CAN THREAD
# ═════════════════════════════════════════════════════════════════════════════

def start_can_bridge():
    """
    Start the CAN bridge in a daemon thread.
    If python-can isn't installed or can0 isn't up, logs a warning and exits.
    The Flask server keeps running either way.
    """
    def _run():
        try:
            import can  # noqa: F401
        except ImportError:
            logger.warning("python-can not installed — CAN bridge disabled. "
                           "Install with: pip install python-can")
            return
        try:
            bridge = CanDatabaseBridge(can_channel=CAN_CHANNEL, bitrate=CAN_BITRATE)
            bridge.run_forever()
        except Exception as e:
            logger.warning("CAN bridge failed to start: %s", e)
            logger.warning("Dashboard will run without live sensor data.")

    t = threading.Thread(target=_run, daemon=True, name="CAN-Bridge")
    t.start()
    logger.info("CAN bridge thread launched.")


# ═════════════════════════════════════════════════════════════════════════════
# FLASK APP
# ═════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)


@app.route("/")
def index():
    return send_file(DASHBOARD_PATH)


@app.route("/api/containers")
def api_containers():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.container_id, c.item_id, i.item_name, i.item_weight,
                       c.needed_stock, c.current_stock
                FROM containers c
                JOIN items i ON c.item_id = i.item_id
                ORDER BY c.container_id
            """)
            return jsonify([dict(r) for r in cur.fetchall()])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>")
def api_container_detail(cid):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.container_id, c.item_id, i.item_name, i.item_weight,
                       c.needed_stock, c.current_stock
                FROM containers c
                JOIN items i ON c.item_id = i.item_id
                WHERE c.container_id = ?
            """, (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            return jsonify(dict(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calibration/<int:cid>")
def api_calibration(cid):
    defaults = {
        "container_id": cid,
        "empty_bin_weight_g": 0.0,
        "scale_factor": 1.0,
        "min_detectable_weight_g": 0.0,
        "rounding_mode": "round",
    }
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT container_id, empty_bin_weight_g, scale_factor,
                min_detectable_weight_g, rounding_mode
                FROM container_calibration WHERE container_id = ?""", (cid,))
            row = cur.fetchone()
            return jsonify(dict(row) if row else defaults)
    except sqlite3.OperationalError:
        return jsonify(defaults)


@app.route("/api/events/<int:cid>")
def api_events(cid):
    limit = request.args.get("limit", 100, type=int)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT event_id, container_id, raw_weight_g, net_weight_g,
                       computed_stock, sensor_status, decision, note, created_at
                FROM sensor_events WHERE container_id = ?
                ORDER BY created_at DESC LIMIT ?
            """, (cid, limit))
            return jsonify([dict(r) for r in cur.fetchall()])
    except sqlite3.OperationalError:
        return jsonify([])


@app.route("/api/stock-level/<int:cid>")
def api_stock_level(cid):
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_stock, needed_stock FROM containers WHERE container_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"level": "unknown"})
            current, needed = row["current_stock"], row["needed_stock"]
            if current == 0:
                level = "Red"
            elif current <= needed * 0.5:
                level = "Yellow"
            else:
                level = "Green"
            return jsonify({"container_id": cid, "level": level,
                            "current_stock": current, "needed_stock": needed})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>/adjust", methods=["POST"])
def api_adjust_stock(cid):
    data = request.get_json(force=True)
    change = data.get("change", 0)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT current_stock FROM containers WHERE container_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            new_stock = row["current_stock"] + change
            if new_stock < 0:
                return jsonify({"error": "cannot go below zero"}), 400
            cur.execute("UPDATE containers SET current_stock = ? WHERE container_id = ?",
                        (new_stock, cid))
            conn.commit()
            return jsonify({"container_id": cid, "current_stock": new_stock})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>/tare", methods=["POST"])
def api_tare(cid):
    try:
        driver = CANDriver(channel=CAN_CHANNEL, bitrate=CAN_BITRATE)
        driver.connect()
        driver.tare_bin(cid)
        driver.disconnect()
        return jsonify({"status": "tare_sent", "container_id": cid})
    except Exception as e:
        return jsonify({"status": "tare_failed", "error": str(e)}), 500


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    database_init()
    start_can_bridge()

    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  AIM Dashboard running on port 3000         │")
    print("  │  Open http://localhost:3000 on touchscreen   │")
    print("  │                                             │")
    print("  │  CAN bridge runs in background thread       │")
    print("  │  (disabled gracefully if can0 unavailable)  │")
    print("  └─────────────────────────────────────────────┘")
    print()

    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False)