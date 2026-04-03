"""
AIM — Environment Setup Script
================================
Interactively generates a .env file by walking the user through each
setting. For every variable the user is shown:
  - what the variable does
  - the recommended value for local development
  - the recommended value for production (on a Pi or other SBC)

Run via PDM from the project root:
    pdm run setup-env
"""

import os
import sys

# ═════════════════════════════════════════════════════════════════════════════
# VARIABLE DEFINITIONS
# Each entry:
#   key         - the env variable name
#   description - one sentence plain-English explanation
#   dev         - recommended value for local development
#   prod        - recommended value on a Pi / SBC
#   default     - pre-filled value shown in the prompt (usually prod)
# ═════════════════════════════════════════════════════════════════════════════

VARIABLES = [
    {
        "key":         "AIM_CAN_CHANNEL",
        "description": "The SocketCAN network interface the Pi listens on for STM32 frames.",
        "dev":         "vcan0  (virtual CAN — run: sudo modprobe vcan && sudo ip link add vcan0 type vcan && sudo ip link set vcan0 up)",
        "prod":        "can0   (physical MCP2515 interface)",
        "default":     "can0",
    },
    {
        "key":         "AIM_CAN_BITRATE",
        "description": "CAN bus speed in bits per second. Must match the value flashed onto the STM32.",
        "dev":         "500000 (match whatever the STM32 is configured with — bitrate is ignored on vcan)",
        "prod":        "500000",
        "default":     "500000",
    },
    {
        "key":         "AIM_DB_PATH",
        "description": "Path to the SQLite database file. Created automatically on first run.",
        "dev":         "inventory.db  (project root, easy to inspect or delete during testing)",
        "prod":        "/home/pi/aim/inventory.db  (persistent path outside the repo)",
        "default":     "inventory.db",
    },
    {
        "key":         "AIM_FLASK_PORT",
        "description": "TCP port the dashboard web server listens on.",
        "dev":         "3000  (open http://localhost:3000 in any browser)",
        "prod":        "3000  (the touchscreen browser points here; change if port is taken)",
        "default":     "3000",
    },
    {
        "key":         "AIM_LOG_PATH",
        "description": "Path to the rotating log file. The parent directory is created if missing.",
        "dev":         "logs/aim.log  (relative to project root)",
        "prod":        "/home/pi/aim/logs/aim.log  (persistent path, survives repo updates)",
        "default":     "logs/aim.log",
    },
    {
        "key":         "AIM_LOG_LEVEL",
        "description": "Minimum severity level written to both console and log file.",
        "dev":         "DEBUG   (verbose — shows every CAN frame and DB query)",
        "prod":        "INFO    (normal operations only; use WARNING to reduce noise further)",
        "default":     "INFO",
    },
    {
        "key":         "AIM_LOG_MAX_BYTES",
        "description": "Maximum size of a single log file in bytes before it rotates.",
        "dev":         "1000000  (1 MB)",
        "prod":        "1000000  (1 MB — keep at least 3 × this free on the SD card)",
        "default":     "1000000",
    },
    {
        "key":         "AIM_LOG_BACKUP_COUNT",
        "description": "Number of rotated log files kept on disk alongside the active one.",
        "dev":         "3",
        "prod":        "3  (total log footprint ≈ 4 × AIM_LOG_MAX_BYTES)",
        "default":     "3",
    },
]

# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

BOLD  = "\033[1m"
CYAN  = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"

# Disable colour codes on Windows terminals that don't support ANSI
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        BOLD = CYAN = GREEN = YELLOW = RESET = ""


def header(text: str) -> None:
    width = 72
    print()
    print(CYAN + "═" * width + RESET)
    print(CYAN + f"  {text}" + RESET)
    print(CYAN + "═" * width + RESET)


def ask(variable: dict) -> str:
    """Print the variable info block and prompt the user for a value."""
    print()
    print(f"{BOLD}{variable['key']}{RESET}")
    print(f"  {variable['description']}")
    print(f"  {YELLOW}Dev:{RESET}  {variable['dev']}")
    print(f"  {GREEN}Prod:{RESET} {variable['prod']}")
    prompt = f"  Enter value [{variable['default']}]: "
    try:
        value = input(prompt).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("\nSetup cancelled.")
        sys.exit(0)
    return value if value else variable["default"]


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env")

    header("AIM — Environment Setup")
    print()
    print("  This script will create a .env file at:")
    print(f"  {env_path}")
    print()
    print("  For each setting you will see the recommended values for")
    print("  development and production. Press Enter to accept the default.")

    if os.path.exists(env_path):
        print()
        print(f"{YELLOW}  Warning: {env_path} already exists.{RESET}")
        try:
            overwrite = input("  Overwrite it? [y/N]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nSetup cancelled.")
            sys.exit(0)
        if overwrite != "y":
            print("  Setup cancelled — existing .env was not modified.")
            sys.exit(0)

    header("Configuration")

    collected = {}
    for variable in VARIABLES:
        collected[variable["key"]] = ask(variable)

    # Write the .env file
    lines = [
        "# AIM Dashboard — Environment Configuration",
        "# Generated by aim_central/scripts/setup_env.py",
        "# To regenerate, run: pdm run setup-env",
        "",
        "# CAN Bus",
        f"AIM_CAN_CHANNEL={collected['AIM_CAN_CHANNEL']}",
        f"AIM_CAN_BITRATE={collected['AIM_CAN_BITRATE']}",
        "",
        "# Database",
        f"AIM_DB_PATH={collected['AIM_DB_PATH']}",
        "",
        "# Flask",
        f"AIM_FLASK_PORT={collected['AIM_FLASK_PORT']}",
        "",
        "# Logging",
        f"AIM_LOG_PATH={collected['AIM_LOG_PATH']}",
        f"AIM_LOG_LEVEL={collected['AIM_LOG_LEVEL']}",
        f"AIM_LOG_MAX_BYTES={collected['AIM_LOG_MAX_BYTES']}",
        f"AIM_LOG_BACKUP_COUNT={collected['AIM_LOG_BACKUP_COUNT']}",
    ]

    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    header("Done")
    print()
    print(f"{GREEN}  .env written to {env_path}{RESET}")
    print()
    print("  To start the dashboard:")
    print("    pdm run start")
    print()


if __name__ == "__main__":
    main()
