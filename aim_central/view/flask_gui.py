"""
AIM — Flask Dashboard GUI
==========================
Touch-optimised web dashboard served on port 3000.
Exposes the REST API consumed by dashboard.js and the SSE stream
that pushes live inventory updates to connected browsers.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import os
import json
import sqlite3
import logging

from flask import Flask, jsonify, send_file, request, Response

from aim_central.shared.config import FLASK_PORT
from aim_central.shared import events as _events
from aim_central.shared.events import publish_push_event
from aim_central.logic.gps_fence import get_fence_enabled, set_fence_enabled, update_fence_config
from aim_central.driver.database_operations import (
    get_db,
    record_sensor_event,
    get_container_calibration,
    get_setting,
    set_setting,
)

# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════

VIEW_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_PATH = os.path.join(VIEW_DIR, "dashboard.html")

logger = logging.getLogger("AIM.Flask")

# ═════════════════════════════════════════════════════════════════════════════
# FLASK APP
# ═════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)


# ─── Static dashboard ────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_file(DASHBOARD_PATH)

@app.route("/dashboard.css")
def dashboard_css():
    return send_file(os.path.join(VIEW_DIR, "dashboard.css"))

@app.route("/dashboard.js")
def dashboard_js():
    return send_file(os.path.join(VIEW_DIR, "dashboard.js"))


# ─── Container list & detail ─────────────────────────────────────────────────

@app.route("/api/containers")
def api_containers():
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT c.container_id, c.item_id, i.item_name, i.item_weight,
                       c.needed_stock, c.current_stock,
                       rw.raw_weight_g,
                       rw.created_at AS weight_timestamp
                FROM containers c
                JOIN items i ON c.item_id = i.item_id
                LEFT JOIN (
                    SELECT s.container_id, s.raw_weight_g, s.created_at
                    FROM sensor_events s
                    INNER JOIN (
                        SELECT container_id, MAX(event_id) AS max_event_id
                        FROM sensor_events
                        GROUP BY container_id
                    ) latest ON latest.max_event_id = s.event_id
                ) rw ON rw.container_id = c.container_id
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


# ─── SSE stream ──────────────────────────────────────────────────────────────

@app.route("/api/stream")
def api_stream():
    def event_stream():
        # Tell clients to retry quickly if connection drops.
        yield "retry: 1000\n\n"

        with _events._push_condition:
            current_version = _events._push_version
            initial_payload = dict(_events._push_payload)
        yield f"event: inventory\ndata: {json.dumps(initial_payload)}\n\n"

        while True:
            with _events._push_condition:
                has_update = _events._push_condition.wait_for(
                    lambda: _events._push_version != current_version, timeout=15.0
                )
                if has_update:
                    current_version = _events._push_version
                    payload = dict(_events._push_payload)
                else:
                    payload = None

            if payload is None:
                # Keep-alive comment so intermediaries don't close idle stream.
                yield ": keepalive\n\n"
            else:
                yield f"event: inventory\ndata: {json.dumps(payload)}\n\n"

    return Response(event_stream(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


# ─── Calibration ─────────────────────────────────────────────────────────────

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


@app.route("/api/calibration/<int:cid>", methods=["POST"])
def api_update_calibration(cid):
    """
    Update calibration params from the UI.
    POST { "scale_factor": 1.0, "min_detectable_weight_g": 2.0, "rounding_mode": "round" }
    """
    data = request.get_json(force=True)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM container_calibration WHERE container_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "no calibration for this bin"}), 404

            sf = float(data.get("scale_factor", row["scale_factor"]))
            md = float(data.get("min_detectable_weight_g", row["min_detectable_weight_g"]))
            rm = data.get("rounding_mode", row["rounding_mode"])

            cur.execute("""UPDATE container_calibration
                SET scale_factor = ?, min_detectable_weight_g = ?, rounding_mode = ?
                WHERE container_id = ?""", (sf, md, rm, cid))
            conn.commit()
            publish_push_event("calibration", cid)
            return jsonify({"status": "ok", "container_id": cid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Events & readings ───────────────────────────────────────────────────────

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


@app.route("/api/raw-weight/<int:cid>")
def api_raw_weight(cid):
    """Get the latest raw weight reading for a container."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT raw_weight_g, created_at FROM sensor_events
                WHERE container_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"container_id": cid, "raw_weight_g": None})
            return jsonify({"container_id": cid,
                            "raw_weight_g": row["raw_weight_g"],
                            "timestamp": row["created_at"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Stock adjustments ───────────────────────────────────────────────────────

@app.route("/api/containers/<int:cid>/adjust", methods=["POST"])
def api_adjust_stock(cid):
    data = request.get_json(force=True)
    change = data.get("change", 0)
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("""SELECT c.current_stock, c.item_id,
                                  cal.empty_bin_weight_g,
                                  se.raw_weight_g
                           FROM containers c
                           LEFT JOIN container_calibration cal ON cal.container_id = c.container_id
                           LEFT JOIN (
                               SELECT container_id, raw_weight_g FROM sensor_events
                               WHERE container_id = ?
                               ORDER BY event_id DESC LIMIT 1
                           ) se ON se.container_id = c.container_id
                           WHERE c.container_id = ?""", (cid, cid))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "not found"}), 404
            new_stock = row["current_stock"] + change
            if new_stock < 0:
                return jsonify({"error": "cannot go below zero"}), 400
            cur.execute("UPDATE containers SET current_stock = ? WHERE container_id = ?",
                        (new_stock, cid))

            # Auto-recalculate item weight from current scale reading when stock > 0
            recalculated_weight = None
            raw = row["raw_weight_g"]
            empty = row["empty_bin_weight_g"]
            if new_stock > 0 and raw is not None and empty is not None:
                item_weight = (raw - empty) / new_stock
                if item_weight > 0:
                    min_detectable = item_weight * 0.7
                    cur.execute("UPDATE items SET item_weight = ? WHERE item_id = ?",
                                (item_weight, row["item_id"]))
                    cur.execute("""UPDATE container_calibration
                                   SET min_detectable_weight_g = ?
                                   WHERE container_id = ?""", (min_detectable, cid))
                    recalculated_weight = round(item_weight, 2)
                    logger.info(
                        "Bin %d: item_weight recalculated to %.2fg, min_detectable=%.2fg",
                        cid, item_weight, min_detectable,
                    )

            conn.commit()
            publish_push_event("stock", cid)
            resp = {"container_id": cid, "current_stock": new_stock}
            if recalculated_weight is not None:
                resp["item_weight"] = recalculated_weight
            return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>/tare", methods=["POST"])
def api_tare(cid):
    """
    Pi-side tare: grabs the most recent raw weight reading from sensor_events
    and stores it as empty_bin_weight_g in container_calibration.

    This means the Pi owns the zero offset — if the ESP32 reboots, the Pi
    still knows what "empty" looks like and keeps interpreting weights correctly.
    """
    try:
        with get_db() as conn:
            cur = conn.cursor()

            # Get the latest raw weight for this container
            cur.execute("""
                SELECT raw_weight_g FROM sensor_events
                WHERE container_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (cid,))
            row = cur.fetchone()

            if not row:
                return jsonify({"status": "tare_failed",
                                "error": "no sensor readings yet for this bin"}), 400

            tare_weight = row["raw_weight_g"]

            # Upsert the calibration row with the new empty_bin_weight_g
            cur.execute("""
                INSERT INTO container_calibration
                    (container_id, empty_bin_weight_g, scale_factor,
                     min_detectable_weight_g, rounding_mode)
                VALUES (?, ?, 1.0, 2.0, 'round')
                ON CONFLICT(container_id) DO UPDATE SET
                    empty_bin_weight_g = ?
            """, (cid, tare_weight, tare_weight))
            conn.commit()

            # Log the tare as a sensor event for the history
            record_sensor_event(cid, tare_weight, "ok", "tare_confirmed",
                                note=f"pi-side tare — empty_bin_weight_g set to {tare_weight:.2f}g")

            logger.info("Tare: bin %d empty_bin_weight_g = %.2f g", cid, tare_weight)
            publish_push_event("calibration", cid)
            return jsonify({"status": "tare_ok", "container_id": cid,
                            "empty_bin_weight_g": round(tare_weight, 2)})

    except Exception as e:
        return jsonify({"status": "tare_failed", "error": str(e)}), 500


# ─── Bin management (add / edit / delete) ────────────────────────────────────

@app.route("/api/containers/<int:cid>/config", methods=["POST"])
def api_update_config(cid):
    """
    Update item_name, item_weight, and needed_stock for a container.
    POST { "item_name": "Bandages", "item_weight": 18.0, "needed_stock": 20 }
    """
    data = request.get_json(force=True)
    item_name = data.get("item_name", "").strip()
    item_weight = data.get("item_weight")
    needed_stock = data.get("needed_stock")

    if not item_name:
        return jsonify({"error": "item_name required"}), 400
    if item_weight is None or float(item_weight) <= 0:
        return jsonify({"error": "item_weight must be > 0"}), 400
    if needed_stock is None or int(needed_stock) < 1:
        return jsonify({"error": "needed_stock must be >= 1"}), 400

    try:
        with get_db() as conn:
            cur = conn.cursor()

            # Check container exists
            cur.execute("SELECT item_id FROM containers WHERE container_id = ?", (cid,))
            row = cur.fetchone()
            if not row:
                return jsonify({"error": "container not found"}), 404

            item_id = row["item_id"]

            # Update item name and weight
            cur.execute("UPDATE items SET item_name = ?, item_weight = ? WHERE item_id = ?",
                        (item_name, float(item_weight), item_id))

            # Update needed_stock
            cur.execute("UPDATE containers SET needed_stock = ? WHERE container_id = ?",
                        (int(needed_stock), cid))

            conn.commit()
            publish_push_event("config", cid)
            return jsonify({"status": "ok", "container_id": cid})
    except sqlite3.IntegrityError:
        return jsonify({"error": "item name already exists on another bin"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/add", methods=["POST"])
def api_add_container():
    """
    Add a new bin.
    POST { "container_id": 2, "item_name": "Bandages", "item_weight": 18.0, "needed_stock": 20 }
    """
    data = request.get_json(force=True)
    cid = data.get("container_id")
    item_name = data.get("item_name", "").strip()
    item_weight = data.get("item_weight")
    needed_stock = data.get("needed_stock")

    if cid is None:
        return jsonify({"error": "container_id required"}), 400
    if not item_name:
        return jsonify({"error": "item_name required"}), 400
    if item_weight is None or float(item_weight) <= 0:
        return jsonify({"error": "item_weight must be > 0"}), 400
    if needed_stock is None or int(needed_stock) < 1:
        return jsonify({"error": "needed_stock must be >= 1"}), 400

    try:
        with get_db() as conn:
            cur = conn.cursor()

            # Check if container_id already exists
            cur.execute("SELECT container_id FROM containers WHERE container_id = ?", (int(cid),))
            if cur.fetchone():
                return jsonify({"error": f"bin {cid} already exists"}), 400

            # Insert or find item
            cur.execute("SELECT item_id FROM items WHERE item_name = ?", (item_name,))
            row = cur.fetchone()
            if row:
                item_id = row["item_id"]
                cur.execute("UPDATE items SET item_weight = ? WHERE item_id = ?",
                            (float(item_weight), item_id))
            else:
                cur.execute("INSERT INTO items (item_name, item_weight) VALUES (?, ?)",
                            (item_name, float(item_weight)))
                item_id = cur.lastrowid

            # Insert container
            cur.execute("INSERT INTO containers (container_id, item_id, needed_stock, current_stock) VALUES (?, ?, ?, 0)",
                        (int(cid), item_id, int(needed_stock)))

            # Insert default calibration
            cur.execute("""INSERT OR IGNORE INTO container_calibration
                (container_id, empty_bin_weight_g, scale_factor, min_detectable_weight_g, rounding_mode)
                VALUES (?, 0.0, 1.0, 2.0, 'round')""", (int(cid),))

            conn.commit()
            publish_push_event("container_added", int(cid))
            return jsonify({"status": "ok", "container_id": int(cid)})
    except sqlite3.IntegrityError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/containers/<int:cid>", methods=["DELETE"])
def api_delete_container(cid):
    """Delete a bin and its sensor events and calibration."""
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM sensor_events WHERE container_id = ?", (cid,))
            cur.execute("DELETE FROM container_calibration WHERE container_id = ?", (cid,))
            cur.execute("DELETE FROM containers WHERE container_id = ?", (cid,))
            conn.commit()
            publish_push_event("container_deleted", cid)
            return jsonify({"status": "ok", "deleted": cid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── Geofence toggle ─────────────────────────────────────────────────────────

@app.route("/api/geofence")
def api_geofence_get():
    """Return the current geofence enabled state."""
    return jsonify({"enabled": get_fence_enabled()})


@app.route("/api/geofence", methods=["POST"])
def api_geofence_set():
    """
    Enable or disable the GPS geofence.
    POST { "enabled": true|false }
    """
    data = request.get_json(force=True)
    enabled = bool(data.get("enabled", True))
    set_fence_enabled(enabled)
    logger.info("Geofence %s via UI.", "enabled" if enabled else "disabled")
    return jsonify({"enabled": get_fence_enabled()})


# ─── Geofence configuration ───────────────────────────────────────────────────

@app.route("/api/settings/geofence")
def api_geofence_config_get():
    """Return the current geofence center coordinates and radius."""
    return jsonify({
        "lat":      float(get_setting("gps_fence_lat",      "42.3396")),
        "lon":      float(get_setting("gps_fence_lon",      "-71.0882")),
        "radius_m": float(get_setting("gps_fence_radius_m", "200")),
    })


@app.route("/api/settings/geofence", methods=["POST"])
def api_geofence_config_set():
    """
    Update geofence center and radius.
    POST { "lat": 42.3396, "lon": -71.0882, "radius_m": 200 }
    Persists to DB and updates the running fence thread immediately.
    """
    data = request.get_json(force=True)
    try:
        lat      = float(data["lat"])
        lon      = float(data["lon"])
        radius_m = float(data["radius_m"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "lat, lon, and radius_m are required numbers"}), 400

    if not (-90 <= lat <= 90):
        return jsonify({"error": "lat must be between -90 and 90"}), 400
    if not (-180 <= lon <= 180):
        return jsonify({"error": "lon must be between -180 and 180"}), 400
    if radius_m <= 0:
        return jsonify({"error": "radius_m must be > 0"}), 400

    set_setting("gps_fence_lat",      str(lat))
    set_setting("gps_fence_lon",      str(lon))
    set_setting("gps_fence_radius_m", str(radius_m))
    update_fence_config(lat, lon, radius_m)

    logger.info("Geofence config updated via UI — centre=(%.6f, %.6f)  r=%.0fm", lat, lon, radius_m)
    return jsonify({"lat": lat, "lon": lon, "radius_m": radius_m})
