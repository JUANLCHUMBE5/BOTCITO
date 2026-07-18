from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from bot.config import AccountConfig, AppConfig
from bot.exchange_client import ExchangeClient
from bot.indicators import add_indicators, build_dataframe
from bot.notifier import TelegramNotifier
from bot.storage import Storage
from bot.strategy import MomentumStrategy, Position


@dataclass
class AccountState:
    paper_balance_usdt: float
    paper_xrp_balance: float = 0.0
    wallet_total_usdt: float = 0.0
    daily_start_equity: float = 0.0
    daily_realized_pnl: float = 0.0
    current_day: date | None = None
    circuit_breaker_triggered: bool = False
    post_profit_cooldown_until: datetime | None = None
    post_loss_cooldown_until: datetime | None = None
    last_profitable_pnl: float = 0.0
    last_high_score_entry_at: datetime | None = None
    consecutive_losses_by_symbol: dict | None = None
    symbol_block_until: dict | None = None

    def __post_init__(self):
        if self.consecutive_losses_by_symbol is None:
            self.consecutive_losses_by_symbol = {}
        if self.symbol_block_until is None:
            self.symbol_block_until = {}


class BotController:
    def __init__(self) -> None:
        self._paused = False
        self._force_close = False
        self._profile = "conservador"
        self._capital_fraction = 0.03
        self._history_weight = 1.0
        self._lock = threading.Lock()

    def pause(self) -> None:
        with self._lock:
            self._paused = True

    def resume(self) -> None:
        with self._lock:
            self._paused = False

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def request_force_close(self) -> None:
        with self._lock:
            self._force_close = True

    def consume_force_close(self) -> bool:
        with self._lock:
            value = self._force_close
            self._force_close = False
            return value

    def set_profile(self, profile: str) -> str:
        normalized = profile.strip().lower()
        if normalized not in {"conservador", "agresivo"}:
            raise ValueError("Perfil invalido. Usa conservador o agresivo.")
        with self._lock:
            self._profile = normalized
        return normalized

    def get_profile(self) -> str:
        with self._lock:
            return self._profile

    def set_capital_fraction(self, value: float) -> float:
        sanitized = max(0.01, min(0.25, value))
        with self._lock:
            self._capital_fraction = sanitized
        return sanitized

    def get_capital_fraction(self) -> float:
        with self._lock:
            return self._capital_fraction

    def set_history_weight(self, value: float) -> float:
        sanitized = max(0.0, min(1.5, value))
        with self._lock:
            self._history_weight = sanitized
        return sanitized

    def get_history_weight(self) -> float:
        with self._lock:
            return self._history_weight


