"""
AIM — GPS Geofence
==================
Reads NMEA sentences from a USB GPS module (/dev/ttyACM0), parses
$GNGGA fixes, and controls the display based on proximity to Hayden
Hall at Northeastern University.

  • Inside geofence  → display ON
  • Outside geofence → display OFF
  • No GPS fix yet   → display stays in its current state (safe default)

Display control uses vcgencmd (Raspberry Pi) with an xset DPMS fallback
for other Linux desktops.

Runs as a daemon thread launched from main.py via start_gps_fence().
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import math
import time
import threading
import logging
import subprocess

try:
    import serial
except ImportError:
    serial = None  # type: ignore[assignment]

# ═════════════════════════════════════════════════════════════════════════════
# GEOFENCE DEFAULTS  (overridden via config.py / .env)
# ═════════════════════════════════════════════════════════════════════════════

# Hayden Hall, Northeastern University, Boston MA
HAYDEN_HALL_LAT: float = 42.3396
HAYDEN_HALL_LON: float = -71.0882
GEOFENCE_RADIUS_M: float = 200.0  # metres

# ═════════════════════════════════════════════════════════════════════════════
# RUNTIME ENABLE FLAG  (toggled via the UI)
# ═════════════════════════════════════════════════════════════════════════════

_fence_enabled: bool = True
_fence_instance: "GPSFence | None" = None


def get_fence_enabled() -> bool:
    """Return whether the geofence is currently active."""
    return _fence_enabled


def update_fence_config(lat: float, lon: float, radius_m: float) -> None:
    """Update the running fence thread's center coordinates and radius in place."""
    global _fence_instance
    if _fence_instance is not None:
        _fence_instance.center_lat = lat
        _fence_instance.center_lon = lon
        _fence_instance.radius_m = radius_m
        logging.getLogger("GPSFence").info(
            "Geofence config updated — centre=(%.6f, %.6f)  r=%.0fm", lat, lon, radius_m
        )


def set_fence_enabled(enabled: bool) -> None:
    """
    Enable or disable the geofence at runtime.

    Disabling immediately turns the display on so the screen is restored
    without waiting for the next GPS fix.
    """
    global _fence_enabled
    prev = _fence_enabled
    _fence_enabled = enabled
    if not enabled and prev:
        _set_display(True)
        logging.getLogger("GPSFence").info("Geofence disabled — display forced ON.")


# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in metres between two lat/lon points."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2.0 * R * math.asin(math.sqrt(a))


def _parse_gngga(sentence: str):
    """
    Parse a $GNGGA NMEA sentence.

    Returns (lat_deg, lon_deg) as floats, or (None, None) when there is
    no fix or the sentence is malformed.
    """
    try:
        parts = sentence.split(",")
        if len(parts) < 7:
            return None, None
        fix_quality = int(parts[6]) if parts[6] else 0
        if fix_quality == 0 or parts[2] == "" or parts[4] == "":
            return None, None

        lat_raw = float(parts[2])
        lat_dir = parts[3]
        lon_raw = float(parts[4])
        lon_dir = parts[5]

        # NMEA encoding: DDMM.MMMMM — convert to decimal degrees
        lat = int(lat_raw / 100) + (lat_raw % 100) / 60.0
        lon = int(lon_raw / 100) + (lon_raw % 100) / 60.0
        if lat_dir == "S":
            lat = -lat
        if lon_dir == "W":
            lon = -lon

        return lat, lon
    except (ValueError, IndexError):
        return None, None


