"""
aim_central.view
================
User-facing presentation layer.

This package sits at the top of the dependency stack. It imports from
driver/ and shared/ but nothing in driver/ or logic/ imports from here.

Modules
-------
flask_gui.py
    Flask application and REST API. Serves the touch-screen dashboard
    (dashboard.html) and exposes the JSON endpoints consumed by
    dashboard.js. Also hosts the /api/stream SSE endpoint, which holds
    open long-lived HTTP connections and pushes inventory updates to
    the browser whenever the database changes. Includes GET/POST
    /api/geofence for toggling the GPS geofence at runtime.

dashboard.html / dashboard.css / dashboard.js
    Single-page touch UI. Communicates exclusively with flask_gui.py
    over REST and SSE — it has no direct access to the database or CAN
    bus. The header includes a FENCE ON/OFF toggle button that calls
    /api/geofence.

Architecture note
-----------------
browser → dashboard.js → flask_gui.py → database_operations (driver/)
                              ↑                ↑
                    SSE via shared/events.py   gps_fence (logic/)
"""
