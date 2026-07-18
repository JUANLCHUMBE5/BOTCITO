from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class TakeProfitLevelConfig:
    target_pct: float
    close_ratio: float


@dataclass
class StrategyConfig:
    take_profit_pct: float
    stop_loss_pct: float
    volume_spike_factor: float
    momentum_threshold: float
    rsi_buy_min: float
    rsi_buy_max: float
    breakout_lookback: int
    max_candle_pct: float
    max_distance_from_ema20_pct: float
    min_atr_pct: float
    require_breakout: bool
    rebound_enabled: bool
    rebound_trend_strength_ratio: float
    rebound_rsi_min: float
    rebound_rsi_max: float
    rebound_volume_ratio_min: float
    rebound_min_atr_pct: float
    rebound_max_distance_from_ema20_pct: float
    rebound_macd_threshold: float
    rvol_override_threshold: float
    spread_limit_pct: float
    slippage_limit_pct: float
    trailing_activation_pct: float
    trailing_distance_pct: float
    trailing_profit_giveback_ratio: float
    support_break_min_profit_pct: float
    breakeven_trigger_pct: float
    atr_stop_loss_multiplier: float
    atr_take_profit_multiplier: float
    leverage_min: float
    leverage_mid: float
    leverage_max: float
    leverage_cap: float
    leverage_mid_score: float
    leverage_high_score: float
    maintenance_margin_pct: float
    pre_liquidation_buffer_pct: float
    circuit_breaker_daily_loss_pct: float
    circuit_breaker_daily_loss_usd: float
    max_simultaneous_positions: int
    open_fee_rate: float
    funding_fee_rate_per_4h: float
    god_candle_volume_multiplier: float
    take_profit_ladder: list[TakeProfitLevelConfig]


@dataclass
class AccountConfig:
    name: str
    api_key: str
    secret: str
    trade_amount_usdt: float
    paper_balance_usdt: float
    enabled: bool = True


@dataclass
class TelegramConfig:
    enabled: bool
    bot_token: str
    chat_id: str
    notify_startup: bool = True
    notify_errors: bool = True
    notify_buys: bool = True
    notify_sells: bool = True
    notify_news_blocks: bool = True
    notify_status_summary: bool = True
    notify_circuit_breaker: bool = True
    status_interval_minutes: int = 30
    allow_commands: bool = True


@dataclass
class NewsConfig:
    enabled: bool
    provider: str
    api_key: str
    query: str
    language: str
    country: str
    check_interval_minutes: int
    max_headlines: int
    block_on_negative_news: bool
    negative_threshold: float
    notify_headlines: bool = True


@dataclass
class MT5Config:
    login: int
    password_env_var: str
    server: str
    trade_volume_lots: float
    enabled: bool = True


@dataclass
class AppConfig:
    exchange: str
    symbols: list[str]
    market_type: str
    margin_mode: str
    exchange_leverage: int
    display_currency: str
    quote_to_display_rate: float
    scalp_reference_profit_display: float
    analysis_timezone: str
    session_start_hour: int | None
    session_lookback_minutes: int
    neutral_probability_floor: float
    neutral_probability_ceiling: float
    small_timeframe_min_probability: float
    low_probability_no_trade_threshold: float
    low_probability_zone_threshold: float
    post_profit_cooldown_minutes: int
    post_loss_cooldown_minutes: int
    post_profit_min_pnl_usdt: float
    post_profit_min_pnl_pct: float
    high_score_cooldown_minutes: int
    high_score_threshold: float
    timeframe: str
    poll_interval_seconds: int
    trading_mode: str
    binance_testnet: bool
    database_path: Path
    log_path: Path
    strategy: StrategyConfig
    accounts: List[AccountConfig]
    mt5: MT5Config
    telegram: TelegramConfig
    news: NewsConfig


def _read_env(prefix: str, field: str, default: str = "") -> str:
    return os.getenv(f"{prefix}_{field}", default).strip()


def _read_first_env(candidates: list[str], default: str = "") -> str:
    for candidate in candidates:
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    return default


