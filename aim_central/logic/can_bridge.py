"""
AIM — CAN → Database Bridge
============================
Receives CAN frames from the STM32 via CANDriver, applies a stability
window to raw weight readings, and writes accepted stock updates to the
database. Runs as a daemon thread launched from main.py.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import time
import threading
import logging
from collections import defaultdict, deque

from aim_central.driver.can_driver import CANDriver
from aim_central.driver.database_operations import (
    record_sensor_event,
    update_stock_from_weight,
    get_stock,
)

# ═════════════════════════════════════════════════════════════════════════════
# CAN → DATABASE BRIDGE
# Simple: receive CAN message → record raw weight → update stock count
# ═════════════════════════════════════════════════════════════════════════════

class CanDatabaseBridge:
    def __init__(self, can_channel='can0', bitrate=500000,
                 stability_window=2, stability_tolerance_g=2.0):
        self.driver = CANDriver(channel=can_channel, bitrate=bitrate)
        self.stability_window = max(1, int(stability_window))
        self.stability_tolerance_g = float(stability_tolerance_g)
        self._weight_windows = defaultdict(lambda: deque(maxlen=self.stability_window))
        self.logger = logging.getLogger("CanDatabaseBridge")

    def _stable_weight(self, bin_id, latest_weight_g):
        """Average the last N readings if they're within tolerance."""
        window = self._weight_windows[bin_id]
        window.append(float(latest_weight_g))
        if len(window) < self.stability_window:
            return None
        spread = max(window) - min(window)
        if spread > self.stability_tolerance_g:
            return None
        return sum(window) / len(window)

    def process_one_message(self, timeout=1.0):
        msg = self.driver.receive(timeout=timeout)
        if msg is None:
            return False

        bin_id = msg["bin_id"]
        weight_g = msg["weight_g"]

        # Always record the raw weight
        record_sensor_event(
            container_id=bin_id,
            raw_weight_g=weight_g,
            sensor_status="ok",
            decision="received",
            notify_ui=False,
        )

        # Check stability window before updating stock
        stable_weight_g = self._stable_weight(bin_id, weight_g)
        if stable_weight_g is not None:
            updated = update_stock_from_weight(bin_id, stable_weight_g)
            if updated:
                record_sensor_event(
                    container_id=bin_id,
                    raw_weight_g=weight_g,
                    sensor_status="ok",
                    decision="accepted",
                    net_weight_g=stable_weight_g,
                    computed_stock=get_stock(bin_id),
                    note=f"stable avg over {self.stability_window} readings",
                )

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

def start_can_bridge(can_channel='can0', bitrate=500000):
    """
    Start the CAN bridge in a daemon thread.
    If python-can isn't installed or can0 isn't up, logs a warning and exits.
    The Flask server keeps running either way.
    """
    logger = logging.getLogger("AIM")

    def _run():
        try:
            import can  # noqa: F401
        except ImportError:
            logger.warning("python-can not installed — CAN bridge disabled. "
                           "Install with: pip install python-can")
            return
        try:
            bridge = CanDatabaseBridge(can_channel=can_channel, bitrate=bitrate)
            bridge.run_forever()
        except Exception as e:
            logger.warning("CAN bridge failed to start: %s", e)
            logger.warning("Dashboard will run without live sensor data.")

    t = threading.Thread(target=_run, daemon=True, name="CAN-Bridge")
    t.start()
    logger.info("CAN bridge thread launched.")
