"""
AIM — Clean Script
===================
Removes generated files from the project tree.

Default (safe) clean:
    pdm run clean
    Removes __pycache__/ directories and *.pyc files.

Deep clean (also removes runtime data):
    pdm run clean --deep
    Also removes inventory.db and logs/.
    Use with caution — this deletes all stored inventory data and logs.
"""

# ═════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═════════════════════════════════════════════════════════════════════════════

import sys
import shutil
from pathlib import Path

# ═════════════════════════════════════════════════════════════════════════════
# CONFIG
# ═════════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent

# Directories never descended into during the walk
PRUNE_DIRS = {".venv", ".git", ".pdm-build", "node_modules"}

# ═════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════════════

BOLD  = "\033[1m"
RED   = "\033[31m"
GREEN = "\033[32m"
CYAN  = "\033[36m"
DIM   = "\033[2m"
RESET = "\033[0m"

if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7
        )
    except Exception:
        BOLD = RED = GREEN = CYAN = DIM = RESET = ""


def remove(path: Path) -> None:
    """Remove a file or directory tree and print what was removed."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"  {RED}removed{RESET}  {DIM}{path.relative_to(PROJECT_ROOT)}{RESET}")
    except Exception as e:
        print(f"  {RED}failed{RESET}   {path.relative_to(PROJECT_ROOT)}: {e}")


def walk(root: Path):
    """Yield all paths under root, skipping pruned directories."""
    for child in sorted(root.iterdir()):
        if child.name in PRUNE_DIRS:
            continue
        yield child
        if child.is_dir():
            yield from walk(child)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main() -> None:
    deep = "--deep" in sys.argv

    print()
    print(f"{CYAN}{BOLD}AIM — Clean{RESET}")
    if deep:
        print(f"  {RED}Deep clean:{RESET} removing build artifacts, database, and logs")
    else:
        print(f"  Removing build artifacts  "
              f"{DIM}(pass --deep to also remove database and logs){RESET}")
    print()

    removed = 0

    # ── Build artifacts (always) ─────────────────────────────────────────────
    for path in walk(PROJECT_ROOT):
        if path.name == "__pycache__" and path.is_dir():
            remove(path)
            removed += 1
        elif path.suffix in {".pyc", ".pyo"} and path.is_file():
            remove(path)
            removed += 1

    # ── Runtime data (--deep only) ────────────────────────────────────────────
    if deep:
        db = PROJECT_ROOT / "inventory.db"
        if db.exists():
            remove(db)
            removed += 1

        logs = PROJECT_ROOT / "logs"
        if logs.exists():
            remove(logs)
            removed += 1

    print()
    if removed == 0:
        print(f"  {GREEN}Nothing to clean.{RESET}")
    else:
        print(f"  {GREEN}Done.{RESET} Removed {removed} item(s).")
    print()


if __name__ == "__main__":
    main()
