"""
aim_central.driver
==================
Low-level hardware and data access.

This package sits at the bottom of the dependency stack — nothing here
imports from logic/ or view/. All other packages depend on this one,
not the other way around.

Modules
-------
can_driver.py
    Wraps python-can to provide a SocketCAN interface for the STM32↔Pi
    CAN bus. Handles connecting, receiving frames, and parsing the
    bin_id + weight payload sent by the STM32.

database_operations.py
    SQLite access layer. Owns the database schema, all read/write
    queries for containers, items, calibration, and sensor events.
    Calls publish_push_event (from shared/events) after any write that
    should update the dashboard in real time.

Architecture note
-----------------
STM32 → CAN bus → can_driver → can_bridge (logic/) → database_operations → SQLite
"""
