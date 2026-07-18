from __future__ import annotations

import logging

import requests

from bot.config import TelegramConfig


class TelegramNotifier:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.logger = logging.getLogger("telegram")

    @property
    def is_ready(self) -> bool:
        return bool(
            self.config.enabled and self.config.bot_token and self.config.chat_id
        )

    def send(self, message: str) -> None:
        if not self.is_ready:
            return

        url = f"https://api.telegram.org/bot{self.config.bot_token}/sendMessage"
        payload = {
            "chat_id": self.config.chat_id,
            "text": message,
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            self.logger.warning("No se pudo enviar alerta a Telegram: %s", exc)

    def send_lines(self, *lines: str) -> None:
        clean_lines = [line for line in lines if line]
        self.send("\n".join(clean_lines))

    def fetch_updates(self, offset: int | None = None) -> list[dict]:
        if not self.is_ready or not self.config.allow_commands:
            return []

        url = f"https://api.telegram.org/bot{self.config.bot_token}/getUpdates"
        payload = {
            "timeout": 5,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset

        try:
            response = requests.get(url, params=payload, timeout=15)
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            self.logger.warning("No se pudieron leer comandos de Telegram: %s", exc)
            return []

        return data.get("result", []) if data.get("ok") else []
