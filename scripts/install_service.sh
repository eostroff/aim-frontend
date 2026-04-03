#!/usr/bin/env bash
# =============================================================================
# AIM — Install Service
# =============================================================================
# Creates the `aim` system user, runtime directories, and a systemd service
# unit for the AIM dashboard.
#
# Usage:
#   sudo bash scripts/install_service.sh
#
# Run this once on the Raspberry Pi after `pdm install` and `pdm run setup-env`.
# The script is idempotent — safe to re-run if you need to update the unit file.
# =============================================================================

set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
BOLD='\033[1m'
RED='\033[31m'
GREEN='\033[32m'
YELLOW='\033[33m'
CYAN='\033[36m'
DIM='\033[2m'
RESET='\033[0m'

step()  { echo -e "  ${CYAN}${BOLD}→${RESET}  $*"; }
ok()    { echo -e "  ${GREEN}✓${RESET}  $*"; }
warn()  { echo -e "  ${YELLOW}!${RESET}  $*"; }
die()   { echo -e "  ${RED}✗${RESET}  $*" >&2; exit 1; }

# ── Root check ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "This script must be run as root:  sudo bash scripts/install_service.sh"

echo
echo -e "${CYAN}${BOLD}AIM — Install Service${RESET}"
echo

# ── Resolve install directory ─────────────────────────────────────────────
# The script lives in <project>/scripts/, so its parent is the project root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"
VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
SERVICE_FILE="/etc/systemd/system/aim.service"
SERVICE_USER="aim"

echo -e "  ${DIM}Install directory : $INSTALL_DIR${RESET}"
echo -e "  ${DIM}Python interpreter: $VENV_PYTHON${RESET}"
echo

# ── Pre-flight checks ─────────────────────────────────────────────────────
step "Checking prerequisites"

[[ -d "$INSTALL_DIR/aim_central" ]] \
    || die "aim_central/ not found under $INSTALL_DIR — are you running from the right directory?"

[[ -x "$VENV_PYTHON" ]] \
    || die ".venv not found or Python not executable. Run 'pdm install' first."

ok "Prerequisites satisfied"

# ── System user ───────────────────────────────────────────────────────────
step "Creating system user '$SERVICE_USER'"

if id "$SERVICE_USER" &>/dev/null; then
    ok "User '$SERVICE_USER' already exists — skipping"
else
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    ok "Created system user '$SERVICE_USER'"
fi

# ── Runtime directories ───────────────────────────────────────────────────
step "Creating runtime directories"

for dir in /var/lib/aim /var/log/aim; do
    if [[ -d "$dir" ]]; then
        ok "$dir already exists — skipping"
    else
        mkdir -p "$dir"
        ok "Created $dir"
    fi
    chown "$SERVICE_USER:$SERVICE_USER" "$dir"
    chmod 750 "$dir"
done

# ── Systemd unit file ─────────────────────────────────────────────────────
step "Writing $SERVICE_FILE"

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=AIM Ambulance Inventory Management Dashboard
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$INSTALL_DIR
Environment=AIM_CAN_CHANNEL=can0
Environment=AIM_CAN_BITRATE=500000
Environment=AIM_DB_PATH=/var/lib/aim/inventory.db
Environment=AIM_FLASK_PORT=3000
Environment=AIM_LOG_PATH=/var/log/aim/aim.log
Environment=AIM_LOG_LEVEL=INFO
Environment=AIM_LOG_MAX_BYTES=1000000
Environment=AIM_LOG_BACKUP_COUNT=3
ExecStart=$VENV_PYTHON -m aim_central.main
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

ok "Wrote $SERVICE_FILE"

# ── Reload systemd ────────────────────────────────────────────────────────
step "Reloading systemd daemon"
systemctl daemon-reload
ok "Daemon reloaded"

# ── Enable / start ────────────────────────────────────────────────────────
echo
read -rp "  Enable aim.service to start on boot? [Y/n] " enable_reply
enable_reply="${enable_reply:-Y}"
if [[ "$enable_reply" =~ ^[Yy]$ ]]; then
    systemctl enable aim.service
    ok "Enabled aim.service"
fi

echo
read -rp "  Start aim.service now? [Y/n] " start_reply
start_reply="${start_reply:-Y}"
if [[ "$start_reply" =~ ^[Yy]$ ]]; then
    systemctl start aim.service
    ok "Started aim.service"
    echo
    echo -e "  ${DIM}Check status : systemctl status aim${RESET}"
    echo -e "  ${DIM}Follow logs  : journalctl -u aim -f${RESET}"
    echo -e "  ${DIM}Log file     : /var/log/aim/aim.log${RESET}"
fi

echo
echo -e "  ${GREEN}${BOLD}Done.${RESET}"
echo
