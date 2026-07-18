#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

mkdir -p /home/ubuntu/xrp-trading-bot
cd /home/ubuntu/xrp-trading-bot

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "Instalación base completada."
