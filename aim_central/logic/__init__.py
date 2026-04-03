"""
aim_central.logic
=================
Business logic and data pipeline coordination.

This package sits in the middle of the dependency stack. It imports
from driver/ but is not imported by view/. It contains the rules and
processes that connect raw hardware input to stored state.

Modules
-------
can_bridge.py
    Bridges the CAN driver to the database. Receives raw weight frames
    from can_driver, applies a stability window to filter out noisy
    readings, and writes confirmed stock updates to the database via
    database_operations. Runs as a daemon thread started from main.py.

Architecture note
-----------------
can_driver (driver/) → CanDatabaseBridge → database_operations (driver/)
                            ↑
                     started by main.py
"""
