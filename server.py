"""
AIM Dashboard — Standalone Server
===================================
Self-contained Flask app. No dependency on the main repo.

Creates its own SQLite database, seeds it with test inventory,
and serves the touch-optimized dashboard.

Usage:
    pip install flask
    python server.py

Then open http://localhost:5000 on the Pi's touchscreen.
"""

import os
import sqlite3
import json
from flask import Flask, jsonify, send_file, request

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inventory.db")
DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")

app = Flask(__name__)


# ── Database Setup ───────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def database_init():
    """Create tables if they don't exist."""
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
    with get_db() as conn:
        for s in statements:
            conn.execute(s)
        conn.commit()
    print("Database initialized.")


def seed_test_data():
    """
    Seed the DB with the 8 ambulance bins from testInventory.json.
    Only runs if the items table is empty.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM items")
        if cur.fetchone()[0] > 0:
            return  # already seeded

        items = [
            (1, "Oxygen Masks",          45.0),
            (2, "Child Airway Adjuncts", 32.0),
            (3, "Bandages",              18.0),
            (4, "Gauze Rolls",           28.0),
            (5, "Adult Airway Adjuncts", 38.0),
            (6, "Oxygen Tubing",         52.0),
            (7, "Respiratory Masks",     40.0),
            (8, "Burn Sheets",           85.0),
        ]
        containers = [
            (0, 1, 5,  5),
            (1, 2, 5,  5),
            (2, 3, 20, 17),
            (3, 4, 5,  5),
            (4, 5, 5,  5),
            (5, 6, 5,  5),
            (6, 7, 5,  5),
            (7, 8, 20, 16),
        ]
        calibrations = [
            (0, 125.0, 1.0, 3.0, "round"),
            (1, 118.0, 0.99, 2.5, "round"),
            (2, 140.0, 1.01, 2.0, "round"),
            (3, 130.0, 1.0, 3.0, "round"),
            (4, 122.0, 0.98, 2.5, "round"),
            (5, 135.0, 1.0, 2.0, "round"),
            (6, 128.0, 1.02, 3.0, "round"),
            (7, 145.0, 1.0, 2.5, "round"),
        ]

        cur.executemany("INSERT INTO items (item_id, item_name, item_weight) VALUES (?, ?, ?)", items)
        cur.executemany("INSERT INTO containers (container_id, item_id, needed_stock, current_stock) VALUES (?, ?, ?, ?)", containers)
        cur.executemany("""INSERT INTO container_calibration
            (container_id, empty_bin_weight_g, scale_factor, min_detectable_weight_g, rounding_mode)
            VALUES (?, ?, ?, ?, ?)""", calibrations)

        # Seed some sensor events so the history sparklines have data
        import random
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        events = []
        for c_id, item_id, needed, current in containers:
            stock = current
            item_weight = next(i[2] for i in items if i[0] == item_id)
            for t in range(96, -1, -1):
                ts = (now - timedelta(minutes=t * 5)).strftime("%Y-%m-%d %H:%M:%S")
                # Simulate occasional stock changes
                if t == 60 and c_id == 2:
                    stock = max(0, stock - 3)
                if t == 40 and c_id == 7:
                    stock = max(0, stock - 4)
                if t == 20 and c_id == 0:
                    stock = max(0, stock - 2)

                raw_weight = stock * item_weight + 125 + random.uniform(-2, 2)
                decision = random.choice(["accepted"] * 5 + ["deferred_unstable"])
                net = round(raw_weight - 125, 2) if decision == "accepted" else None
                comp = stock if decision == "accepted" else None

                events.append((
                    c_id, round(raw_weight, 2), net, comp,
                    "ok", decision, None, ts
                ))

        cur.executemany("""INSERT INTO sensor_events
            (container_id, raw_weight_g, net_weight_g, computed_stock,
             sensor_status, decision, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", events)

        conn.commit()
        print("Test data seeded: 8 items, 8 containers, calibrations, ~776 sensor events.")


# ── Routes ───────────────────────────────────────────────────────────────────

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
            cur.execute("""
                SELECT container_id, empty_bin_weight_g, scale_factor,
                       min_detectable_weight_g, rounding_mode
                FROM container_calibration WHERE container_id = ?
            """, (cid,))
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
                FROM sensor_events
                WHERE container_id = ?
                ORDER BY created_at DESC
                LIMIT ?
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
            return jsonify({"container_id": cid, "level": level, "current_stock": current, "needed_stock": needed})
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
            cur.execute("UPDATE containers SET current_stock = ? WHERE container_id = ?", (new_stock, cid))
            conn.commit()
            return jsonify({"container_id": cid, "current_stock": new_stock})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>/tare", methods=["POST"])
def api_tare(cid):
    """
    Tries to send a CAN tare command. If python-can isn't installed
    or the CAN bus isn't up, it just returns an error gracefully.
    """
    try:
        import can
        bus = can.interface.Bus(channel='can0', bustype='socketcan', bitrate=500000)
        data = [cid, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        msg = can.Message(arbitration_id=0x200 + cid, data=data, is_extended_id=False)
        bus.send(msg)
        bus.shutdown()
        return jsonify({"status": "tare_sent", "container_id": cid})
    except Exception as e:
        return jsonify({"status": "tare_failed", "error": str(e)}), 500


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    database_init()
    print("\n  AIM Dashboard running at http://localhost:3000\n")
    app.run(host="0.0.0.0", port=3000, debug=False)