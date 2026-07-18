from __future__ import annotations

import numpy as np
import pandas as pd


def build_dataframe(ohlcv: list[list[float]]) -> pd.DataFrame:
    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    frame = pd.DataFrame(ohlcv, columns=columns)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    return frame


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_100"] = df["close"].ewm(span=100, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    df["volume_ma_20"] = df["volume"].rolling(20).mean()
    df["volume_std_20"] = df["volume"].rolling(20).std()
    df["volume_ratio"] = df["volume"] / df["volume_ma_20"].replace(0, np.nan)
    df["volume_ratio"] = df["volume_ratio"].fillna(1.0)
    df["volume_zscore"] = (
        (df["volume"] - df["volume_ma_20"]) / df["volume_std_20"].replace(0, np.nan)
    ).fillna(0.0)
    df["momentum_5"] = df["close"].pct_change(5)
    df["momentum_20"] = df["close"].pct_change(20)
    df["range_pct"] = ((df["high"] - df["low"]) / df["open"].replace(0, np.nan)).fillna(0)
    df["candle_body_pct"] = ((df["close"] - df["open"]).abs() / df["open"].replace(0, np.nan)).fillna(0)
    df["upper_wick_pct"] = (
        (df["high"] - df[["open", "close"]].max(axis=1)) / df["open"].replace(0, np.nan)
    ).fillna(0)
    df["lower_wick_pct"] = (
        (df[["open", "close"]].min(axis=1) - df["low"]) / df["open"].replace(0, np.nan)
    ).fillna(0)
    df["distance_from_ema20_pct"] = ((df["close"] - df["ema_20"]) / df["ema_20"].replace(0, np.nan)).fillna(0)
    df["distance_from_ema50_pct"] = ((df["close"] - df["ema_50"]) / df["ema_50"].replace(0, np.nan)).fillna(0)
    df["trend_strength_pct"] = ((df["ema_20"] - df["ema_200"]) / df["ema_200"].replace(0, np.nan)).fillna(0)
    df["breakout_high_20"] = df["high"].rolling(20).max().shift(1)
    df["breakout_low_20"] = df["low"].rolling(20).min().shift(1)
    df["breakout_high_50"] = df["high"].rolling(50).max().shift(1)
    df["breakout_low_50"] = df["low"].rolling(50).min().shift(1)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    rolling_volume = df["volume"].rolling(20).sum().replace(0, np.nan)
    df["rolling_vwap_20"] = ((typical_price * df["volume"]).rolling(20).sum() / rolling_volume).fillna(df["close"])
    df["distance_from_vwap_pct"] = (
        (df["close"] - df["rolling_vwap_20"]) / df["rolling_vwap_20"].replace(0, np.nan)
    ).fillna(0)

    delta = df["close"].diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = pd.Series(gain).rolling(14).mean()
    avg_loss = pd.Series(loss).rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))
    df["rsi_14"] = df["rsi_14"].fillna(50)

    ema_12 = df["close"].ewm(span=12, adjust=False).mean()
    ema_26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd"] = ema_12 - ema_26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr_14"] = true_range.rolling(14).mean().bfill()
    df["atr_pct"] = (df["atr_14"] / df["close"].replace(0, np.nan)).fillna(0)

    df["bb_middle_20"] = df["close"].rolling(20).mean()
    df["bb_std_20"] = df["close"].rolling(20).std()
    df["bb_upper_20"] = df["bb_middle_20"] + (df["bb_std_20"] * 2)
    df["bb_lower_20"] = df["bb_middle_20"] - (df["bb_std_20"] * 2)
    df["bb_width_pct"] = (
        (df["bb_upper_20"] - df["bb_lower_20"]) / df["bb_middle_20"].replace(0, np.nan)
    ).fillna(0)
    return df