class AccountTrader(threading.Thread):
    def __init__(
        self,
        app_config: AppConfig,
        account: AccountConfig,
        symbol: str,
        storage: Storage,
        notifier: TelegramNotifier,
        stop_event: threading.Event,
        controller: BotController,
    ) -> None:
        super().__init__(daemon=True, name=f"bot-{account.name}-{symbol.replace('/', '_')}")
        self.symbol = symbol
        self.app_config = app_config
        self.account = account
        self.storage = storage
        self.notifier = notifier
        self.stop_event = stop_event
        self.controller = controller
        self.logger = logging.getLogger(account.name)
        self.client = ExchangeClient(app_config, account)
        self.strategy = MomentumStrategy(app_config.strategy)
        self.state = AccountState(
            paper_balance_usdt=account.paper_balance_usdt,
            wallet_total_usdt=account.paper_balance_usdt,
            daily_start_equity=account.paper_balance_usdt,
        )
        self._restore_state_from_storage()
        self.last_summary_sent_at: datetime | None = None
        self.market_context_cache: dict[str, float | str | bool] = {}
        self.market_context_updated_at: datetime | None = None
        self.balance_synced_at: datetime | None = None
        self.snapshot_lock = threading.Lock()
        self.latest_snapshot: dict[str, float | str] = {}
        self.latest_exchange_position: dict[str, float | str] | None = None

    def _display_value(self, usdt_value: float) -> str:
        rate = self.app_config.quote_to_display_rate
        if rate <= 0:
            return f"{usdt_value:.4f} USDT"
        return f"{usdt_value:.4f} USDT (~{self.app_config.display_currency} {usdt_value * rate:.2f})"

    def _scalp_reference_lines(self, notional_usdt: float, leverage: float) -> list[str]:
        lines = [
            f"Motor: `futures scalping 5m/10s LONG+SHORT`",
            f"Apalancamiento Binance: `x{self.app_config.exchange_leverage}` | efectivo bot: `x{leverage:.1f}`",
        ]
        if notional_usdt <= 0:
            return lines

        one_percent_pnl = notional_usdt * 0.01
        lines.append(
            f"Movimiento 1% precio ~= `{self._display_value(one_percent_pnl)}` antes de fees"
        )

        target_display = self.app_config.scalp_reference_profit_display
        rate = self.app_config.quote_to_display_rate
        if target_display > 0 and rate > 0:
            target_usdt = target_display / rate
            move_needed_pct = (target_usdt / notional_usdt) * 100
            lines.append(
                f"Para ~{self.app_config.display_currency} {target_display:.2f}: necesita ~`{move_needed_pct:.2f}%` a favor"
            )

        if leverage >= 50:
            approx_liq_move_pct = max(0.0, ((1 / leverage) - self.app_config.strategy.maintenance_margin_pct) * 100)
            lines.append(
                f"Riesgo x{leverage:.0f}: liquidacion aprox si va ~`{approx_liq_move_pct:.2f}%` en contra"
            )
        return lines

    def _post_profit_cooldown_active(self) -> bool:
        cooldown_until = self.state.post_profit_cooldown_until
        return bool(cooldown_until and datetime.now(timezone.utc) < cooldown_until)

    def _post_profit_cooldown_remaining_minutes(self) -> float:
        cooldown_until = self.state.post_profit_cooldown_until
        if cooldown_until is None:
            return 0.0
        remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
        return max(0.0, remaining)

    def _post_loss_cooldown_active(self) -> bool:
        cooldown_until = self.state.post_loss_cooldown_until
        return bool(cooldown_until and datetime.now(timezone.utc) < cooldown_until)

    def _post_loss_cooldown_remaining_minutes(self) -> float:
        cooldown_until = self.state.post_loss_cooldown_until
        if cooldown_until is None:
            return 0.0
        remaining = (cooldown_until - datetime.now(timezone.utc)).total_seconds() / 60
        return max(0.0, remaining)

    def _trading_day(self) -> date:
        try:
            local_tz = ZoneInfo(self.app_config.analysis_timezone)
        except Exception:
            local_tz = timezone.utc
        return datetime.now(timezone.utc).astimezone(local_tz).date()

    def _restore_state_from_storage(self) -> None:
        snapshot = self.storage.get_latest_account_snapshot(self.account.name)
        position = self.storage.get_open_position(self.account.name, self.symbol)

        if snapshot is not None:
            self.state.paper_balance_usdt = float(snapshot["paper_balance_usdt"])
            self.state.paper_xrp_balance = float(snapshot["paper_xrp_balance"])
            self.state.daily_start_equity = float(snapshot["estimated_equity"])

        # Restaurar circuit breaker desde DB para que persista entre reinicios
        today_key = self._trading_day().isoformat()
        cb_state = self.storage.get_circuit_breaker_state(self.account.name)
        if cb_state and cb_state["triggered"] and cb_state["triggered_date"] == today_key:
            self.state.circuit_breaker_triggered = True
            self.state.current_day = self._trading_day()
            self.controller.pause()
            self.logger.warning(
                "Circuit breaker restaurado desde DB | cuenta=%s | fecha=%s",
                self.account.name,
                today_key,
            )

        if position is None:
            return

        reconstructed_fee = position.cost_usdt * self.app_config.strategy.open_fee_rate
        reserved_capital = position.margin_used_usdt if position.margin_used_usdt > 0 else position.cost_usdt
        reconstructed_free_balance = max(
            0.0,
            self.account.paper_balance_usdt - reserved_capital - reconstructed_fee,
        )

        if self.state.paper_xrp_balance <= 0:
            self.state.paper_xrp_balance = float(position.quantity)
        if (
            snapshot is None
            or not bool(snapshot.get("open_position"))
            or self.state.paper_balance_usdt >= self.account.paper_balance_usdt
        ):
            self.state.paper_balance_usdt = reconstructed_free_balance

    def run(self) -> None:
        self.logger.info("Iniciando worker para %s", self.account.name)
        markets_loaded = False

        while not self.stop_event.is_set():
            try:
                if not markets_loaded:
                    self.client.load_markets()
                    self.client.configure_derivatives(self.symbol)
                    markets_loaded = True
                self._run_once()
            except Exception as exc:
                markets_loaded = False
                self.logger.exception("Error aislado en %s: %s", self.account.name, exc)
                if self.app_config.telegram.notify_errors:
                    self.notifier.send(
                        f"⚠️ Error en {self.account.name}\n⚙️ Modo: {self.app_config.trading_mode}\n🚨 Detalle: {exc}"
                    )
                time.sleep(max(self.app_config.poll_interval_seconds, 10))
            else:
                time.sleep(self.app_config.poll_interval_seconds)

    def _run_once(self) -> None:
        raw_ohlcv = self.client.fetch_ohlcv(
            self.symbol,
            self.app_config.timeframe,
            limit=220,
        )
        frame = add_indicators(build_dataframe(raw_ohlcv))
        last_row = frame.iloc[-1]
        market_context = self._get_market_context(last_row)
        market_context.update(self._build_session_context(frame, last_row))
        market_context.update(self._build_zone_context(frame, last_row))
        self.storage.save_price(self.account.name, self.symbol, last_row)

        position = self.storage.get_open_position(self.account.name, self.symbol)

        close_price = float(last_row["close"])
        synced = self._sync_wallet_balances(close_price, force=position is not None)
        if synced:
            if self._reconcile_missing_exchange_position(position):
                position = None
            if position is None:
                position = self._restore_untracked_exchange_position(last_row)
        estimated_equity = self._estimated_equity(close_price, position)
        self._roll_daily_context(estimated_equity)

        if self.state.circuit_breaker_triggered:
            if position is not None:
                ticker = self.client.fetch_ticker(self.symbol)
                exit_price = float(
                    (ticker.get("ask") if position.side == "SHORT" else ticker.get("bid"))
                    or ticker.get("last")
                    or close_price
                )
                self._close_position_quantity(
                    position=position,
                    price=exit_price,
                    quantity=position.quantity,
                    reason="circuit_breaker_active_exit",
                )
                position = None
                estimated_equity = self._estimated_equity(close_price, position)
            self._update_snapshot(last_row, position, estimated_equity, "circuit_breaker", market_context)
            self._save_snapshot(last_row, position, estimated_equity, "circuit_breaker")
            self._maybe_send_status_summary(last_row, position, estimated_equity, market_context)
            self.logger.warning("Circuit breaker activo | equity=%.2f", estimated_equity)
            return

        self._update_snapshot(last_row, position, estimated_equity, "running", market_context)
        self._save_snapshot(last_row, position, estimated_equity, "running")
        self._maybe_send_status_summary(last_row, position, estimated_equity, market_context)

        if self._should_trigger_circuit_breaker(estimated_equity):
            if position is not None:
                self._close_position_quantity(
                    position=position,
                    price=close_price,
                    quantity=position.quantity,
                    reason="circuit_breaker_exit",
                )
                position = None
                estimated_equity = self._estimated_equity(close_price, position)
            self.state.circuit_breaker_triggered = True
            self.controller.pause()
            # Persistir CB en DB para que sobreviva reinicios del proceso
            today_key = self._trading_day().isoformat()
            self.storage.set_circuit_breaker_state(self.account.name, True, today_key)
            self.logger.error("Circuit breaker activado | equity=%.2f", estimated_equity)
            if self.app_config.telegram.notify_circuit_breaker:
                limit_str = (
                    f"-${self.app_config.strategy.circuit_breaker_daily_loss_usd:.2f} USD"
                    if self.app_config.strategy.circuit_breaker_daily_loss_usd > 0
                    else f"-{self.app_config.strategy.circuit_breaker_daily_loss_pct * 100:.1f}%"
                )
                self.notifier.send_lines(
                    "🚨 Circuit breaker activado",
                    f"👤 Cuenta: `{self.account.name}`",
                    f"💸 Pérdida diaria: `{self.state.daily_realized_pnl:.2f}`",
                    f"📈 Equity actual: `{estimated_equity:.2f}`",
                    f"🛑 Límite: `{limit_str}`",
                )
            return

        if position is None:
            self._try_open_position(last_row, market_context)
        else:
            self._try_manage_position(position, last_row)

    def _roll_daily_context(self, estimated_equity: float) -> None:
        today = self._trading_day()
        if self.state.current_day != today:
            self.state.current_day = today
            self.state.daily_start_equity = estimated_equity
            self.state.daily_realized_pnl = 0.0
            self.state.circuit_breaker_triggered = False
            # Resetear CB en DB al iniciar nuevo día
            self.storage.set_circuit_breaker_state(
                self.account.name, False, today.isoformat()
            )
            self.controller.resume()
            self.logger.info("Nuevo día UTC | CB reseteado | equity_inicio=%.4f", estimated_equity)
            # Limpiar precios viejos (mantener solo 7 días)
            deleted = self.storage.cleanup_old_prices(days_to_keep=7)
            if deleted > 0:
                self.logger.info("Limpieza DB | %d filas de prices eliminadas", deleted)

    def _should_trigger_circuit_breaker(self, estimated_equity: float) -> bool:
        if self.state.daily_start_equity <= 0:
            return False
        if self.app_config.strategy.circuit_breaker_daily_loss_usd > 0:
            loss = self.state.daily_start_equity - estimated_equity
            return loss >= self.app_config.strategy.circuit_breaker_daily_loss_usd
        limit = self.app_config.strategy.circuit_breaker_daily_loss_pct
        floor = self.state.daily_start_equity * (1 - limit)
        return estimated_equity <= floor

    def _estimated_equity(self, close_price: float, position: Position | None) -> float:
        if self.app_config.market_type in {"swap", "future", "forex_cfd"}:
            if position is None:
                return self.state.wallet_total_usdt
            contract_size = self.client.get_contract_size(self.symbol)
            unrealized_pnl = (
                (position.quantity * contract_size * (close_price - position.entry_price))
                if position.side == "LONG"
                else (position.quantity * contract_size * (position.entry_price - close_price))
            )
            return self.state.wallet_total_usdt + unrealized_pnl
        if self.app_config.trading_mode in {"live", "real"}:
            return self.state.paper_balance_usdt + (self.state.paper_xrp_balance * close_price)
        if position is None:
            return self.state.paper_balance_usdt
        contract_size = self.client.get_contract_size(self.symbol)
        unrealized_pnl = (position.quantity * contract_size * close_price) - position.cost_usdt
        return self.state.paper_balance_usdt + position.margin_used_usdt + unrealized_pnl

    def _sync_wallet_balances(self, close_price: float, force: bool = False) -> bool:
        if self.app_config.trading_mode not in {"live", "real"}:
            return False

        now = datetime.now(timezone.utc)
        if not force and self.balance_synced_at and now - self.balance_synced_at < timedelta(seconds=30):
            return False

        try:
            if "/" in self.symbol:
                base_asset, quote_asset = self.symbol.split("/", 1)
                quote_asset = quote_asset.split(":")[0]
            else:
                if self.symbol.endswith("USD"):
                    base_asset = self.symbol[:-3]
                    quote_asset = "USD"
                elif self.symbol.endswith("USDT"):
                    base_asset = self.symbol[:-4]
                    quote_asset = "USDT"
                else:
                    base_asset = self.symbol
                    quote_asset = "USD"
            balance = self.client.fetch_balance()
            quote_data = balance.get(quote_asset, {}) or {}
            quote_free = float(quote_data.get("free") or 0.0)
            quote_total = float(quote_data.get("total") or quote_free)
            position_snapshot = (
                self._fetch_derivatives_position_snapshot()
                if self.app_config.market_type in {"swap", "future", "forex_cfd"}
                else None
            )
            self.latest_exchange_position = position_snapshot
            base_total = (
                float(position_snapshot["amount"])
                if position_snapshot is not None
                else float((balance.get(base_asset, {}) or {}).get("total") or 0.0)
            )
        except Exception as exc:
            self.logger.warning("No pude leer balance real de exchange: %s", exc)
            return False

        self.state.paper_balance_usdt = quote_free
        self.state.paper_xrp_balance = base_total
        self.state.wallet_total_usdt = quote_total if self.app_config.market_type in {"swap", "future", "forex_cfd"} else quote_free + (base_total * close_price)
        self.balance_synced_at = now

        if self.state.daily_start_equity <= 0 and self.state.wallet_total_usdt > 0:
            self.state.daily_start_equity = self.state.wallet_total_usdt
            self.logger.info("daily_start_equity inicializado con el balance real sincronizado: %.4f", self.state.daily_start_equity)
            if self.state.circuit_breaker_triggered:
                self.state.circuit_breaker_triggered = False
                today_key = self._trading_day().isoformat()
                self.storage.set_circuit_breaker_state(self.account.name, False, today_key)
                self.controller.resume()
                self.logger.info("Circuit breaker desactivado automáticamente al detectar balance real positivo.")

        self.logger.info(
            "Balance real sincronizado | mercado=%s | %s libre=%.4f | %s posicion=%.8f | equity=%.4f",
            self.app_config.market_type,
            quote_asset,
            quote_free,
            base_asset,
            base_total,
            self.state.wallet_total_usdt,
        )
        return True

    def _fetch_derivatives_position_quantity(self) -> float:
        snapshot = self._fetch_derivatives_position_snapshot()
        return 0.0 if snapshot is None else float(snapshot["amount"])

    def _fetch_derivatives_position_snapshot(self) -> dict[str, float | str] | None:
        if self.app_config.market_type not in {"swap", "future", "forex_cfd"}:
            return None
        positions = self.client.fetch_positions([self.symbol])
        for item in positions:
            if item.get("symbol") != self.symbol:
                continue
            contracts = float(item.get("contracts") or 0.0)
            side = str(item.get("side") or "").lower()
            info = item.get("info", {}) or {}
            raw_amt = float(info.get("positionAmt") or 0.0)
            amount = raw_amt
            if not amount and side == "short":
                amount = -abs(contracts)
            elif not amount and side == "long":
                amount = abs(contracts)
            if not amount:
                continue
            entry_price = float(item.get("entryPrice") or info.get("entryPrice") or 0.0)
            mark_price = float(item.get("markPrice") or info.get("markPrice") or 0.0)
            liquidation_price = float(item.get("liquidationPrice") or info.get("liquidationPrice") or 0.0)
            leverage = float(item.get("leverage") or info.get("leverage") or self.app_config.exchange_leverage)
            return {
                "amount": amount,
                "contracts": abs(amount) if contracts == 0 else contracts,
                "side": "SHORT" if amount < 0 else "LONG",
                "entry_price": entry_price,
                "mark_price": mark_price,
                "liquidation_price": liquidation_price,
                "leverage": leverage,
            }
        return None

    def _reconcile_missing_exchange_position(self, position: Position | None) -> bool:
        if position is None:
            return False
        if self.app_config.trading_mode not in {"live", "real"}:
            return False
        if self.app_config.market_type not in {"swap", "future", "forex_cfd"}:
            return False
        if abs(self.state.paper_xrp_balance) > 1e-9:
            return False

        self.storage.close_position(self.account.name, self.symbol)
        self.logger.warning(
            "Posicion fantasma reconciliada | DB tenia %s qty=%.6f entrada=%.5f pero Binance no tiene posicion activa",
            position.side,
            position.quantity,
            position.entry_price,
        )
        if self.app_config.telegram.notify_errors:
            self.notifier.send_lines(
                "👻 Posición fantasma reconciliada",
                f"👤 Cuenta: `{self.account.name}`",
                f"↔️ Lado DB: `{position.side}`",
                f"📦 Qty DB: `{position.quantity:.6f}`",
                "⚠️ Binance Futures reporta: `sin posición activa`",
                "🔧 Acción: DB marcada como cerrada; el bot vuelve a analizar entradas.",
            )
        return True

    def _restore_untracked_exchange_position(self, row) -> Position | None:
        if self.app_config.trading_mode not in {"live", "real"}:
            return None
        if self.app_config.market_type not in {"swap", "future", "forex_cfd"}:
            return None
        snapshot = self.latest_exchange_position
        if snapshot is None or abs(float(snapshot.get("amount", 0.0))) <= 1e-9:
            return None

        side = str(snapshot["side"])
        quantity = abs(float(snapshot["amount"]))
        entry_price = float(snapshot.get("entry_price") or row["close"])
        leverage = max(1.0, float(snapshot.get("leverage") or self.app_config.exchange_leverage))
        cost_usdt = quantity * entry_price
        margin_used = max(0.0, cost_usdt / leverage)
        position = self.strategy.build_position(
            account_name=self.account.name,
            symbol=self.symbol,
            quantity=quantity,
            entry_price=entry_price,
            cost_usdt=cost_usdt,
            leverage=leverage,
            margin_used_usdt=margin_used,
            mode=self.app_config.trading_mode,
            row=row,
            side=side,
            order_id="recovered_from_exchange",
        )
        exchange_liq = float(snapshot.get("liquidation_price") or 0.0)
        if exchange_liq > 0:
            position.liquidation_price = exchange_liq

        self.storage.upsert_position(position)
        self.logger.warning(
            "Posicion real restaurada desde Binance | lado=%s qty=%.6f entrada=%.5f margen=%.4f",
            position.side,
            position.quantity,
            position.entry_price,
            position.margin_used_usdt,
        )
        if self.app_config.telegram.notify_errors:
            self.notifier.send_lines(
                "🔌 Posición real restaurada desde Binance",
                f"👤 Cuenta: `{self.account.name}`",
                f"↔️ Lado: `{position.side}`",
                f"📦 Qty: `{position.quantity:.6f}`",
                f"🏷️ Entrada: `{position.entry_price:.5f}`",
                f"💰 Margen estimado: `{self._display_value(position.margin_used_usdt)}`",
                "🔧 Acción: el bot vuelve a gestionarla y no abre otra encima.",
            )
        return position

    def _position_metrics(
        self,
        current_price: float,
        position: Position | None,
    ) -> tuple[float, float, float, float, float]:
        if position is None:
            return 0.0, 0.0, 0.0, 0.0, 0.0

        contract_size = self.client.get_contract_size(self.symbol)
        current_notional = position.quantity * contract_size * current_price
        unrealized_pnl = (
            current_notional - position.cost_usdt
            if position.side == "LONG"
            else position.cost_usdt - current_notional
        )
        margin_used = position.margin_used_usdt if position.margin_used_usdt > 0 else position.cost_usdt
        unrealized_pct = (
            (unrealized_pnl / margin_used) * 100
            if margin_used > 0
            else 0.0
        )
        return margin_used, position.cost_usdt, current_notional, unrealized_pnl, unrealized_pct

    def _update_snapshot(
        self,
        row,
        position: Position | None,
        estimated_equity: float,
        note: str,
        market_context: dict[str, float | str | bool] | None = None,
    ) -> None:
        market_context = market_context or {}
        with self.snapshot_lock:
            self.latest_snapshot = {
                "price": float(row["close"]),
                "rsi": float(row["rsi_14"]),
                "macd": float(row["macd"]),
                "volume_ratio": float(row["volume_ratio"]),
                "atr_pct": float(row["atr_pct"]),
                "equity": estimated_equity,
                "status": note if position is None else "position_open",
                "position_qty": 0.0 if position is None else float(position.quantity),
                "position_entry": 0.0 if position is None else float(position.entry_price),
                "position_side": "" if position is None else position.side,
                "market_regime": str(market_context.get("market_regime", "desconocido")),
                "risk_state": str(market_context.get("risk_state", "normal")),
                "trend_15m_aligned": str(bool(market_context.get("trend_15m_aligned", False))),
                "trend_1h_aligned": str(bool(market_context.get("trend_1h_aligned", False))),
                "zone_long_probability": float(market_context.get("zone_long_probability", 0.0)),
                "zone_short_probability": float(market_context.get("zone_short_probability", 0.0)),
                "zone_long_grade": int(market_context.get("zone_long_grade", 0)),
                "zone_short_grade": int(market_context.get("zone_short_grade", 0)),
                "next_long_liquidity": float(market_context.get("next_long_liquidity", 0.0)),
                "next_short_liquidity": float(market_context.get("next_short_liquidity", 0.0)),
                "session_direction": str(market_context.get("session_direction", "NEUTRAL")),
                "session_continuation_probability": float(market_context.get("session_continuation_probability", 50.0)),
                "micro_continuation_probability": float(market_context.get("micro_continuation_probability", 50.0)),
                "session_no_trade": str(bool(market_context.get("session_no_trade", False))),
                "session_low_probability_block": str(bool(market_context.get("session_low_probability_block", False))),
                "session_low_probability_value": float(market_context.get("session_low_probability_value", 50.0)),
                "session_low_liquidity": str(bool(market_context.get("session_low_liquidity", False))),
                "economic_holiday": str(bool(market_context.get("economic_holiday", False))),
            }

    def build_status_lines(self) -> list[str]:
        with self.snapshot_lock:
            snapshot = dict(self.latest_snapshot)

        if not snapshot:
            return [
                "🤖 Estado instantáneo",
                f"👤 Cuenta: `{self.account.name}`",
                "⚠️ Estado: `sin snapshot todavía`",
            ]

        position = self.storage.get_open_position(self.account.name, self.symbol)
        margin_used, cost_usdt, current_notional, unrealized_pnl, unrealized_pct = self._position_metrics(
            float(snapshot["price"]),
            position,
        )
        if snapshot["position_qty"] > 0:
            side_emoji = "🟢 LONG" if position.side == "LONG" else "🔴 SHORT"
            position_text = (
                f"{side_emoji} abierta | lev `x{position.leverage:.1f}` | qty `{snapshot['position_qty']:.6f}` | "
                f"entrada `{snapshot['position_entry']:.5f}` | margen `{self._display_value(margin_used)}` | "
                f"nocional `{self._display_value(current_notional)}` | PnL flotante `{self._display_value(unrealized_pnl)}` ({unrealized_pct:.2f}%)"
            )
        else:
            position_text = "sin posición abierta"
        planned_notional = self.account.trade_amount_usdt * self.app_config.exchange_leverage
        base_asset = self.symbol.split('/')[0] if '/' in self.symbol else "XRP"

        long_grade = int(snapshot.get('zone_long_grade', 0))
        long_fuegos_str = f" ({long_grade} {'🔥' * long_grade if long_grade > 0 else 'fuegos'})"
        short_grade = int(snapshot.get('zone_short_grade', 0))
        short_fuegos_str = f" ({short_grade} {'🔥' * short_grade if short_grade > 0 else 'fuegos'})"

        return [
            "📊 Estado instantáneo",
            f"👤 Cuenta: `{self.account.name}`",
            f"⚙️ Modo: `{self.app_config.trading_mode}`",
            f"🪙 Par: `{self.symbol}`",
            f"🏗️ Mercado: `{self.app_config.market_type}` | Margen: `{self.app_config.margin_mode}` | Lev exchange: `x{self.app_config.exchange_leverage}`",
            f"🏷️ Precio: `{snapshot['price']:.5f}`",
            f"📊 RSI: `{snapshot['rsi']:.2f}` | MACD: `{snapshot['macd']:.5f}`",
            f"🔊 Vol ratio: `{snapshot['volume_ratio']:.2f}` | ATR %: `{snapshot['atr_pct']:.4f}`",
            f"🔄 Régimen: `{snapshot.get('market_regime', 'desconocido')}` | Riesgo mercado: `{snapshot.get('risk_state', 'normal')}`",
            f"⏱️ Alineación: 15m `{snapshot.get('trend_15m_aligned', 'False')}` | 1h `{snapshot.get('trend_1h_aligned', 'False')}`",
            f"⏰ Sesión: `{snapshot.get('session_direction', 'NEUTRAL')}` | cont `{float(snapshot.get('session_continuation_probability', 50.0)):.0f}%` | micro `{float(snapshot.get('micro_continuation_probability', 50.0)):.0f}%`",
            f"🛡️ Filtro sesión: no trade `{snapshot.get('session_no_trade', 'False')}` | prob baja `{snapshot.get('session_low_probability_block', 'False')}` ({float(snapshot.get('session_low_probability_value', 50.0)):.0f}%) | baja liquidez `{snapshot.get('session_low_liquidity', 'False')}` | feriado `{snapshot.get('economic_holiday', 'False')}`",
            f"🟢 Zona LONG: `{float(snapshot.get('zone_long_probability', 0.0)):.0f}%`{long_fuegos_str} | liquidez `{float(snapshot.get('next_long_liquidity', 0.0)):.5f}`",
            f"🔴 Zona SHORT: `{float(snapshot.get('zone_short_probability', 0.0)):.0f}%`{short_fuegos_str} | liquidez `{float(snapshot.get('next_short_liquidity', 0.0)):.5f}`",
            f"💰 USDT libre real: `{self._display_value(self.state.paper_balance_usdt)}`",
            f"🪙 Posición {base_asset} real: `{self.state.paper_xrp_balance:.6f}`",
            f"📈 Equity: `{self._display_value(float(snapshot['equity']))}`",
            f"🎯 Plan entrada: margen `{self._display_value(self.account.trade_amount_usdt)}` | nocional aprox `{self._display_value(planned_notional)}`",
            f"👤 Perfil: `{self.controller.get_profile()}` | Riesgo: `{self.controller.get_capital_fraction() * 100:.1f}%`",
            f"⚖️ Peso histórico: `{self.controller.get_history_weight():.2f}`",
            f"💼 Posición: {position_text}",
            f"⏳ Cooldown: Ganancia={self._post_profit_cooldown_active()} ({self._post_profit_cooldown_remaining_minutes():.1f}m) | Pérdida={self._post_loss_cooldown_active()} ({self._post_loss_cooldown_remaining_minutes():.1f}m)",
            f"⏸️ Bot pausado: `{self.controller.is_paused()}`",
        ]

    def build_pnl_lines(self) -> list[str]:
        trades = self.storage.get_trades_today(self.account.name)
        if not trades:
            return [
                f"PnL del día — `{self.account.name}`",
                "Sin operaciones registradas hoy.",
            ]
        wins = [t for t in trades if float(t["pnl"]) > 0]
        losses = [t for t in trades if float(t["pnl"]) <= 0]
        total_pnl = sum(float(t["pnl"]) for t in trades)
        best = max(trades, key=lambda t: float(t["pnl"]))
        worst = min(trades, key=lambda t: float(t["pnl"]))
        trade_lines = []
        for t in trades[-8:]:  # últimas 8
            emoji = "✅" if float(t["pnl"]) > 0 else "❌"
            trade_lines.append(
                f"{emoji} `{t['side']}` {t['reason']} → `{self._display_value(float(t['pnl']))}`"
            )
        return [
            f"📊 PnL del día — `{self.account.name}`",
            f"Operaciones: `{len(trades)}` | Ganadoras: `{len(wins)}` | Perdedoras: `{len(losses)}`",
            f"Win rate: `{len(wins)/len(trades)*100:.0f}%`",
            f"PnL total: `{self._display_value(total_pnl)}`",
            f"Mejor trade: `{self._display_value(float(best['pnl']))}` ({best['reason']})",
            f"Peor trade: `{self._display_value(float(worst['pnl']))}` ({worst['reason']})",
            "— Últimas operaciones —",
            *trade_lines,
        ]

    def _save_snapshot(
        self,
        row,
        position: Position | None,
        estimated_equity: float,
        note: str,
    ) -> None:
        self.storage.save_account_snapshot(
            account_name=self.account.name,
            mode=self.app_config.trading_mode,
            symbol=self.symbol,
            close_price=float(row["close"]),
            paper_balance_usdt=self.state.paper_balance_usdt,
            paper_xrp_balance=self.state.paper_xrp_balance,
            estimated_equity=estimated_equity,
            open_position=position is not None,
            note=note,
        )

    def _maybe_send_status_summary(
        self,
        row,
        position: Position | None,
        estimated_equity: float,
        market_context: dict[str, float | str | bool] | None = None,
    ) -> None:
        if not self.app_config.telegram.notify_status_summary:
            return
        market_context = market_context or {}

        now = datetime.now(timezone.utc)
        interval = timedelta(minutes=self.app_config.telegram.status_interval_minutes)
        if self.last_summary_sent_at and now - self.last_summary_sent_at < interval:
            return

        margin_used, cost_usdt, current_notional, unrealized_pnl, unrealized_pct = self._position_metrics(
            float(row["close"]),
            position,
        )
        if position:
            side_emoji = "🟢 LONG" if position.side == "LONG" else "🔴 SHORT"
            position_status = (
                f"{side_emoji} abierta a {position.entry_price:.5f} | lev x{position.leverage:.1f} | "
                f"qty {position.quantity:.6f} | margen `{self._display_value(margin_used)}` | "
                f"nocional `{self._display_value(current_notional)}` | PnL flotante `{self._display_value(unrealized_pnl)}` "
                f"({unrealized_pct:.2f}%) | SL {position.stop_loss_price:.5f}"
            )
        else:
            position_status = "sin posicion abierta"

        planned_notional = self.account.trade_amount_usdt * self.app_config.exchange_leverage
        base_asset = self.symbol.split('/')[0] if '/' in self.symbol else "XRP"

        long_grade = int(market_context.get('zone_long_grade', 0))
        long_fuegos_str = f" ({long_grade} {'🔥' * long_grade if long_grade > 0 else 'fuegos'})"
        short_grade = int(market_context.get('zone_short_grade', 0))
        short_fuegos_str = f" ({short_grade} {'🔥' * short_grade if short_grade > 0 else 'fuegos'})"

        self.notifier.send_lines(
            "🤖 Resumen del bot",
            f"👤 Cuenta: `{self.account.name}`",
            f"⚙️ Modo: `{self.app_config.trading_mode}`",
            f"🪙 Par: `{self.symbol}`",
            f"🏗️ Mercado: `{self.app_config.market_type}` | Margen: `{self.app_config.margin_mode}` | Lev exchange: `x{self.app_config.exchange_leverage}`",
            f"🏷️ Precio: `{float(row['close']):.5f}`",
            f"📊 RSI: `{float(row['rsi_14']):.2f}` | MACD: `{float(row['macd']):.5f}`",
            f"🔊 Vol ratio: `{float(row['volume_ratio']):.2f}` | ATR %: `{float(row['atr_pct']):.4f}`",
            f"🔄 Régimen: `{market_context.get('market_regime', 'desconocido')}` | Riesgo: `{market_context.get('risk_state', 'normal')}`",
            f"⏱️ 15m alineado: `{bool(market_context.get('trend_15m_aligned', False))}` | 1h alineado: `{bool(market_context.get('trend_1h_aligned', False))}`",
            f"⏰ Sesión: `{market_context.get('session_direction', 'NEUTRAL')}` | cont `{float(market_context.get('session_continuation_probability', 50.0)):.0f}%` | micro `{float(market_context.get('micro_continuation_probability', 50.0)):.0f}%`",
            f"🛡️ Filtro sesión: no trade `{bool(market_context.get('session_no_trade', False))}` | prob baja `{bool(market_context.get('session_low_probability_block', False))}` ({float(market_context.get('session_low_probability_value', 50.0)):.0f}%) | baja liquidez `{bool(market_context.get('session_low_liquidity', False))}` | feriado `{bool(market_context.get('economic_holiday', False))}`",
            f"🟢 Zona LONG: `{float(market_context.get('zone_long_probability', 0.0)):.0f}%`{long_fuegos_str} | liquidez `{float(market_context.get('next_long_liquidity', 0.0)):.5f}`",
            f"🔴 Zona SHORT: `{float(market_context.get('zone_short_probability', 0.0)):.0f}%`{short_fuegos_str} | liquidez `{float(market_context.get('next_short_liquidity', 0.0)):.5f}`",
            f"💰 USDT libre real: `{self._display_value(self.state.paper_balance_usdt)}`",
            f"🪙 Posición {base_asset} real: `{self.state.paper_xrp_balance:.6f}`",
            f"📈 Equity estimada: `{self._display_value(estimated_equity)}`",
            f"💸 Pérdida diaria: `{self._display_value(self.state.daily_realized_pnl)}`",
            f"🎯 Plan entrada: margen `{self._display_value(self.account.trade_amount_usdt)}` | nocional aprox `{self._display_value(planned_notional)}`",
            f"👤 Perfil: `{self.controller.get_profile()}` | Riesgo: `{self.controller.get_capital_fraction() * 100:.1f}%`",
            f"⏳ Cooldown: Ganancia={self._post_profit_cooldown_active()} ({self._post_profit_cooldown_remaining_minutes():.1f}m) | Pérdida={self._post_loss_cooldown_active()} ({self._post_loss_cooldown_remaining_minutes():.1f}m)",
            f"💼 Estado: {position_status}",
        )
        self.last_summary_sent_at = now

    def _build_historical_context(self, row) -> dict[str, float]:
        historical_context = self.storage.get_hourly_history_context(
            int(row["timestamp"].hour)
        )
        avg_hourly_volume = max(float(historical_context.get("avg_hourly_volume_30d", 0.0)), 0.0)
        projected_hour_volume = float(row["volume"]) * 60.0
        projected_ratio = (
            projected_hour_volume / avg_hourly_volume if avg_hourly_volume > 0 else 0.0
        )
        historical_context["projected_hourly_volume_ratio"] = projected_ratio
        return historical_context

    def _observed_date(self, day: date) -> date:
        if day.weekday() == 5:
            return day - timedelta(days=1)
        if day.weekday() == 6:
            return day + timedelta(days=1)
        return day

    def _nth_weekday(self, year: int, month: int, weekday: int, nth: int) -> date:
        current = date(year, month, 1)
        days_until = (weekday - current.weekday()) % 7
        return current + timedelta(days=days_until + ((nth - 1) * 7))

    def _last_weekday(self, year: int, month: int, weekday: int) -> date:
        next_month = date(year + int(month == 12), 1 if month == 12 else month + 1, 1)
        current = next_month - timedelta(days=1)
        return current - timedelta(days=(current.weekday() - weekday) % 7)

    def _easter_date(self, year: int) -> date:
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day = ((h + l - 7 * m + 114) % 31) + 1
        return date(year, month, day)

    def _is_us_market_holiday(self, local_day: date) -> bool:
        year = local_day.year
        holidays = {
            self._observed_date(date(year, 1, 1)),
            self._nth_weekday(year, 1, 0, 3),
            self._nth_weekday(year, 2, 0, 3),
            self._easter_date(year) - timedelta(days=2),
            self._last_weekday(year, 5, 0),
            self._observed_date(date(year, 6, 19)),
            self._observed_date(date(year, 7, 4)),
            self._nth_weekday(year, 9, 0, 1),
            self._nth_weekday(year, 11, 3, 4),
            self._observed_date(date(year, 12, 25)),
        }
        return local_day in holidays

    def _probability_for_direction(self, frame, direction: str, atr_pct: float) -> float:
        if len(frame) < 6:
            return 50.0
        sign = 1.0 if direction == "LONG" else -1.0
        first_open = max(float(frame.iloc[0]["open"]), 1e-9)
        last_close = float(frame.iloc[-1]["close"])
        net_move = ((last_close - first_open) / first_open) * sign
        recent_idx = max(0, len(frame) - 8)
        recent_move = ((last_close - float(frame.iloc[recent_idx]["close"])) / max(float(frame.iloc[recent_idx]["close"]), 1e-9)) * sign
        candle_changes = frame["close"].pct_change().dropna()
        same_direction_ratio = 0.5
        if not candle_changes.empty:
            same_direction_ratio = float(((candle_changes * sign) > 0).mean())
        ema_aligned = (
            last_close >= float(frame.iloc[-1]["ema_20"])
            if direction == "LONG"
            else last_close <= float(frame.iloc[-1]["ema_20"])
        )
        vwap_aligned = (
            last_close >= float(frame.iloc[-1]["rolling_vwap_20"])
            if direction == "LONG"
            else last_close <= float(frame.iloc[-1]["rolling_vwap_20"])
        )
        avg_volume_ratio = float(frame["volume_ratio"].tail(min(60, len(frame))).mean())
        range_pct = (
            (float(frame["high"].max()) - float(frame["low"].min())) / first_open
            if first_open > 0
            else 0.0
        )
        score = 50.0
        score += max(-18.0, min(18.0, net_move / max(atr_pct * 5.0, 0.001) * 18.0))
        score += max(-8.0, min(8.0, recent_move / max(atr_pct * 2.0, 0.0005) * 8.0))
        score += (same_direction_ratio - 0.5) * 18.0
        if ema_aligned:
            score += 5.0
        else:
            score -= 5.0
        if vwap_aligned:
            score += 4.0
        else:
            score -= 4.0
        if avg_volume_ratio >= 1.15:
            score += 5.0
        elif avg_volume_ratio < 0.75:
            score -= 7.0
        if range_pct < max(atr_pct * 2.5, 0.0012):
            score -= 8.0
        return round(max(1.0, min(99.0, score)), 2)

    def _build_session_context(self, frame, row) -> dict[str, float | str | bool]:
        try:
            local_tz = ZoneInfo(self.app_config.analysis_timezone)
        except Exception:
            local_tz = ZoneInfo("UTC")

        current_ts = row["timestamp"]
        if current_ts.tzinfo is None:
            current_ts = current_ts.tz_localize(timezone.utc)
        local_now = current_ts.to_pydatetime().astimezone(local_tz)

        start_mode = "rolling"
        if self.app_config.session_start_hour is None:
            start_utc = current_ts - timedelta(minutes=max(30, self.app_config.session_lookback_minutes))
        else:
            start_mode = "fixed_hour"
            start_local = local_now.replace(
                hour=self.app_config.session_start_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if local_now < start_local:
                start_local -= timedelta(days=1)
            start_utc = start_local.astimezone(timezone.utc)

        session_frame = frame[frame["timestamp"] >= start_utc]
        if len(session_frame) < 20:
            session_frame = frame.tail(min(len(frame), max(20, self.app_config.session_lookback_minutes)))

        current_price = float(row["close"])
        first_open = max(float(session_frame.iloc[0]["open"]), 1e-9)
        session_return = (current_price - first_open) / first_open
        atr_pct = max(float(row["atr_pct"]), 0.00035)
        direction = "NEUTRAL"
        if session_return >= atr_pct * 1.15:
            direction = "LONG"
        elif session_return <= -atr_pct * 1.15:
            direction = "SHORT"

        long_probability = self._probability_for_direction(session_frame, "LONG", atr_pct)
        short_probability = self._probability_for_direction(session_frame, "SHORT", atr_pct)
        continuation_probability = (
            long_probability
            if direction == "LONG"
            else short_probability
            if direction == "SHORT"
            else max(45.0, min(55.0, (long_probability + short_probability) / 2))
        )

        micro_frame = frame.tail(min(len(frame), 30))
        micro_long_probability = self._probability_for_direction(micro_frame, "LONG", atr_pct)
        micro_short_probability = self._probability_for_direction(micro_frame, "SHORT", atr_pct)
        micro_probability = (
            micro_long_probability
            if direction == "LONG"
            else micro_short_probability
            if direction == "SHORT"
            else max(micro_long_probability, micro_short_probability)
        )

        avg_session_volume_ratio = float(session_frame["volume_ratio"].tail(min(60, len(session_frame))).mean())
        session_range_pct = (
            (float(session_frame["high"].max()) - float(session_frame["low"].min())) / first_open
            if first_open > 0
            else 0.0
        )
        low_liquidity = bool(
            avg_session_volume_ratio < 0.75
            or session_range_pct < max(atr_pct * 2.5, 0.0012)
        )

        previous_rows = frame[frame["timestamp"] < session_frame.iloc[0]["timestamp"]]
        gap_pct = 0.0
        gap_open = False
        if not previous_rows.empty:
            previous_close = float(previous_rows.iloc[-1]["close"])
            session_open = float(session_frame.iloc[0]["open"])
            gap_pct = (session_open - previous_close) / max(previous_close, 1e-9)
            if abs(gap_pct) >= max(atr_pct * 1.8, 0.0018):
                if gap_pct > 0:
                    gap_open = bool(float(session_frame["low"].min()) > previous_close)
                else:
                    gap_open = bool(float(session_frame["high"].max()) < previous_close)

        neutralized = bool(
            self.app_config.neutral_probability_floor
            <= continuation_probability
            <= self.app_config.neutral_probability_ceiling
            and micro_probability < self.app_config.small_timeframe_min_probability
        )
        low_probability_value = min(continuation_probability, micro_probability)
        low_probability_block = bool(
            low_probability_value <= self.app_config.low_probability_no_trade_threshold
        )
        holiday = self._is_us_market_holiday(local_now.date())
        no_trade = bool(
            low_probability_block
            or neutralized
            or (low_liquidity and micro_probability < self.app_config.small_timeframe_min_probability)
            or (holiday and continuation_probability < 60 and micro_probability < 60)
            or (gap_open and continuation_probability <= 60 and micro_probability < 60)
        )

        return {
            "session_start_mode": start_mode,
            "session_direction": direction,
            "session_continuation_probability": round(continuation_probability, 2),
            "session_long_probability": round(long_probability, 2),
            "session_short_probability": round(short_probability, 2),
            "micro_continuation_probability": round(micro_probability, 2),
            "micro_long_probability": round(micro_long_probability, 2),
            "micro_short_probability": round(micro_short_probability, 2),
            "session_return_pct": round(session_return, 6),
            "session_range_pct": round(session_range_pct, 6),
            "session_avg_volume_ratio": round(avg_session_volume_ratio, 4),
            "session_low_liquidity": low_liquidity,
            "session_neutralized": neutralized,
            "session_low_probability_block": low_probability_block,
            "session_low_probability_value": round(low_probability_value, 2),
            "session_gap_pct": round(gap_pct, 6),
            "session_gap_open": gap_open,
            "economic_holiday": holiday,
            "session_no_trade": no_trade,
        }

    def _build_timeframe_context(self, row, prefix: str) -> dict[str, float | bool]:
        close_price = float(row["close"])
        ema_20 = float(row["ema_20"])
        ema_50 = float(row["ema_50"])
        ema_200 = float(row["ema_200"])
        return {
            f"close_{prefix}": close_price,
            f"ema_20_{prefix}": ema_20,
            f"ema_50_{prefix}": ema_50,
            f"ema_200_{prefix}": ema_200,
            f"rsi_{prefix}": float(row["rsi_14"]),
            f"atr_pct_{prefix}": float(row["atr_pct"]),
            f"volume_ratio_{prefix}": float(row["volume_ratio"]),
            f"momentum_5_{prefix}": float(row["momentum_5"]),
            f"distance_from_ema20_pct_{prefix}": float(row["distance_from_ema20_pct"]),
            f"bb_width_pct_{prefix}": float(row["bb_width_pct"]),
            f"trend_{prefix}_aligned": bool(close_price > ema_20 > ema_50 and ema_50 >= ema_200 * 0.998),
            f"trend_{prefix}_bearish": bool(close_price < ema_20 < ema_50 and ema_50 <= ema_200 * 1.002),
            f"macd_{prefix}_bullish": bool(float(row["macd"]) >= float(row["macd_signal"])),
            f"macd_{prefix}_bearish": bool(float(row["macd"]) <= float(row["macd_signal"])),
            f"above_{prefix}_vwap": bool(close_price >= float(row["rolling_vwap_20"])),
            f"below_{prefix}_vwap": bool(close_price <= float(row["rolling_vwap_20"])),
            f"below_ema50_{prefix}": bool(close_price < ema_50),
        }

    def _get_market_context(self, row) -> dict[str, float | str | bool]:
        now = datetime.now(timezone.utc)
        if (
            self.market_context_updated_at is not None
            and now - self.market_context_updated_at < timedelta(seconds=60)
            and self.market_context_cache
        ):
            context = dict(self.market_context_cache)
            context.update(self._build_historical_context(row))
            return context

        context: dict[str, float | str | bool] = self._build_historical_context(row)
        context.update(self._build_timeframe_context(row, "5m"))
        try:
            for timeframe, prefix in (("15m", "15m"), ("1h", "1h")):
                raw_ohlcv = self.client.fetch_ohlcv(
                    self.symbol,
                    timeframe,
                    limit=220,
                )
                frame = add_indicators(build_dataframe(raw_ohlcv))
                context.update(self._build_timeframe_context(frame.iloc[-1], prefix))
        except Exception as exc:
            self.logger.warning("No pude refrescar contexto multi-timeframe: %s", exc)

        trend_1h = bool(context.get("trend_1h_aligned", False))
        trend_15m = bool(context.get("trend_15m_aligned", False))
        bearish_1h = bool(context.get("trend_1h_bearish", False))
        bearish_15m = bool(context.get("trend_15m_bearish", False))
        below_1h = bool(context.get("below_ema50_1h", False))
        below_15m = bool(context.get("below_ema50_15m", False))
        close_above_1h = float(context.get("close_1h", row["close"])) >= float(
            context.get("ema_200_1h", row["ema_200"])
        )
        close_above_15m = not below_15m

        if trend_1h and trend_15m:
            market_regime = "tendencia_alcista"
        elif bearish_1h and bearish_15m:
            market_regime = "bajista"
        elif close_above_1h and close_above_15m:
            market_regime = "rango_constructivo"
        else:
            market_regime = "mixto"

        distance_5m = abs(float(row["distance_from_ema20_pct"]))
        distance_15m = abs(float(context.get("distance_from_ema20_pct_15m", 0.0)))
        bb_width_15m = float(context.get("bb_width_pct_15m", 0.0))
        if distance_5m > self.app_config.strategy.max_distance_from_ema20_pct or distance_15m > 0.025:
            risk_state = "sobreextendido"
        elif bb_width_15m and bb_width_15m < 0.012:
            risk_state = "compresion"
        else:
            risk_state = "normal"

        context["market_regime"] = market_regime
        context["risk_state"] = risk_state
        self.market_context_cache = dict(context)
        self.market_context_updated_at = now
        return context

    def _zone_grade(self, probability: float) -> int:
        if probability >= 75:
            return 3
        if probability >= 65:
            return 2
        if probability >= 55:
            return 1
        return 0

    def _is_induced_liquidity(self, frame, idx: int, price: float, side: str, radius_pct: float) -> bool:
        row = frame.iloc[idx]
        body_pct = float(row.get("candle_body_pct", 0.0))
        volume_ratio = float(row.get("volume_ratio", 1.0))
        if side == "high":
            wick_pct = float(row.get("upper_wick_pct", 0.0))
            swept_later = bool((frame.iloc[idx + 1 :]["high"] > price * (1 + radius_pct * 0.7)).any())
        else:
            wick_pct = float(row.get("lower_wick_pct", 0.0))
            swept_later = bool((frame.iloc[idx + 1 :]["low"] < price * (1 - radius_pct * 0.7)).any())
        wick_rejection = wick_pct > max(body_pct * 1.8, radius_pct * 0.35) and volume_ratio < 1.15
        return bool(swept_later or wick_rejection)

    def _score_zone_probability(self, frame, zone_price: float, side: str, radius_pct: float) -> tuple[float, int]:
        touches = 0
        wins = 0
        recent = frame.tail(180).reset_index(drop=True)
        reaction_window = 6
        for idx in range(max(0, len(recent) - reaction_window - 90), len(recent) - reaction_window):
            row = recent.iloc[idx]
            touched = (
                float(row["low"]) <= zone_price * (1 + radius_pct)
                and float(row["high"]) >= zone_price * (1 - radius_pct)
            )
            if not touched:
                continue
            future = recent.iloc[idx + 1 : idx + 1 + reaction_window]
            if future.empty:
                continue
            touches += 1
            if side == "support":
                protected = float(future["low"].min()) >= zone_price * (1 - radius_pct * 1.4)
                reacted = float(future["high"].max()) >= zone_price * (1 + radius_pct * 2.2)
            else:
                protected = float(future["high"].max()) <= zone_price * (1 + radius_pct * 1.4)
                reacted = float(future["low"].min()) <= zone_price * (1 - radius_pct * 2.2)
            if protected and reacted:
                wins += 1

        if touches == 0:
            return 50.0, 0
        raw_probability = (wins / touches) * 100
        sample_bonus = min(12.0, touches * 2.0)
        probability = min(92.0, max(35.0, raw_probability * 0.88 + sample_bonus))
        return round(probability, 2), touches

    def _find_liquidity_target(
        self,
        frame,
        current_price: float,
        direction: str,
        radius_pct: float,
    ) -> tuple[float, float, bool]:
        recent = frame.tail(150).reset_index(drop=True)
        candidates: list[tuple[float, int]] = []
        for idx in range(3, len(recent) - 3):
            row = recent.iloc[idx]
            if direction == "LONG":
                price = float(row["high"])
                if price <= current_price * (1 + radius_pct):
                    continue
                is_pivot = price >= float(recent.iloc[idx - 3 : idx]["high"].max()) and price >= float(recent.iloc[idx + 1 : idx + 4]["high"].max())
                side = "high"
            else:
                price = float(row["low"])
                if price >= current_price * (1 - radius_pct):
                    continue
                is_pivot = price <= float(recent.iloc[idx - 3 : idx]["low"].min()) and price <= float(recent.iloc[idx + 1 : idx + 4]["low"].min())
                side = "low"
            if not is_pivot:
                continue
            if self._is_induced_liquidity(recent, idx, price, side, radius_pct):
                continue
            candidates.append((price, idx))

        if not candidates:
            return 0.0, 0.0, True
        target_price, _ = min(candidates, key=lambda item: abs(item[0] - current_price))
        distance_pct = (
            (target_price - current_price) / current_price
            if direction == "LONG"
            else (current_price - target_price) / current_price
        )
        return round(target_price, 6), round(max(0.0, distance_pct), 6), False

    def _build_zone_context(self, frame, row) -> dict[str, float | bool | int]:
        current_price = float(row["close"])
        atr_pct = max(float(row["atr_pct"]), 0.00035)
        radius_pct = min(0.004, max(0.001, atr_pct * 1.25))
        recent = frame.tail(170).reset_index(drop=True)

        support_candidates: list[float] = []
        resistance_candidates: list[float] = []
        for idx in range(3, len(recent) - 3):
            low = float(recent.iloc[idx]["low"])
            high = float(recent.iloc[idx]["high"])
            if low <= float(recent.iloc[idx - 3 : idx]["low"].min()) and low <= float(recent.iloc[idx + 1 : idx + 4]["low"].min()):
                support_candidates.append(low)
            if high >= float(recent.iloc[idx - 3 : idx]["high"].max()) and high >= float(recent.iloc[idx + 1 : idx + 4]["high"].max()):
                resistance_candidates.append(high)

        supports = [price for price in support_candidates if price <= current_price * (1 + radius_pct * 2.5)]
        resistances = [price for price in resistance_candidates if price >= current_price * (1 - radius_pct * 2.5)]
        support_price = max(supports) if supports else 0.0
        resistance_price = min(resistances) if resistances else 0.0

        long_probability, long_touches = (
            self._score_zone_probability(recent, support_price, "support", radius_pct)
            if support_price > 0
            else (0.0, 0)
        )
        short_probability, short_touches = (
            self._score_zone_probability(recent, resistance_price, "resistance", radius_pct)
            if resistance_price > 0
            else (0.0, 0)
        )
        long_active = bool(support_price > 0 and abs(current_price - support_price) / current_price <= radius_pct * 2.5)
        short_active = bool(resistance_price > 0 and abs(resistance_price - current_price) / current_price <= radius_pct * 2.5)
        long_low_probability_block = bool(
            long_active
            and 0 < long_probability <= self.app_config.low_probability_zone_threshold
        )
        short_low_probability_block = bool(
            short_active
            and 0 < short_probability <= self.app_config.low_probability_zone_threshold
        )
        long_target, long_target_distance, long_target_induced = self._find_liquidity_target(
            recent,
            current_price,
            "LONG",
            radius_pct,
        )
        short_target, short_target_distance, short_target_induced = self._find_liquidity_target(
            recent,
            current_price,
            "SHORT",
            radius_pct,
        )

        return {
            "zone_radius_pct": round(radius_pct, 6),
            "zone_long_price": round(support_price, 6),
            "zone_short_price": round(resistance_price, 6),
            "zone_long_probability": long_probability,
            "zone_short_probability": short_probability,
            "zone_long_touches": long_touches,
            "zone_short_touches": short_touches,
            "zone_long_grade": self._zone_grade(long_probability) if long_active else 0,
            "zone_short_grade": self._zone_grade(short_probability) if short_active else 0,
            "zone_long_active": long_active,
            "zone_short_active": short_active,
            "zone_long_low_probability_block": long_low_probability_block,
            "zone_short_low_probability_block": short_low_probability_block,
            "next_long_liquidity": long_target,
            "next_short_liquidity": short_target,
            "next_long_liquidity_distance_pct": long_target_distance,
            "next_short_liquidity_distance_pct": short_target_distance,
            "next_long_liquidity_induced": long_target_induced,
            "next_short_liquidity_induced": short_target_induced,
        }

    def _build_microstructure_context(self, row) -> dict[str, float | bool]:
        try:
            order_book = self.client.fetch_order_book(self.symbol, limit=20)
            bids = order_book.get("bids") or []
            asks = order_book.get("asks") or []
            bid_depth = sum(float(price) * float(amount) for price, amount in bids[:10])
            ask_depth = sum(float(price) * float(amount) for price, amount in asks[:10])
            best_bid = float(bids[0][0]) if bids else float(row["close"])
            best_ask = float(asks[0][0]) if asks else float(row["close"])
            total_depth = bid_depth + ask_depth
            imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
            micro_spread_pct = (
                max(0.0, best_ask - best_bid) / best_ask
                if best_ask > 0
                else 0.0
            )
            return {
                "order_book_imbalance": imbalance,
                "micro_spread_pct": micro_spread_pct,
                "micro_bid_depth_usdt": bid_depth,
                "micro_ask_depth_usdt": ask_depth,
                "micro_liquidity_ok": bool(
                    total_depth >= 2000.0 and micro_spread_pct <= self.app_config.strategy.spread_limit_pct
                ),
            }
        except Exception as exc:
            self.logger.warning("No pude leer order book: %s", exc)
            return {
                "order_book_imbalance": 0.0,
                "micro_spread_pct": 0.0,
                "micro_bid_depth_usdt": 0.0,
                "micro_ask_depth_usdt": 0.0,
                "micro_liquidity_ok": True,
            }

    def _try_open_position(self, row, market_context: dict[str, float | str | bool]) -> None:
        if self.controller.is_paused():
            self.logger.info("Bot pausado | sin nuevas entradas")
            return
        if self._post_profit_cooldown_active():
            self.logger.info(
                "Cooldown post-ganancia activo | resta=%.1f min | ultima_ganancia=%.4f | sigue analizando sin abrir",
                self._post_profit_cooldown_remaining_minutes(),
                self.state.last_profitable_pnl,
            )
            return
        if self._post_loss_cooldown_active():
            self.logger.info(
                "Cooldown post-pérdida activo | resta=%.1f min | sigue analizando sin abrir",
                self._post_loss_cooldown_remaining_minutes(),
            )
            return
        if self.app_config.market_type in {"swap", "future", "forex_cfd"} and abs(self.state.paper_xrp_balance) > 1e-9:
            self.logger.warning(
                "Entrada bloqueada | Binance ya reporta posicion real qty=%.8f sin posicion DB restaurada todavia",
                self.state.paper_xrp_balance,
            )
            return

        # --- Anti-espiral: bloquear si hay muchas pérdidas consecutivas en este par ---
        symbol_block_until = self.state.symbol_block_until.get(self.symbol)
        if symbol_block_until is not None and datetime.now(timezone.utc) < symbol_block_until:
            remaining = (symbol_block_until - datetime.now(timezone.utc)).total_seconds() / 60
            self.logger.info(
                "Anti-espiral activo | %s bloqueado por pérdidas consecutivas | resta=%.1f min",
                self.symbol,
                remaining,
            )
            return

        # --- Cooldown post-score-alto (Componente 5) ---
        high_score_cooldown_until = self.state.last_high_score_entry_at
        if high_score_cooldown_until is not None:
            elapsed = (datetime.now(timezone.utc) - high_score_cooldown_until).total_seconds() / 60
            if elapsed < self.app_config.high_score_cooldown_minutes:
                # Permitir re-entrada solo si el nuevo score también es alto
                # (se validará después de evaluar la señal)
                pass  # se chequea abajo después del cálculo de decision

        market_context.update(self._build_microstructure_context(row))
        historical_context = dict(market_context)
        history_weight = self.controller.get_history_weight()
        should_long, long_decision = self.strategy.should_buy(
            row,
            historical_context=historical_context,
            market_context=market_context,
            history_weight=history_weight,
        )
        should_short, short_decision = self.strategy.should_short(
            row,
            historical_context=historical_context,
            market_context=market_context,
            history_weight=history_weight,
        )
        ticker = self.client.fetch_ticker(self.symbol)
        bid = float(ticker.get("bid") or row["close"])
        ask = float(ticker.get("ask") or row["close"])
        last = float(ticker.get("last") or row["close"])
        spread_pct = max(0.0, (ask - bid) / ask) if ask else 0.0

        if should_long and should_short:
            direction = "SHORT" if short_decision.score > long_decision.score else "LONG"
            approved = True
        elif should_long:
            direction = "LONG"
            approved = True
        elif should_short:
            direction = "SHORT"
            approved = True
        else:
            direction = "LONG" if long_decision.score >= short_decision.score else "SHORT"
            approved = False
        decision = long_decision if direction == "LONG" else short_decision

        entry_price = ask if direction == "LONG" and ask > 0 else bid if bid > 0 else last
        slippage_pct = abs(entry_price - float(row["close"])) / max(float(row["close"]), 1e-9)
        leverage = (
            1.0
            if self.app_config.trading_mode == "real" and self.app_config.market_type == "spot"
            else self.strategy.select_leverage(decision.score)
        )

        audit_reason = f"{direction}: {decision.reason} | long_score={long_decision.score:.2f} short_score={short_decision.score:.2f}"
        if spread_pct > self.app_config.strategy.spread_limit_pct:
            approved = False
            audit_reason = f"spread alto ({spread_pct:.4f})"
        if slippage_pct > self.app_config.strategy.slippage_limit_pct:
            approved = False
            audit_reason = f"slippage alto ({slippage_pct:.4f})"

        # --- Enforcement del cooldown post-score-alto (Componente 5) ---
        if approved and self.state.last_high_score_entry_at is not None:
            elapsed = (datetime.now(timezone.utc) - self.state.last_high_score_entry_at).total_seconds() / 60
            if elapsed < self.app_config.high_score_cooldown_minutes:
                if decision.score < self.app_config.high_score_threshold:
                    approved = False
                    audit_reason = f"cooldown post-score-alto ({elapsed:.1f}/{self.app_config.high_score_cooldown_minutes} min, score={decision.score:.1f} < {self.app_config.high_score_threshold:.0f})"

        # --- Verificar límite de posiciones simultáneas ---
        if approved:
            active_positions = self.storage.get_active_positions_count(self.account.name)
            if active_positions >= self.app_config.strategy.max_simultaneous_positions:
                approved = False
                audit_reason = f"limite posiciones simultaneas ({active_positions}/{self.app_config.strategy.max_simultaneous_positions})"

        self.storage.save_audit_event(
            account_name=self.account.name,
            symbol=self.symbol,
            approved=approved,
            decision_score=decision.score,
            confidence=decision.confidence,
            leverage=leverage,
            spread_pct=spread_pct,
            slippage_pct=slippage_pct,
            close_price=float(row["close"]),
            volume_ratio=float(row["volume_ratio"]),
            atr_pct=float(row["atr_pct"]),
            rsi=float(row["rsi_14"]),
            macd=float(row["macd"]),
            historical_return=historical_context["avg_hourly_return"],
            historical_positive_ratio=historical_context["positive_hour_ratio"],
            reason=audit_reason,
        )

        if not approved:
            self.logger.info(
                "Sin entrada | mejor=%s sesion=%s cont=%.0f micro=%.0f no_trade=%s prob_baja=%s min_prob=%.0f feriado=%s baja_liq=%s regimen=%s riesgo=%s zona_long=%.0f%%/%df zona_short=%.0f%%/%df liq_long=%.5f liq_short=%.5f 15m_long=%s 1h_long=%s 15m_short=%s 1h_short=%s ob_imb=%.2f precio=%.5f rsi=%.2f macd=%.5f vol_ratio=%.2f dist_ema20=%.4f hist_ret=%.4f hist_pos=%.2f god_vol=%.2f spread=%.4f slip=%.4f long_score=%.2f short_score=%.2f conf=%s motivo=%s",
                direction,
                market_context.get("session_direction", "NEUTRAL"),
                float(market_context.get("session_continuation_probability", 50.0)),
                float(market_context.get("micro_continuation_probability", 50.0)),
                bool(market_context.get("session_no_trade", False)),
                bool(market_context.get("session_low_probability_block", False)),
                float(market_context.get("session_low_probability_value", 50.0)),
                bool(market_context.get("economic_holiday", False)),
                bool(market_context.get("session_low_liquidity", False)),
                market_context.get("market_regime", "desconocido"),
                market_context.get("risk_state", "normal"),
                float(market_context.get("zone_long_probability", 0.0)),
                int(market_context.get("zone_long_grade", 0)),
                float(market_context.get("zone_short_probability", 0.0)),
                int(market_context.get("zone_short_grade", 0)),
                float(market_context.get("next_long_liquidity", 0.0)),
                float(market_context.get("next_short_liquidity", 0.0)),
                bool(market_context.get("trend_15m_aligned", False)),
                bool(market_context.get("trend_1h_aligned", False)),
                bool(market_context.get("trend_15m_bearish", False)),
                bool(market_context.get("trend_1h_bearish", False)),
                float(market_context.get("order_book_imbalance", 0.0)),
                row["close"],
                row["rsi_14"],
                row["macd"],
                row["volume_ratio"],
                row["distance_from_ema20_pct"],
                historical_context["avg_hourly_return"],
                historical_context["positive_hour_ratio"],
                historical_context["projected_hourly_volume_ratio"],
                spread_pct,
                slippage_pct,
                long_decision.score,
                short_decision.score,
                decision.confidence,
                audit_reason,
            )
            return

        if direction == "SHORT" and self.app_config.market_type not in {"swap", "future", "forex_cfd"}:
            self.logger.warning("Senal SHORT ignorada porque el mercado no es futures/swap")
            return

        free_balance = self.state.paper_balance_usdt
        usable_balance = free_balance * 0.90
        
        contract_size = self.client.get_contract_size(self.symbol)
        
        if self.app_config.exchange == "exness":
            quantity = self.app_config.mt5.trade_volume_lots
            trade_cost = quantity * contract_size * entry_price
            margin_to_use = trade_cost / leverage
        else:
            margin_to_use = min(
                usable_balance,
                max(self.account.trade_amount_usdt, free_balance * self.controller.get_capital_fraction()),
            )
            if margin_to_use < 1.0:
                self.logger.warning(
                    "Margen libre insuficiente para abrir con buffer | libre=%.4f usable=%.4f",
                    free_balance,
                    usable_balance,
                )
                return
            notional_to_use = margin_to_use * leverage
            quantity = self.client.normalize_amount(
                self.symbol,
                notional_to_use / entry_price,
            )
            trade_cost = quantity * contract_size * entry_price

        if quantity <= 0:
            self.logger.warning("Cantidad invalida calculada para %s", self.account.name)
            return

        if margin_to_use <= 0 or margin_to_use > usable_balance:
            self.logger.warning("Margen requerido invalido o insuficiente: %.4f (disponible: %.4f)", margin_to_use, usable_balance)
            return

        open_fee = trade_cost * self.app_config.strategy.open_fee_rate
        
        # Calcular SL y TP antes de abrir la orden para enviarlos al exchange (especialmente en MT5)
        atr_pct = max(float(row["atr_pct"]), 0.0001)
        stop_loss_pct = max(self.app_config.strategy.stop_loss_pct, atr_pct * self.app_config.strategy.atr_stop_loss_multiplier)
        take_profit_pct = max(
            self.app_config.strategy.take_profit_pct,
            atr_pct * self.app_config.strategy.atr_take_profit_multiplier,
        )
        regime_sl_factor = {
            "compresion": 0.8,
            "sobreextendido": 0.7,
            "tendencia_alcista": 1.2,
            "bajista": 1.2,
            "rango_constructivo": 1.0,
            "mixto": 0.9,
        }.get(market_context.get("market_regime", "normal"), 1.0)
        stop_loss_pct *= regime_sl_factor
        
        sl_price = entry_price * (1 - stop_loss_pct) if direction == "LONG" else entry_price * (1 + stop_loss_pct)
        tp_price = entry_price * (1 + take_profit_pct) if direction == "LONG" else entry_price * (1 - take_profit_pct)

        params = {
            "sl": sl_price,
            "tp": tp_price,
            "reason": f"bot_{direction.lower()}"
        }

        order_id = None
        if self.app_config.trading_mode == "real":
            if direction == "SHORT":
                order = self.client.create_market_sell(self.symbol, quantity, params=params)
            else:
                order = self.client.create_market_buy(self.symbol, quantity, params=params)
            order_id = str(order.get("id", ""))
            time.sleep(0.5)
            self._sync_wallet_balances(entry_price, force=True)
        else:
            total_debit = margin_to_use + open_fee
            if total_debit > self.state.paper_balance_usdt:
                self.logger.warning("Saldo insuficiente en paper para %s", self.account.name)
                return
            self.state.paper_balance_usdt -= total_debit
            self.state.paper_xrp_balance += quantity if direction == "LONG" else -quantity
            self.state.daily_realized_pnl -= open_fee

        position = self.strategy.build_position(
            account_name=self.account.name,
            symbol=self.symbol,
            quantity=quantity,
            entry_price=entry_price,
            cost_usdt=trade_cost,
            leverage=leverage,
            margin_used_usdt=margin_to_use,
            mode=self.app_config.trading_mode,
            row=row,
            side=direction,
            order_id=order_id,
            market_regime=str(market_context.get("risk_state", "normal")),
        )

        # --- Marcar alineación macro para salida inteligente (Componente 2) ---
        if direction == "LONG":
            position.macro_aligned = bool(
                market_context.get("trend_15m_aligned") and market_context.get("trend_1h_aligned")
            )
        else:
            position.macro_aligned = bool(
                market_context.get("trend_15m_bearish") and market_context.get("trend_1h_bearish")
            )

        self.storage.upsert_position(position)

        # --- Registrar entrada de score alto para cooldown (Componente 5) ---
        if decision.score >= self.app_config.high_score_threshold:
            self.state.last_high_score_entry_at = datetime.now(timezone.utc)

        context_json = json.dumps(
            {
                "score": decision.score,
                "direction": direction,
                "long_score": long_decision.score,
                "short_score": short_decision.score,
                "confidence": decision.confidence,
                "leverage": leverage,
                "margin_used": round(margin_to_use, 6),
                "trade_cost": round(trade_cost, 6),
                "volume_ratio": round(float(row["volume_ratio"]), 4),
                "projected_hourly_volume_ratio": round(historical_context["projected_hourly_volume_ratio"], 4),
                "history_return": round(historical_context["avg_hourly_return"], 6),
                "history_positive_ratio": round(historical_context["positive_hour_ratio"], 4),
                "market_regime": market_context.get("market_regime", "desconocido"),
                "risk_state": market_context.get("risk_state", "normal"),
                "trend_15m_aligned": bool(market_context.get("trend_15m_aligned", False)),
                "trend_1h_aligned": bool(market_context.get("trend_1h_aligned", False)),
                "zone_long_probability": round(float(market_context.get("zone_long_probability", 0.0)), 2),
                "zone_short_probability": round(float(market_context.get("zone_short_probability", 0.0)), 2),
                "zone_long_grade": int(market_context.get("zone_long_grade", 0)),
                "zone_short_grade": int(market_context.get("zone_short_grade", 0)),
                "zone_long_price": round(float(market_context.get("zone_long_price", 0.0)), 6),
                "zone_short_price": round(float(market_context.get("zone_short_price", 0.0)), 6),
                "next_long_liquidity": round(float(market_context.get("next_long_liquidity", 0.0)), 6),
                "next_short_liquidity": round(float(market_context.get("next_short_liquidity", 0.0)), 6),
                "next_long_liquidity_distance_pct": round(float(market_context.get("next_long_liquidity_distance_pct", 0.0)), 6),
                "next_short_liquidity_distance_pct": round(float(market_context.get("next_short_liquidity_distance_pct", 0.0)), 6),
                "session_direction": market_context.get("session_direction", "NEUTRAL"),
                "session_continuation_probability": round(float(market_context.get("session_continuation_probability", 50.0)), 2),
                "micro_continuation_probability": round(float(market_context.get("micro_continuation_probability", 50.0)), 2),
                "session_low_liquidity": bool(market_context.get("session_low_liquidity", False)),
                "session_neutralized": bool(market_context.get("session_neutralized", False)),
                "session_low_probability_block": bool(market_context.get("session_low_probability_block", False)),
                "session_low_probability_value": round(float(market_context.get("session_low_probability_value", 50.0)), 2),
                "session_gap_open": bool(market_context.get("session_gap_open", False)),
                "economic_holiday": bool(market_context.get("economic_holiday", False)),
                "session_no_trade": bool(market_context.get("session_no_trade", False)),
                "rsi_15m": round(float(market_context.get("rsi_15m", 0.0)), 2),
                "rsi_1h": round(float(market_context.get("rsi_1h", 0.0)), 2),
                "order_book_imbalance": round(float(market_context.get("order_book_imbalance", 0.0)), 4),
                "micro_spread_pct": round(float(market_context.get("micro_spread_pct", 0.0)), 6),
                "spread_pct": round(spread_pct, 6),
                "slippage_pct": round(slippage_pct, 6),
                "why_entered": decision.reason,
            },
            ensure_ascii=False,
        )
        self.storage.save_trade(
            account_name=self.account.name,
            symbol=self.symbol,
            side=direction,
            quantity=quantity,
            price=entry_price,
            reason="futures_short_entry" if direction == "SHORT" else ("futures_long_entry" if self.app_config.market_type in {"swap", "future", "forex_cfd"} else ("paper_leveraged_entry" if leverage > 1.0 else "spot_entry")),
            mode=self.app_config.trading_mode,
            order_id=order_id,
            context_json=context_json,
        )
        self.logger.info(
            "Entrada %s ejecutada | cuenta=%s lev=%.1fx qty=%.6f precio=%.5f margin=%.4f cost=%.4f fee=%.4f tp=%.5f sl=%.5f",
            direction,
            self.account.name,
            leverage,
            quantity,
            entry_price,
            margin_to_use,
            trade_cost,
            open_fee,
            position.take_profit_price,
            position.stop_loss_price,
        )
        if self.app_config.telegram.notify_buys:
            estimated_equity = self._estimated_equity(entry_price, position)
            scalp_lines = self._scalp_reference_lines(trade_cost, leverage)
            long_grade = int(market_context.get('zone_long_grade', 0))
            long_fuegos_str = f" ({long_grade} {'🔥' * long_grade if long_grade > 0 else 'fuegos'})"
            short_grade = int(market_context.get('zone_short_grade', 0))
            short_fuegos_str = f" ({short_grade} {'🔥' * short_grade if short_grade > 0 else 'fuegos'})"
            entry_emoji = "🟢" if direction == "LONG" else "🔴"

            self.notifier.send_lines(
                f"{entry_emoji} Entrada {direction} ejecutada",
                f"👤 Cuenta: `{self.account.name}`",
                f"⚙️ Modo: `{self.app_config.trading_mode}`",
                f"🪙 Par: `{self.symbol}`",
                f"🏗️ Mercado: `{self.app_config.market_type}` | Margen: `{self.app_config.margin_mode}`",
                f"⚡ Apalancamiento: `x{leverage:.1f}`",
                f"📦 Cantidad: `{quantity}`",
                f"🏷️ Entrada: `{entry_price:.5f}`",
                f"💰 Margen usado: `{self._display_value(margin_to_use)}`",
                f"💼 Exposición nocional: `{self._display_value(trade_cost)}`",
                f"💸 Fee apertura: `{self._display_value(open_fee)}`",
                f"🎯 TP base: `{position.take_profit_price:.5f}` | SL: `{position.stop_loss_price:.5f}`",
                f"📏 Spread: `{spread_pct:.4f}` | Slippage: `{slippage_pct:.4f}`",
                f"🔄 Régimen: `{market_context.get('market_regime', 'desconocido')}` | Riesgo: `{market_context.get('risk_state', 'normal')}`",
                f"⏱️ 15m alineado: `{bool(market_context.get('trend_15m_bearish' if direction == 'SHORT' else 'trend_15m_aligned', False))}` | 1h alineado: `{bool(market_context.get('trend_1h_bearish' if direction == 'SHORT' else 'trend_1h_aligned', False))}`",
                f"⏰ Sesión: `{market_context.get('session_direction', 'NEUTRAL')}` | cont `{float(market_context.get('session_continuation_probability', 50.0)):.0f}%` | micro `{float(market_context.get('micro_continuation_probability', 50.0)):.0f}%` | prob baja `{bool(market_context.get('session_low_probability_block', False))}` | no trade `{bool(market_context.get('session_no_trade', False))}`",
                f"🟢 Zona LONG: `{float(market_context.get('zone_long_probability', 0.0)):.0f}%`{long_fuegos_str} | demanda `{float(market_context.get('zone_long_price', 0.0)):.5f}` | liquidez `{float(market_context.get('next_long_liquidity', 0.0)):.5f}`",
                f"🔴 Zona SHORT: `{float(market_context.get('zone_short_probability', 0.0)):.0f}%`{short_fuegos_str} | oferta `{float(market_context.get('zone_short_price', 0.0)):.5f}` | liquidez `{float(market_context.get('next_short_liquidity', 0.0)):.5f}`",
                f"🕯️ God Candle: `{historical_context['projected_hourly_volume_ratio']:.2f}x`",
                f"🧠 Score IA: `{decision.score}` ({decision.confidence})",
                f"📈 Hist hora: ret `{historical_context['avg_hourly_return']:.4f}` | cierres positivos `{historical_context['positive_hour_ratio']:.2f}`",
                *scalp_lines,
                f"📈 Equity estimada: `{self._display_value(estimated_equity)}`",
                f"💬 Razón IA: {decision.reason}",
            )

    def _try_manage_position(self, position: Position, row) -> None:
        if self.controller.consume_force_close():
            ticker = self.client.fetch_ticker(self.symbol)
            exit_price = float(
                (ticker.get("ask") if position.side == "SHORT" else ticker.get("bid"))
                or ticker.get("last")
                or row["close"]
            )
            self._close_position_quantity(
                position=position,
                price=exit_price,
                quantity=position.quantity,
                reason="manual_force_close",
            )
            return

        evaluation = self.strategy.evaluate_position(position, row)
        self.storage.upsert_position(position)
        if not evaluation["should_close"]:
            self.logger.info(
                "Posicion abierta | cuenta=%s entrada=%.5f actual=%.5f sl=%.5f trail=%.5f qty=%.6f",
                self.account.name,
                position.entry_price,
                float(row["close"]),
                position.stop_loss_price,
                position.trailing_stop_price,
                position.quantity,
            )
            return

        ticker = self.client.fetch_ticker(self.symbol)
        exit_price = float(
            (ticker.get("ask") if position.side == "SHORT" else ticker.get("bid"))
            or ticker.get("last")
            or row["close"]
        )
        quantity = float(evaluation["quantity"])
        self._close_position_quantity(
            position=position,
            price=exit_price,
            quantity=quantity,
            reason=str(evaluation["reason"]),
        )

    def _close_position_quantity(
        self,
        position: Position,
        price: float,
        quantity: float,
        reason: str,
    ) -> None:
        quantity = min(quantity, position.quantity)
        order_id = None
        contract_size = self.client.get_contract_size(self.symbol)
        
        if self.app_config.trading_mode == "real":
            params = {"reduceOnly": True} if self.app_config.market_type in {"swap", "future", "forex_cfd"} else {}
            if position.side == "SHORT":
                order = self.client.create_market_buy(self.symbol, quantity, params=params)
            else:
                order = self.client.create_market_sell(self.symbol, quantity, params=params)
            order_id = str(order.get("id", ""))
            time.sleep(0.5)
            self._sync_wallet_balances(price, force=True)

        release_ratio = quantity / max(position.quantity, 1e-9)
        cost_released = position.cost_usdt * release_ratio
        margin_released = position.margin_used_usdt * release_ratio
        gross_proceeds = price * quantity * contract_size
        sell_fee = gross_proceeds * self.app_config.strategy.open_fee_rate
        pnl = (
            gross_proceeds - sell_fee - cost_released
            if position.side == "LONG"
            else cost_released - gross_proceeds - sell_fee
        )

        if position.side == "LONG":
            self.state.paper_xrp_balance = max(0.0, self.state.paper_xrp_balance - quantity)
        else:
            self.state.paper_xrp_balance = min(0.0, self.state.paper_xrp_balance + quantity)
        self.state.paper_balance_usdt += max(0.0, margin_released + pnl)
        self.state.daily_realized_pnl += pnl
        pnl_pct = (pnl / margin_released * 100) if margin_released > 0 else 0.0

        position.cost_usdt = max(0.0, position.cost_usdt - cost_released)
        position.margin_used_usdt = max(0.0, position.margin_used_usdt - margin_released)
        position.quantity = max(0.0, position.quantity - quantity)
        position.realized_pnl += pnl
        if reason.startswith("ladder_take_profit"):
            position.ladder_step += 1

        context_json = json.dumps(
            {
                "reason": reason,
                "side": position.side,
                "entry_price": round(position.entry_price, 6),
                "exit_price": round(price, 6),
                "leverage": round(position.leverage, 4),
                "margin_released": round(margin_released, 6),
                "remaining_qty": round(position.quantity, 8),
                "sell_fee": round(sell_fee, 6),
            },
            ensure_ascii=False,
        )
        self.storage.save_trade(
            account_name=self.account.name,
            symbol=self.symbol,
            side="CLOSE_SHORT" if position.side == "SHORT" else "CLOSE_LONG",
            quantity=quantity,
            price=price,
            reason=reason,
            mode=self.app_config.trading_mode,
            order_id=order_id,
            context_json=context_json,
            pnl=pnl,
        )

        if position.quantity <= 1e-12:
            self.storage.close_position(self.account.name, self.symbol)
            if (
                pnl > 0
                and (
                    pnl >= self.app_config.post_profit_min_pnl_usdt
                    or pnl_pct >= self.app_config.post_profit_min_pnl_pct
                )
            ):
                self.state.post_profit_cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.app_config.post_profit_cooldown_minutes
                )
                self.state.last_profitable_pnl = pnl
                self.logger.info(
                    "Cooldown post-ganancia activado | pnl=%.4f pct=%.2f%% hasta=%s",
                    pnl,
                    pnl_pct,
                    self.state.post_profit_cooldown_until.isoformat(),
                )
            elif pnl < 0:
                self.state.post_loss_cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.app_config.post_loss_cooldown_minutes
                )
                # --- Anti-espiral: rastrear pérdidas consecutivas por símbolo ---
                consecutive = self.state.consecutive_losses_by_symbol.get(self.symbol, 0) + 1
                self.state.consecutive_losses_by_symbol[self.symbol] = consecutive
                if consecutive >= 2:
                    block_minutes = 60
                    self.state.symbol_block_until[self.symbol] = datetime.now(timezone.utc) + timedelta(
                        minutes=block_minutes
                    )
                    self.logger.warning(
                        "Anti-espiral activado | %s tiene %d pérdidas consecutivas | bloqueado %d min",
                        self.symbol,
                        consecutive,
                        block_minutes,
                    )
                self.logger.info(
                    "Cooldown post-pérdida activado | pnl=%.4f pct=%.2f%% hasta=%s | consecutivas=%d",
                    pnl,
                    pnl_pct,
                    self.state.post_loss_cooldown_until.isoformat(),
                    consecutive,
                )
            else:
                # Resultado neutro o ganancia: resetear conteo de pérdidas consecutivas
                self.state.consecutive_losses_by_symbol[self.symbol] = 0
            self.logger.info(
                "Salida total %s ejecutada | cuenta=%s motivo=%s pnl=%.4f",
                position.side,
                self.account.name,
                reason,
                pnl,
            )
        else:
            self.storage.upsert_position(position)
            self.logger.info(
                "Salida parcial %s ejecutada | cuenta=%s motivo=%s qty=%.6f restante=%.6f pnl=%.4f",
                position.side,
                self.account.name,
                reason,
                quantity,
                position.quantity,
                pnl,
            )

        if self.app_config.telegram.notify_sells:
            estimated_equity = self._estimated_equity(
                price,
                None if position.quantity <= 1e-12 else position,
            )
            pnl_emoji = "✅" if pnl > 0 else "❌"
            self.notifier.send_lines(
                f"{pnl_emoji} Salida ejecutada",
                f"👤 Cuenta: `{self.account.name}`",
                f"⚙️ Modo: `{self.app_config.trading_mode}`",
                f"🪙 Par: `{self.symbol}`",
                f"↔️ Lado: `{position.side}`",
                f"❓ Motivo: `{reason}`",
                f"⚡ Apalancamiento: `x{position.leverage:.1f}`",
                f"🏷️ Precio: `{price:.5f}`",
                f"📦 Cantidad cerrada: `{quantity:.6f}`",
                f"💰 Margen liberado: `{self._display_value(margin_released)}`",
                f"💵 PnL realizado: `{self._display_value(pnl)}`",
                f"📊 PnL sobre margen: `{pnl_pct:.2f}%`",
                f"💸 Fee salida: `{self._display_value(sell_fee)}`",
                f"📈 PnL diario: `{self._display_value(self.state.daily_realized_pnl)}`",
                f"⏳ Cooldown: Ganancia={self._post_profit_cooldown_active()} ({self._post_profit_cooldown_remaining_minutes():.1f}m) | Pérdida={self._post_loss_cooldown_active()} ({self._post_loss_cooldown_remaining_minutes():.1f}m)",
                f"💰 Balance libre: `{self._display_value(self.state.paper_balance_usdt)}`",
                f"📈 Equity estimada: `{self._display_value(estimated_equity)}`",
            )
