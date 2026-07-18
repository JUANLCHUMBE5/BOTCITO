from __future__ import annotations

import logging
import signal
import threading
import time
from logging.handlers import RotatingFileHandler

from bot.config import load_config
from bot.notifier import TelegramNotifier
from bot.storage import Storage
from bot.trader import AccountTrader, BotController


def setup_logging(log_path: str) -> None:
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
    )
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console)
    root_logger.addHandler(file_handler)


def main() -> None:
    config = load_config()
    setup_logging(str(config.log_path))
    logging.info(
        "Exchange: %s | Modo activo: %s | Símbolos: %s",
        config.exchange,
        config.trading_mode,
        ", ".join(config.symbols),
    )
    notifier = TelegramNotifier(config.telegram)

    if config.trading_mode == "real":
        for account in config.accounts:
            if not account.api_key or not account.secret:
                raise ValueError(f"Faltan credenciales para {account.name}.")

    storage = Storage(config.database_path)
    stop_event = threading.Event()
    controller = BotController()
    traders = [
        AccountTrader(config, account, symbol, storage, notifier, stop_event, controller)
        for account in config.accounts
        for symbol in config.symbols
    ]

    if config.telegram.notify_startup:
        notifier.send_lines(
            "🤖 *Bot iniciado*",
            f"*Exchange:* `{config.exchange}`",
            f"*Modo:* `{config.trading_mode}`",
            f"*Pares:* `{', '.join(config.symbols)}`",
            f"*Mercado:* `{config.market_type}` | *Margen:* `{config.margin_mode}`",
            f"*Apalancamiento exchange:* `x{config.exchange_leverage}`",
            f"*Display:* `USDT` y `{config.display_currency}` a `{config.quote_to_display_rate:.2f}`",
            f"*Cuentas activas:* `{len(config.accounts)}`",
            "*Motor de decisión:* `scalping futures 5m/10s + histórico XRP + order book LONG/SHORT`",
        )

    def handle_stop(signum, _frame) -> None:
        logging.info("Señal %s recibida. Cerrando bot...", signum)
        stop_event.set()

    def process_settings_command(text: str) -> str:
        parts = text.split()
        if len(parts) == 1:
            return (
                "⚙️ *Settings actuales*\n"
                f"Perfil: `{controller.get_profile()}`\n"
                f"Riesgo por operación: `{controller.get_capital_fraction() * 100:.1f}%`\n"
                f"Peso histórico: `{controller.get_history_weight():.2f}`\n"
                "Usa `/settings conservador`, `/settings agresivo`, `/settings risk 0.05`, `/settings ia 0.0-1.5`"
            )

        if parts[1] in {"conservador", "agresivo"}:
            profile = controller.set_profile(parts[1])
            return f"⚙️ Perfil actualizado a `{profile}`."

        if parts[1] == "risk" and len(parts) >= 3:
            value = controller.set_capital_fraction(float(parts[2]))
            return f"⚙️ Riesgo por operación actualizado a `{value * 100:.1f}%`."

        if parts[1] == "ia" and len(parts) >= 3:
            value = controller.set_history_weight(float(parts[2]))
            return f"⚙️ Peso del histórico actualizado a `{value:.2f}`."

        return "❓ Comando no reconocido. Usa `/settings`, `/settings conservador`, `/settings agresivo`, `/settings risk 0.05`, `/settings ia 1.0`"

    def command_loop() -> None:
        offset = None
        while not stop_event.is_set():
            updates = notifier.fetch_updates(offset=offset)
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message", {})
                text = (message.get("text") or "").strip().lower()
                chat_id = str(message.get("chat", {}).get("id", ""))
                if chat_id and chat_id != config.telegram.chat_id:
                    continue

                if text == "/status":
                    for trader in traders:
                        notifier.send_lines(*trader.build_status_lines())
                elif text == "/pnl":
                    for trader in traders:
                        notifier.send_lines(*trader.build_pnl_lines())
                elif text == "/stop":
                    controller.pause()
                    notifier.send("⏸️ Bot pausado. No abrirá nuevas posiciones.")
                elif text == "/resume":
                    controller.resume()
                    notifier.send("▶️ Bot reanudado. Vuelve a evaluar entradas.")
                elif text == "/close":
                    controller.request_force_close()
                    notifier.send("🧯 Cierre manual solicitado. La siguiente iteración cerrará la posición abierta.")
                elif text == "/help":
                    notifier.send(
                        "🤖 *Comandos disponibles*\n"
                        "/status — Estado actual del bot\n"
                        "/pnl — PnL del día con resumen de trades\n"
                        "/stop — Pausar nuevas entradas\n"
                        "/resume — Reanudar entradas\n"
                        "/close — Forzar cierre de posición abierta\n"
                        "/settings — Ver perfil y riesgo actual\n"
                        "/settings conservador|agresivo — Cambiar perfil\n"
                        "/settings risk 0.03 — Riesgo por operación\n"
                        "/settings ia 1.0 — Peso del histórico IA"
                    )
                elif text.startswith("/settings"):
                    try:
                        notifier.send(process_settings_command(text))
                    except ValueError as exc:
                        notifier.send(f"⚠️ No pude aplicar `/settings`: {exc}")
            time.sleep(2)

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    for trader in traders:
        trader.start()

    command_thread = None
    if notifier.is_ready and config.telegram.allow_commands:
        command_thread = threading.Thread(
            target=command_loop,
            daemon=True,
            name="telegram-commands",
        )
        command_thread.start()

    for trader in traders:
        trader.join()

    if command_thread is not None:
        command_thread.join(timeout=1)


if __name__ == "__main__":
    main()