def load_config(config_path: str = "config/settings.json") -> AppConfig:
    raw_path = Path(config_path)
    if not raw_path.exists():
        raise FileNotFoundError(
            f"No se encontró {raw_path}. Copia config/settings.example.json a config/settings.json."
        )

    with raw_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    strategy_payload = payload["strategy"]
    ladder_payload = strategy_payload.get(
        "take_profit_ladder",
        [
            {"target_pct": 0.05, "close_ratio": 0.30},
            {"target_pct": 0.10, "close_ratio": 0.40},
        ],
    )
    strategy = StrategyConfig(
        take_profit_pct=float(strategy_payload["take_profit_pct"]),
        stop_loss_pct=float(strategy_payload["stop_loss_pct"]),
        volume_spike_factor=float(strategy_payload["volume_spike_factor"]),
        momentum_threshold=float(strategy_payload["momentum_threshold"]),
        rsi_buy_min=float(strategy_payload["rsi_buy_min"]),
        rsi_buy_max=float(strategy_payload["rsi_buy_max"]),
        breakout_lookback=int(strategy_payload["breakout_lookback"]),
        max_candle_pct=float(strategy_payload["max_candle_pct"]),
        max_distance_from_ema20_pct=float(strategy_payload["max_distance_from_ema20_pct"]),
        min_atr_pct=float(strategy_payload["min_atr_pct"]),
        require_breakout=bool(strategy_payload["require_breakout"]),
        rebound_enabled=bool(strategy_payload.get("rebound_enabled", True)),
        rebound_trend_strength_ratio=float(strategy_payload.get("rebound_trend_strength_ratio", 0.997)),
        rebound_rsi_min=float(strategy_payload.get("rebound_rsi_min", 38.0)),
        rebound_rsi_max=float(strategy_payload.get("rebound_rsi_max", 58.0)),
        rebound_volume_ratio_min=float(strategy_payload.get("rebound_volume_ratio_min", 0.75)),
        rebound_min_atr_pct=float(strategy_payload.get("rebound_min_atr_pct", 0.00025)),
        rebound_max_distance_from_ema20_pct=float(strategy_payload.get("rebound_max_distance_from_ema20_pct", 0.012)),
        rebound_macd_threshold=float(strategy_payload.get("rebound_macd_threshold", -0.0006)),
        rvol_override_threshold=float(strategy_payload.get("rvol_override_threshold", 6.0)),
        spread_limit_pct=float(strategy_payload.get("spread_limit_pct", 0.002)),
        slippage_limit_pct=float(strategy_payload.get("slippage_limit_pct", 0.0025)),
        trailing_activation_pct=float(strategy_payload.get("trailing_activation_pct", 0.01)),
        trailing_distance_pct=float(strategy_payload.get("trailing_distance_pct", 0.006)),
        trailing_profit_giveback_ratio=float(strategy_payload.get("trailing_profit_giveback_ratio", 0.20)),
        support_break_min_profit_pct=float(strategy_payload.get("support_break_min_profit_pct", 0.004)),
        breakeven_trigger_pct=float(strategy_payload.get("breakeven_trigger_pct", 0.008)),
        atr_stop_loss_multiplier=float(strategy_payload.get("atr_stop_loss_multiplier", 1.8)),
        atr_take_profit_multiplier=float(strategy_payload.get("atr_take_profit_multiplier", 2.8)),
        leverage_min=float(strategy_payload.get("leverage_min", 1.0)),
        leverage_mid=float(strategy_payload.get("leverage_mid", 1.5)),
        leverage_max=float(strategy_payload.get("leverage_max", 3.0)),
        leverage_cap=float(strategy_payload.get("leverage_cap", 10.0)),
        leverage_mid_score=float(strategy_payload.get("leverage_mid_score", 75.0)),
        leverage_high_score=float(strategy_payload.get("leverage_high_score", 90.0)),
        maintenance_margin_pct=float(strategy_payload.get("maintenance_margin_pct", 0.10)),
        pre_liquidation_buffer_pct=float(strategy_payload.get("pre_liquidation_buffer_pct", 0.03)),
        circuit_breaker_daily_loss_pct=float(strategy_payload.get("circuit_breaker_daily_loss_pct", 0.05)),
        circuit_breaker_daily_loss_usd=float(strategy_payload.get("circuit_breaker_daily_loss_usd", 0.0)),
        max_simultaneous_positions=int(strategy_payload.get("max_simultaneous_positions", 1)),
        open_fee_rate=float(strategy_payload.get("open_fee_rate", 0.0002)),
        funding_fee_rate_per_4h=float(strategy_payload.get("funding_fee_rate_per_4h", 0.0001)),
        god_candle_volume_multiplier=float(strategy_payload.get("god_candle_volume_multiplier", 3.0)),
        take_profit_ladder=[
            TakeProfitLevelConfig(
                target_pct=float(item["target_pct"]),
                close_ratio=float(item["close_ratio"]),
            )
            for item in ladder_payload
        ],
    )
    accounts: List[AccountConfig] = []

    for account_payload in payload.get("accounts", []):
        prefix = account_payload.get("env_prefix")
        if prefix:
            account = AccountConfig(
                name=_read_env(prefix, "NAME", prefix.lower()),
                api_key=_read_env(prefix, "API_KEY"),
                secret=_read_env(prefix, "SECRET"),
                trade_amount_usdt=float(account_payload["trade_amount_usdt"]),
                paper_balance_usdt=float(account_payload["paper_balance_usdt"]),
                enabled=bool(account_payload.get("enabled", True)),
            )
        else:
            account = AccountConfig(
                name=account_payload.get("name", "cuenta_mt5"),
                api_key="",
                secret="",
                trade_amount_usdt=float(account_payload.get("trade_amount_usdt", 5.0)),
                paper_balance_usdt=float(account_payload.get("paper_balance_usdt", 1000.0)),
                enabled=bool(account_payload.get("enabled", True)),
            )
        accounts.append(account)

    database_path = Path(payload["database_path"])
    log_path = Path(payload["log_path"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    return AppConfig(
        exchange=str(payload.get("exchange", "binance")).lower(),
        symbols=[str(s) for s in payload.get("symbols", [payload.get("symbol", "XRP/USDT:USDT")])],
        market_type=str(payload.get("market_type", "spot")).lower(),
        margin_mode=str(payload.get("margin_mode", "cross")).lower(),
        exchange_leverage=int(payload.get("exchange_leverage", 1)),
        display_currency=str(payload.get("display_currency", "S/")),
        quote_to_display_rate=float(payload.get("quote_to_display_rate", 3.8)),
        scalp_reference_profit_display=float(payload.get("scalp_reference_profit_display", 23.0)),
        analysis_timezone=str(payload.get("analysis_timezone", "America/Lima")),
        session_start_hour=(
            None
            if payload.get("session_start_hour") is None
            else int(payload.get("session_start_hour", 7))
        ),
        session_lookback_minutes=int(payload.get("session_lookback_minutes", 240)),
        neutral_probability_floor=float(payload.get("neutral_probability_floor", 45.0)),
        neutral_probability_ceiling=float(payload.get("neutral_probability_ceiling", 56.0)),
        small_timeframe_min_probability=float(payload.get("small_timeframe_min_probability", 55.0)),
        low_probability_no_trade_threshold=float(payload.get("low_probability_no_trade_threshold", 45.0)),
        low_probability_zone_threshold=float(payload.get("low_probability_zone_threshold", 45.0)),
        post_profit_cooldown_minutes=int(payload.get("post_profit_cooldown_minutes", 20)),
        post_loss_cooldown_minutes=int(payload.get("post_loss_cooldown_minutes", 30)),
        post_profit_min_pnl_usdt=float(payload.get("post_profit_min_pnl_usdt", 0.25)),
        post_profit_min_pnl_pct=float(payload.get("post_profit_min_pnl_pct", 3.0)),
        high_score_cooldown_minutes=int(payload.get("high_score_cooldown_minutes", 10)),
        high_score_threshold=float(payload.get("high_score_threshold", 90.0)),
        timeframe=payload["timeframe"],
        poll_interval_seconds=int(payload["poll_interval_seconds"]),
        trading_mode=payload["trading_mode"].lower(),
        binance_testnet=bool(payload.get("binance_testnet", False)),
        database_path=database_path,
        log_path=log_path,
        strategy=strategy,
        accounts=[item for item in accounts if item.enabled],
        mt5=MT5Config(
            login=int(payload.get("mt5", {}).get("login", 0)),
            password_env_var=str(payload.get("mt5", {}).get("password_env_var", "EXNESS_MT5_PASSWORD")),
            server=str(payload.get("mt5", {}).get("server", "Exness-MT5Demo")),
            trade_volume_lots=float(payload.get("mt5", {}).get("trade_volume_lots", 0.01)),
            enabled=bool(payload.get("mt5", {}).get("enabled", True)),
        ),
        telegram=TelegramConfig(
            enabled=bool(payload.get("telegram", {}).get("enabled", False)),
            bot_token=_read_first_env(["TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"]),
            chat_id=_read_first_env(["TELEGRAM_CHAT_ID", "CHAT_ID"]),
            notify_startup=bool(payload.get("telegram", {}).get("notify_startup", True)),
            notify_errors=bool(payload.get("telegram", {}).get("notify_errors", True)),
            notify_buys=bool(payload.get("telegram", {}).get("notify_buys", True)),
            notify_sells=bool(payload.get("telegram", {}).get("notify_sells", True)),
            notify_news_blocks=bool(payload.get("telegram", {}).get("notify_news_blocks", True)),
            notify_status_summary=bool(payload.get("telegram", {}).get("notify_status_summary", True)),
            notify_circuit_breaker=bool(payload.get("telegram", {}).get("notify_circuit_breaker", True)),
            status_interval_minutes=int(payload.get("telegram", {}).get("status_interval_minutes", 30)),
            allow_commands=bool(payload.get("telegram", {}).get("allow_commands", True)),
        ),
        news=NewsConfig(
            enabled=bool(payload.get("news", {}).get("enabled", False)),
            provider=str(payload.get("news", {}).get("provider", "gnews")).lower(),
            api_key=_read_first_env(["NEWS_API_KEY", "GNEWS_API_KEY", "NEWSAPI_API_KEY"]),
            query=str(payload.get("news", {}).get("query", "XRP OR Ripple OR crypto market")),
            language=str(payload.get("news", {}).get("language", "en")),
            country=str(payload.get("news", {}).get("country", "us")),
            check_interval_minutes=int(payload.get("news", {}).get("check_interval_minutes", 15)),
            max_headlines=int(payload.get("news", {}).get("max_headlines", 5)),
            block_on_negative_news=bool(payload.get("news", {}).get("block_on_negative_news", True)),
            negative_threshold=float(payload.get("news", {}).get("negative_threshold", -0.35)),
            notify_headlines=bool(payload.get("news", {}).get("notify_headlines", True)),
        ),
    )
