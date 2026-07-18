from __future__ import annotations

import logging
import os
import subprocess
import time

import requests


BOT_SERVICE = "xrp-bot"
CHECK_INTERVAL_SECONDS = 60


def send_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")
    if not token or not chat_id:
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        ).raise_for_status()
    except requests.RequestException as exc:
        logging.warning("No pude enviar alerta del watchdog: %s", exc)


def service_is_active() -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", BOT_SERVICE],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() == "active"


def restart_service() -> None:
    subprocess.run(["sudo", "systemctl", "restart", BOT_SERVICE], check=False)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | watchdog | %(message)s")
    send_telegram("🩺 *Watchdog activo*\nMonitoreando `xrp-bot` en AWS.")

    while True:
        if not service_is_active():
            logging.error("Servicio %s no activo. Intentando reinicio.", BOT_SERVICE)
            restart_service()
            send_telegram("🚨 *Watchdog reinició el bot*\nDetecté que `xrp-bot` estaba caído y lancé un reinicio de seguridad.")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
