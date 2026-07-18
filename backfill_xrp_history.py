from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import yfinance as yf

from bot.config import load_config
from bot.storage import Storage


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def normalize_rows(frame: pd.DataFrame) -> list[tuple[str, float, float, float, float, float]]:
    rows: list[tuple[str, float, float, float, float, float]] = []
    if frame.empty:
        return rows

    clean = frame.copy()
    clean = clean.reset_index()
    time_column = "Datetime" if "Datetime" in clean.columns else "Date"
    clean[time_column] = pd.to_datetime(clean[time_column], utc=True)

    for _, row in clean.iterrows():
        rows.append(
            (
                row[time_column].isoformat(),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                float(row.get("Volume", 0.0)),
            )
        )
    return rows


def fetch_and_store(storage: Storage, interval: str, period: str) -> int:
    logging.info("Descargando histórico real XRP con intervalo=%s periodo=%s", interval, period)
    ticker = yf.Ticker("XRP-USD")
    frame = ticker.history(period=period, interval=interval, auto_adjust=False)
    rows = normalize_rows(frame)
    inserted = storage.save_history_rows("yfinance", interval, rows)
    logging.info("Filas procesadas=%s | insertadas nuevas=%s", len(rows), inserted)
    return inserted


def main() -> None:
    config = load_config()
    storage = Storage(Path(config.database_path))
    inserted_daily = fetch_and_store(storage, interval="1d", period="max")
    inserted_hourly = fetch_and_store(storage, interval="1h", period="730d")
    inserted_15m = fetch_and_store(storage, interval="15m", period="60d")
    logging.info(
        "Backfill completado | 1d=%s | 1h=%s | 15m=%s",
        inserted_daily,
        inserted_hourly,
        inserted_15m,
    )


if __name__ == "__main__":
    main()
