"""
AIM — CAN Driver
================
Low-level SocketCAN interface for the STM32↔Pi CAN bus.
Wraps python-can, parses incoming weight frames, and exposes a
context-manager-friendly Bus handle.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import struct
import logging

# ═════════════════════════════════════════════════════════════════════════════
# CAN PROTOCOL CONSTANTS
# ═════════════════════════════════════════════════════════════════════════════

# CAN arbitration IDs
STM32_TO_PI_ID = 0x100
PI_TO_STM32_ID = 0x200

# Sensor status codes (byte 0 of outgoing frame)
STATUS_OK        = 0x00
STATUS_ERROR     = 0x01
STATUS_NOT_TARED = 0x02

# Tare result flags
TARE_NONE    = 0x00
TARE_SUCCESS = 0x01
TARE_FAIL    = 0x02

# LED state codes
LED_OFF    = 0x00
LED_GREEN  = 0x01
LED_YELLOW = 0x02
LED_RED    = 0x03

# Buzzer codes
BUZZER_OFF = 0x00
BUZZER_ON  = 0x01

# Human-readable mappings
STATUS_MAP = {STATUS_OK: "ok", STATUS_ERROR: "error", STATUS_NOT_TARED: "not_tared"}
TARE_MAP   = {TARE_NONE: "none", TARE_SUCCESS: "success", TARE_FAIL: "fail"}

# ═════════════════════════════════════════════════════════════════════════════
# CAN DRIVER
# ═════════════════════════════════════════════════════════════════════════════

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
        if len(msg.data) < 5:
            return None
        bin_id = msg.data[0]
        weight_g = struct.unpack('f', bytes(msg.data[1:5]))[0]
        return {
            "bin_id": bin_id,
            "weight_g": round(weight_g, 2),
        }

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
