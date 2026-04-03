"""
AIM Dashboard — Entry Point
============================
Initialises the database, seeds default bins, launches the CAN bridge
background thread, then starts the Flask dashboard server.

The CAN bridge runs in a daemon thread. If the CAN bus isn't available
(no MCP2515, no can0 interface), the dashboard still works — you just
won't get live sensor data.

Usage:
    pip install flask python-can
    python -m aim_central.main
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import logging

from aim_central.shared.config import (
    CAN_CHANNEL, CAN_BITRATE, FLASK_PORT,
    GPS_PORT, GPS_BAUDRATE, GPS_FENCE_LAT, GPS_FENCE_LON, GPS_FENCE_RADIUS_M,
)
from aim_central.shared.logging import setup_logging
from aim_central.driver.database_operations import get_db, database_init, seed_geofence_settings, get_setting
from aim_central.logic.can_bridge import start_can_bridge
from aim_central.logic.gps_fence import start_gps_fence
from aim_central.view.flask_gui import app

setup_logging()
logger = logging.getLogger("AIM")

# ═════════════════════════════════════════════════════════════════════════════
# SEED DATA
# ═════════════════════════════════════════════════════════════════════════════

def seed_containers():
    """
    Seed bins and items on startup. Only inserts if not already present
    (INSERT OR IGNORE), so it's safe to run every time.

    ── TO ADD MORE BINS ──────────────────────────────────────────────
    Just add a tuple to each list:
      ITEMS:       (item_id, "Item Name", weight_per_unit_in_grams)
      CONTAINERS:  (container_id, item_id, needed_stock, starting_stock)
      CALIBRATION: (container_id, empty_bin_weight_g, scale_factor,
                    min_detectable_g, "round"|"floor"|"ceil")
    The container_id must match the bin_id your STM32 sends in byte 0.
    """
    ITEMS = [
        (1, "Surgilube", 4.1),
        # (2, "Bandages", 18.0),
        # (3, "Gauze Rolls", 28.0),
        # ... uncomment or add more as you connect bins
    ]

    CONTAINERS = [
        # (container_id, item_id, needed_stock, current_stock)
        (1, 1, 4, 0),
        # (2, 2, 20, 0),
        # (3, 3, 5, 0),
    ]

    CALIBRATIONS = [
        # (container_id, empty_bin_weight_g, scale_factor, min_detectable_g, rounding_mode)
        (1, 55.3, 1.0, 2.0, "round"),
        # (2, 0.0, 1.0, 2.0, "round"),
        # (3, 0.0, 1.0, 2.0, "round"),
    ]

    try:
        with get_db() as conn:
            cur = conn.cursor()
            for item in ITEMS:
                cur.execute("INSERT OR IGNORE INTO items (item_id, item_name, item_weight) VALUES (?, ?, ?)", item)
            for container in CONTAINERS:
                cur.execute("INSERT OR IGNORE INTO containers (container_id, item_id, needed_stock, current_stock) VALUES (?, ?, ?, ?)", container)
            for cal in CALIBRATIONS:
                cur.execute("""INSERT OR IGNORE INTO container_calibration
                    (container_id, empty_bin_weight_g, scale_factor, min_detectable_weight_g, rounding_mode)
                    VALUES (?, ?, ?, ?, ?)""", cal)
            conn.commit()
        logger.info("Containers seeded: %d items, %d bins", len(ITEMS), len(CONTAINERS))
    except Exception as e:
        logger.error("Failed to seed containers: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    database_init()
    seed_geofence_settings(GPS_FENCE_LAT, GPS_FENCE_LON, GPS_FENCE_RADIUS_M)
    seed_containers()
    start_can_bridge(can_channel=CAN_CHANNEL, bitrate=CAN_BITRATE)
    start_gps_fence(
        port=GPS_PORT,
        baudrate=GPS_BAUDRATE,
        center_lat=float(get_setting("gps_fence_lat", str(GPS_FENCE_LAT))),
        center_lon=float(get_setting("gps_fence_lon", str(GPS_FENCE_LON))),
        radius_m=float(get_setting("gps_fence_radius_m", str(GPS_FENCE_RADIUS_M))),
    )

    print()
    print("  ┌─────────────────────────────────────────────┐")
    print("  │  AIM Dashboard running on port 3000         │")
    print("  │  Open http://localhost:3000 on touchscreen   │")
    print("  │                                             │")
    print("  │  CAN bridge runs in background thread       │")
    print("  │  GPS fence runs in background thread        │")
    print("  └─────────────────────────────────────────────┘")
    print()

    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, threaded=True)
