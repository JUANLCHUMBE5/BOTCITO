#!/usr/bin/env bash
set -euo pipefail

BOT_DIR="${BOT_DIR:-/home/ubuntu/xrp-trading-bot}"
BOT_USER="${BOT_USER:-ubuntu}"
WORKDIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[1/8] Installing system packages"
sudo apt update
sudo apt install -y python3 python3-venv python3-pip rsync unzip

echo "[2/8] Preparing target directory at ${BOT_DIR}"
sudo mkdir -p "${BOT_DIR}"
sudo chown -R "${BOT_USER}:${BOT_USER}" "${BOT_DIR}"

echo "[3/8] Syncing project files"
rsync -a --delete \
  --exclude ".venv" \
  --exclude "__pycache__" \
  --exclude ".env" \
  --exclude "data" \
  --exclude "logs" \
  "${WORKDIR}/" "${BOT_DIR}/"

echo "[4/8] Creating virtual environment"
sudo -u "${BOT_USER}" python3 -m venv "${BOT_DIR}/.venv"
sudo -u "${BOT_USER}" "${BOT_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${BOT_USER}" "${BOT_DIR}/.venv/bin/pip" install -r "${BOT_DIR}/requirements.txt"

echo "[5/8] Ensuring runtime directories"
sudo -u "${BOT_USER}" mkdir -p "${BOT_DIR}/data" "${BOT_DIR}/logs"

if [[ ! -f "${BOT_DIR}/.env" && -f "${BOT_DIR}/.env.example" ]]; then
  echo "[6/8] Creating .env from example"
  sudo -u "${BOT_USER}" cp "${BOT_DIR}/.env.example" "${BOT_DIR}/.env"
  echo "⚠️  Complete ${BOT_DIR}/.env before starting the bot."
elif [[ -f "${BOT_DIR}/.env" ]]; then
  echo "[6/8] Backing up existing .env"
  backup_name=".env.backup.$(date +%Y%m%d-%H%M%S)"
  sudo -u "${BOT_USER}" cp "${BOT_DIR}/.env" "${BOT_DIR}/${backup_name}"
  echo "Backup guardado en ${BOT_DIR}/${backup_name}"
else
  echo "[6/8] No .env found and no .env.example — skipping"
fi

echo "[7/8] Installing systemd services"
sudo tee /etc/systemd/system/xrp-bot.service > /dev/null <<SERVICE
[Unit]
Description=XRP Trading Bot
After=network.target

[Service]
User=${BOT_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${BOT_DIR}/.env
ExecStart=${BOT_DIR}/.venv/bin/python ${BOT_DIR}/main.py
Restart=always
RestartSec=10
KillSignal=SIGINT
TimeoutStopSec=30
StandardOutput=append:${BOT_DIR}/logs/systemd-bot.log
StandardError=append:${BOT_DIR}/logs/systemd-bot-error.log

[Install]
WantedBy=multi-user.target
SERVICE

sudo tee /etc/systemd/system/xrp-watchdog.service > /dev/null <<SERVICE
[Unit]
Description=XRP Trading Bot Watchdog
After=network.target xrp-bot.service

[Service]
User=${BOT_USER}
WorkingDirectory=${BOT_DIR}
EnvironmentFile=${BOT_DIR}/.env
ExecStart=${BOT_DIR}/.venv/bin/python ${BOT_DIR}/watchdog.py
Restart=always
RestartSec=15
KillSignal=SIGINT
TimeoutStopSec=15
StandardOutput=append:${BOT_DIR}/logs/systemd-watchdog.log
StandardError=append:${BOT_DIR}/logs/systemd-watchdog-error.log

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable xrp-bot xrp-watchdog

echo "[8/8] Health check"
sudo systemctl restart xrp-bot xrp-watchdog
sleep 15

if systemctl is-active --quiet xrp-bot; then
  echo "✅ xrp-bot está activo y funcionando"
else
  echo "⚠️  xrp-bot no arrancó correctamente. Revisa con:"
  echo "    sudo journalctl -u xrp-bot --no-pager -n 30"
fi

if systemctl is-active --quiet xrp-watchdog; then
  echo "✅ xrp-watchdog está activo"
else
  echo "⚠️  xrp-watchdog no arrancó. Revisa con:"
  echo "    sudo journalctl -u xrp-watchdog --no-pager -n 30"
fi

echo
echo "Installation complete."
echo "Next commands:"
echo "  nano ${BOT_DIR}/.env"
echo "  sudo systemctl restart xrp-bot xrp-watchdog"
echo "  sudo systemctl status xrp-bot --no-pager"
echo "  sudo journalctl -u xrp-bot -f"
