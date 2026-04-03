"""
aim_central.shared
==================
Cross-cutting infrastructure with no layer affiliation.

Code lives here when it needs to be used by more than one layer and
cannot belong to any single layer without creating an unwanted
dependency. Neither driver/, logic/, nor view/ is the right owner —
so it lives here instead.

Modules
-------
config.py
    Single source of truth for all runtime configuration. Loads .env
    from the project root on first import via python-dotenv, then
    exposes each setting as a typed constant (CAN_CHANNEL, CAN_BITRATE,
    DB_PATH, FLASK_PORT, LOG_PATH, LOG_LEVEL, etc.). All other modules
    import their config values from here. A .env.example file in the
    project root documents every available variable.

events.py
    Process-local SSE pub/sub state. Provides publish_push_event() for
    any layer that needs to notify the dashboard, and exposes the
    underlying Condition and version state for flask_gui.py to consume
    in the /api/stream endpoint. This is the only module in the project
    that both driver/ and view/ import from.

logging.py
    Configures the root logger with a console handler and a rotating
    file handler. Call setup_logging() once from main.py at startup.
    Accepts a custom log path, level, file size limit, and backup count.
    All other modules obtain their loggers via logging.getLogger() and
    inherit these handlers automatically.

Architecture note
-----------------
        shared/events.py
           ↑          ↑
driver/database_ops   view/flask_gui
"""
