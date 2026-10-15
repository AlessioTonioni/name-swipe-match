#!/usr/bin/env bash
# Template: deploy to any Linux box with systemd over SSH (e.g. a Raspberry Pi).
# Copy to deploy.sh (gitignored), set the three variables below, then:
#   ./deploy.sh --setup     first time: rsync, venv, systemd unit
#   ./deploy.sh             afterwards: rsync, restart
#   ./deploy.sh --dry-run   preview the rsync
set -e

HOST="user@my-server.local"        # SSH target
REMOTE_DIR="/home/user/name_match" # where the app lives on the server
PORT=8090

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_USER="$(ssh -G "$HOST" | awk '/^user /{print $2}')"

# Server-only state (DB) is excluded so --delete never touches it
RSYNC_OPTS=(-avz --delete --itemize-changes
  --exclude='.git' --exclude='.claude' --exclude='.venv' --exclude='__pycache__' --exclude='*.pyc'
  --exclude='data/raw' --exclude='*.db' --exclude='*.db-wal' --exclude='*.db-shm'
  --exclude='deploy.sh' --exclude='.DS_Store')

if [ "${1}" = "--dry-run" ]; then
  rsync "${RSYNC_OPTS[@]}" --dry-run "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"
  exit 0
fi

echo "[deploy] Syncing files..."
rsync "${RSYNC_OPTS[@]}" "$LOCAL_DIR/" "$HOST:$REMOTE_DIR/"

if [ "${1}" = "--setup" ]; then
  echo "[deploy] Creating venv and systemd unit..."
  ssh "$HOST" "cd $REMOTE_DIR && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt"
  # Secrets (GEMINI_API_KEY=...) go in /etc/name-match.env on the server
  ssh "$HOST" "sudo tee /etc/systemd/system/name-match.service >/dev/null" <<EOF
[Unit]
Description=Name Swipe Match
After=network.target

[Service]
Type=simple
User=$REMOTE_USER
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=-/etc/name-match.env
ExecStart=$REMOTE_DIR/.venv/bin/uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
  ssh "$HOST" "sudo systemctl daemon-reload && sudo systemctl enable --now name-match.service"
else
  echo "[deploy] Restarting service..."
  ssh "$HOST" "cd $REMOTE_DIR && .venv/bin/pip install -q -r requirements.txt && sudo systemctl restart name-match.service"
fi

sleep 2
ssh "$HOST" "systemctl status name-match.service --no-pager --lines=5"
echo ""
echo "[deploy] Done. http://${HOST#*@}:$PORT"