def _set_display(on: bool) -> None:
    """
    Turn the connected display on or off.

    Tries vcgencmd first (Raspberry Pi), then falls back to xset DPMS
    (X11 desktop). Logs but never raises on failure so the fence loop
    keeps running even if display control is unavailable.
    """
    logger = logging.getLogger("GPSFence")
    state = "1" if on else "0"

    # Raspberry Pi: vcgencmd display_power 0|1
    try:
        subprocess.run(
            ["vcgencmd", "display_power", state],
            check=True, capture_output=True, timeout=3,
        )
        return
    except FileNotFoundError:
        pass  # not a Pi — try fallback
    except subprocess.CalledProcessError as e:
        logger.warning("vcgencmd display_power failed: %s", e.stderr.decode().strip())

    # Fallback: X11 DPMS
    try:
        if on:
            subprocess.run(["xset", "dpms", "force", "on"],
                           check=False, capture_output=True, timeout=3)
        else:
            subprocess.run(["xset", "dpms", "force", "off"],
                           check=False, capture_output=True, timeout=3)
    except Exception as e:
        logger.warning("xset DPMS fallback failed: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# GPS GEOFENCE
# ═════════════════════════════════════════════════════════════════════════════

class GPSFence:
    """
    Monitors GPS position and toggles the display when the device
    crosses the geofence boundary.
    """

    def __init__(
        self,
        port: str = "/dev/ttyACM0",
        baudrate: int = 9600,
        center_lat: float = HAYDEN_HALL_LAT,
        center_lon: float = HAYDEN_HALL_LON,
        radius_m: float = GEOFENCE_RADIUS_M,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.center_lat = center_lat
        self.center_lon = center_lon
        self.radius_m = radius_m
        self._display_on: bool = True  # assume display starts on
        self.logger = logging.getLogger("GPSFence")

    # ─────────────────────────────────────────────────────────────────────────

    def _apply_display_state(self, should_be_on: bool) -> None:
        """Call _set_display only when the desired state differs from current.

        Does nothing (and syncs tracking state to ON) while the fence is
        disabled, so re-enabling picks up correctly on the next GPS fix.
        """
        if not _fence_enabled:
            self._display_on = True  # display was restored when fence was disabled
            return
        if should_be_on == self._display_on:
            return
        _set_display(should_be_on)
        self._display_on = should_be_on
        label = "ON  (inside geofence)" if should_be_on else "OFF (outside geofence)"
        self.logger.info("Display → %s", label)

    # ─────────────────────────────────────────────────────────────────────────

    def run_forever(self) -> None:
        """
        Main loop: open the serial port, read NMEA lines, parse GGA fixes,
        and update display state. Reconnects automatically on serial errors.
        """
        if serial is None:
            self.logger.warning(
                "pyserial not installed — GPS fence disabled. "
                "Install with: pip install pyserial"
            )
            return

        self.logger.info(
            "GPS fence started on %s  centre=(%.6f, %.6f)  r=%.0fm",
            self.port, self.center_lat, self.center_lon, self.radius_m,
        )

        while True:
            try:
                with serial.Serial(self.port, self.baudrate, timeout=2) as ser:
                    self.logger.info("GPS serial port open.")
                    while True:
                        raw = ser.readline()
                        line = raw.decode("ascii", errors="replace").strip()

                        if not line.startswith("$GNGGA"):
                            continue

                        lat, lon = _parse_gngga(line)
                        if lat is None:
                            self.logger.debug("No GPS fix — display state unchanged.")
                            continue

                        dist = _haversine_m(lat, lon, self.center_lat, self.center_lon)
                        inside = dist <= self.radius_m
                        self.logger.debug(
                            "Fix (%.6f, %.6f)  dist=%.0fm  inside=%s",
                            lat, lon, dist, inside,
                        )
                        self._apply_display_state(inside)

            except serial.SerialException as e:
                self.logger.error("GPS serial error: %s — retrying in 10 s.", e)
                time.sleep(10)
            except Exception as e:
                self.logger.error("GPS fence error: %s — retrying in 10 s.", e)
                time.sleep(10)


# ═════════════════════════════════════════════════════════════════════════════
# BACKGROUND GPS THREAD
# ═════════════════════════════════════════════════════════════════════════════

def start_gps_fence(
    port: str = "/dev/ttyACM0",
    baudrate: int = 9600,
    center_lat: float = HAYDEN_HALL_LAT,
    center_lon: float = HAYDEN_HALL_LON,
    radius_m: float = GEOFENCE_RADIUS_M,
) -> None:
    """
    Start the GPS geofence monitor in a daemon thread.

    If pyserial isn't installed or the port can't be opened, a warning
    is logged and the thread exits — the rest of the application keeps
    running normally.
    """
    logger = logging.getLogger("AIM")
    global _fence_instance
    fence = GPSFence(
        port=port,
        baudrate=baudrate,
        center_lat=center_lat,
        center_lon=center_lon,
        radius_m=radius_m,
    )
    _fence_instance = fence
    t = threading.Thread(target=fence.run_forever, daemon=True, name="GPS-Fence")
    t.start()
    logger.info("GPS fence thread launched.")
