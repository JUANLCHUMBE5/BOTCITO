from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from bot.ai_filter import SignalDecision, SignalEngine
from bot.config import StrategyConfig


@dataclass
class Position:
    account_name: str
    symbol: str
    quantity: float
    initial_quantity: float
    entry_price: float
    cost_usdt: float
    stop_loss_price: float
    take_profit_price: float
    mode: str
    side: str = "LONG"
    leverage: float = 1.0
    margin_used_usdt: float = 0.0
    liquidation_price: float = 0.0
    highest_price: float = 0.0
    trailing_stop_price: float = 0.0
    breakeven_armed: bool = False
    ladder_step: int = 0
    realized_pnl: float = 0.0
    opened_at: str | None = None
    last_funding_at: str | None = None
    status: str = "OPEN"
    entry_order_id: str | None = None
    macro_aligned: bool = False


class MomentumStrategy:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self.advisor = SignalEngine()

    def select_leverage(self, decision_score: float) -> float:
        capped_max = min(self.config.leverage_max, self.config.leverage_cap)
        capped_mid = min(self.config.leverage_mid, capped_max)
        capped_min = min(self.config.leverage_min, capped_mid)

        if decision_score >= self.config.leverage_high_score:
            return max(1.0, capped_max)
        if decision_score >= self.config.leverage_mid_score:
            return max(1.0, capped_mid)
        return max(1.0, capped_min)

    def estimate_liquidation_price(self, entry_price: float, leverage: float) -> float:
        if leverage <= 1.0:
            return 0.0
        liquidation_factor = max(
            0.0,
            1 - (1 / leverage) + self.config.maintenance_margin_pct,
        )
        return entry_price * liquidation_factor

    def estimate_short_liquidation_price(self, entry_price: float, leverage: float) -> float:
        if leverage <= 1.0:
            return 0.0
        liquidation_factor = max(1.0, 1 + (1 / leverage) - self.config.maintenance_margin_pct)
        return entry_price * liquidation_factor

    def _build_rebound_signal(self, row) -> bool:
        if not self.config.rebound_enabled:
            return False

        close_price = float(row["close"])
        ema_20 = float(row["ema_20"])
        ema_50 = float(row["ema_50"])
        ema_200 = float(row["ema_200"])
        rsi = float(row["rsi_14"])
        volume_ratio = float(row["volume_ratio"])
        atr_pct = float(row["atr_pct"])
        macd = float(row["macd"])
        macd_signal = float(row["macd_signal"])
        distance_from_ema20_pct = float(row["distance_from_ema20_pct"])
        candle_body_pct = float(row["candle_body_pct"])

        trend_ok = ema_50 > ema_200 and close_price >= ema_50 * self.config.rebound_trend_strength_ratio
        price_recovered = close_price >= ema_20 * (1 - self.config.rebound_max_distance_from_ema20_pct)
        rsi_ok = self.config.rebound_rsi_min <= rsi <= self.config.rebound_rsi_max
        macd_ok = macd >= self.config.rebound_macd_threshold and macd >= macd_signal * 0.85
        volume_ok = volume_ratio >= self.config.rebound_volume_ratio_min
        volatility_ok = atr_pct >= self.config.rebound_min_atr_pct
        distance_ok = abs(distance_from_ema20_pct) <= self.config.rebound_max_distance_from_ema20_pct

        return bool(
            trend_ok
            and price_recovered
            and rsi_ok
            and macd_ok
            and volume_ok
            and volatility_ok
            and distance_ok
            and candle_body_pct <= self.config.max_candle_pct
        )

    def should_buy(
        self,
        row,
        historical_context: dict[str, float] | None = None,
        market_context: dict[str, float | str | bool] | None = None,
        history_weight: float = 1.0,
    ) -> tuple[bool, SignalDecision]:
        historical_context = historical_context or {}
        market_context = market_context or {}
        vertical_breakout = (
            float(row["close"]) > float(row["breakout_high_20"])
            and float(row["volume_ratio"]) >= self.config.rvol_override_threshold
            and float(row["momentum_5"]) >= self.config.momentum_threshold * 2
            and float(row["macd"]) > float(row["macd_signal"])
        )
        god_candle = float(historical_context.get("projected_hourly_volume_ratio", 0.0)) >= self.config.god_candle_volume_multiplier
        trend_1h_aligned = bool(market_context.get("trend_1h_aligned", False))
        trend_15m_aligned = bool(market_context.get("trend_15m_aligned", False))
        macd_15m_bullish = bool(market_context.get("macd_15m_bullish", False))
        above_15m_vwap = bool(market_context.get("above_15m_vwap", False))
        market_regime = str(market_context.get("market_regime", "desconocido"))
        order_book_imbalance = float(market_context.get("order_book_imbalance", 0.0))
        micro_liquidity_ok = bool(market_context.get("micro_liquidity_ok", True))
        zone_long_active = bool(market_context.get("zone_long_active", False))
        zone_long_probability = float(market_context.get("zone_long_probability", 0.0))
        next_long_liquidity_distance = float(market_context.get("next_long_liquidity_distance_pct", 0.0))
        next_long_liquidity_induced = bool(market_context.get("next_long_liquidity_induced", True))
        session_direction = str(market_context.get("session_direction", "NEUTRAL"))
        session_probability = float(market_context.get("session_continuation_probability", 50.0))
        micro_probability = float(market_context.get("micro_continuation_probability", 50.0))
        low_probability_block = bool(market_context.get("session_low_probability_block", False))
        breakout_ok = (
            not self.config.require_breakout
            or float(row["close"]) > float(row["breakout_high_20"])
            or (trend_1h_aligned and trend_15m_aligned and float(row["close"]) > float(row["ema_20"]))
        )
        rsi_ok = (
            self.config.rsi_buy_min <= row["rsi_14"] <= self.config.rsi_buy_max
            or vertical_breakout
            or god_candle
        )
        professional_continuation = (
            trend_1h_aligned
            and trend_15m_aligned
            and macd_15m_bullish
            and above_15m_vwap
            and row["close"] > row["ema_20"] >= row["ema_50"]
            and row["volume_ratio"] >= 0.95
            and row["momentum_5"] >= -0.0005
            and row["distance_from_ema20_pct"] <= self.config.max_distance_from_ema20_pct
            and row["candle_body_pct"] <= self.config.max_candle_pct
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and order_book_imbalance >= -0.12
            and micro_liquidity_ok
        )
        professional_breakout = (
            trend_1h_aligned
            and float(row["close"]) > float(row["breakout_high_50"])
            and row["volume_ratio"] >= self.config.volume_spike_factor
            and row["macd"] > row["macd_signal"]
            and row["momentum_5"] >= self.config.momentum_threshold
            and order_book_imbalance >= -0.05
            and micro_liquidity_ok
        )
        validated_zone_reaction = (
            zone_long_active
            and zone_long_probability >= 70
            and next_long_liquidity_distance >= 0.0025
            and not next_long_liquidity_induced
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and order_book_imbalance >= -0.18
            and micro_liquidity_ok
        )
        session_continuation = (
            session_direction == "LONG"
            and session_probability >= 60
            and micro_probability >= 55
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and micro_liquidity_ok
        )
        base_signal = (
            row["close"] > row["ema_20"] > row["ema_50"] > row["ema_200"]
            and row["volume_ratio"] >= self.config.volume_spike_factor
            and row["momentum_5"] >= self.config.momentum_threshold
            and row["macd"] > row["macd_signal"]
            and rsi_ok
            and row["candle_body_pct"] <= self.config.max_candle_pct
            and row["distance_from_ema20_pct"] <= self.config.max_distance_from_ema20_pct
            and row["atr_pct"] >= self.config.min_atr_pct
            and breakout_ok
        )
        rebound_signal = self._build_rebound_signal(row)
        decision = self.advisor.evaluate(
            row,
            historical_context=historical_context,
            market_context=market_context,
            history_weight=history_weight,
        )

        setup_reasons: list[str] = []
        if base_signal:
            setup_reasons.append("breakout_momentum")
        if rebound_signal:
            setup_reasons.append("rebound_trend")
        if professional_continuation:
            setup_reasons.append("professional_continuation")
        if professional_breakout:
            setup_reasons.append("professional_breakout")
        if validated_zone_reaction:
            setup_reasons.append("validated_demand_zone")
        if session_continuation:
            setup_reasons.append("session_continuation")

        vetoes: list[str] = []
        if market_regime == "bajista" and not (vertical_breakout or god_candle):
            vetoes.append("régimen mayor bajista")
        if float(market_context.get("rsi_1h", 50.0)) > 82 and not god_candle:
            vetoes.append("RSI 1h extremo")
        if float(row["distance_from_ema20_pct"]) > self.config.max_distance_from_ema20_pct and not god_candle:
            vetoes.append("precio lejos de EMA20")
        if float(row["upper_wick_pct"]) > float(row["candle_body_pct"]) * 1.8 and float(row["volume_ratio"]) > 1.2:
            vetoes.append("rechazo superior con volumen")
        if float(row["atr_pct"]) < self.config.rebound_min_atr_pct:
            vetoes.append("volatilidad insuficiente")
        if order_book_imbalance <= -0.25:
            vetoes.append("muro vendedor fuerte")
        if not micro_liquidity_ok:
            vetoes.append("liquidez micro insuficiente")
        if low_probability_block:
            vetoes.append("probabilidad baja no trade")
        if bool(market_context.get("zone_long_low_probability_block", False)):
            vetoes.append("zona demanda 40/45% no trade")
        elif zone_long_active and 0 < zone_long_probability < 50:
            vetoes.append("zona demanda debil")
        if zone_long_active and next_long_liquidity_induced:
            vetoes.append("liquidez superior inducida")
        if zone_long_active and next_long_liquidity_distance < 0.0015:
            vetoes.append("sin recorrido a liquidez")
        if bool(market_context.get("session_no_trade", False)):
            vetoes.append("sesion neutralizada")
        if session_direction == "SHORT" and not (vertical_breakout or god_candle):
            vetoes.append("sesion favorece short")

        if setup_reasons:
            decision.reason = f"{decision.reason}; setup={','.join(setup_reasons)}"
        if vetoes:
            decision.reason = f"{decision.reason}; veto={','.join(vetoes)}"

        has_setup = bool(
            base_signal
            or rebound_signal
            or professional_continuation
            or professional_breakout
            or validated_zone_reaction
            or session_continuation
        )
        return bool(has_setup and decision.approved and not vetoes), decision

    def should_short(
        self,
        row,
        historical_context: dict[str, float] | None = None,
        market_context: dict[str, float | str | bool] | None = None,
        history_weight: float = 1.0,
    ) -> tuple[bool, SignalDecision]:
        historical_context = historical_context or {}
        market_context = market_context or {}
        trend_1h_bearish = bool(market_context.get("trend_1h_bearish", False))
        trend_15m_bearish = bool(market_context.get("trend_15m_bearish", False))
        macd_15m_bearish = bool(market_context.get("macd_15m_bearish", False))
        below_15m_vwap = bool(market_context.get("below_15m_vwap", False))
        market_regime = str(market_context.get("market_regime", "desconocido"))
        order_book_imbalance = float(market_context.get("order_book_imbalance", 0.0))
        micro_liquidity_ok = bool(market_context.get("micro_liquidity_ok", True))
        zone_short_active = bool(market_context.get("zone_short_active", False))
        zone_short_probability = float(market_context.get("zone_short_probability", 0.0))
        next_short_liquidity_distance = float(market_context.get("next_short_liquidity_distance_pct", 0.0))
        next_short_liquidity_induced = bool(market_context.get("next_short_liquidity_induced", True))
        session_direction = str(market_context.get("session_direction", "NEUTRAL"))
        session_probability = float(market_context.get("session_continuation_probability", 50.0))
        micro_probability = float(market_context.get("micro_continuation_probability", 50.0))
        low_probability_block = bool(market_context.get("session_low_probability_block", False))

        vertical_breakdown = (
            float(row["close"]) < float(row["breakout_low_20"])
            and float(row["volume_ratio"]) >= self.config.rvol_override_threshold
            and float(row["momentum_5"]) <= -self.config.momentum_threshold * 2
            and float(row["macd"]) < float(row["macd_signal"])
        )
        breakdown_setup = (
            row["close"] < row["ema_20"] < row["ema_50"] < row["ema_200"]
            and row["volume_ratio"] >= self.config.volume_spike_factor
            and row["momentum_5"] <= -self.config.momentum_threshold
            and row["macd"] < row["macd_signal"]
            and row["candle_body_pct"] <= self.config.max_candle_pct
            and abs(float(row["distance_from_ema20_pct"])) <= self.config.max_distance_from_ema20_pct
            and row["atr_pct"] >= self.config.min_atr_pct
        )
        professional_continuation = (
            trend_1h_bearish
            and trend_15m_bearish
            and macd_15m_bearish
            and below_15m_vwap
            and row["close"] < row["ema_20"] <= row["ema_50"]
            and row["volume_ratio"] >= 0.95
            and row["momentum_5"] <= 0.0005
            and row["candle_body_pct"] <= self.config.max_candle_pct
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and order_book_imbalance <= 0.12
            and micro_liquidity_ok
        )
        professional_breakdown = (
            trend_1h_bearish
            and float(row["close"]) < float(row["breakout_low_50"])
            and row["volume_ratio"] >= self.config.volume_spike_factor
            and row["macd"] < row["macd_signal"]
            and row["momentum_5"] <= -self.config.momentum_threshold
            and order_book_imbalance <= 0.05
            and micro_liquidity_ok
        )
        crash_breakdown = (
            float(row["close"]) < float(row["breakout_low_20"])
            and row["volume_ratio"] >= 1.6
            and row["momentum_5"] <= -self.config.momentum_threshold * 1.5
            and row["macd"] < row["macd_signal"]
            and (trend_15m_bearish or trend_1h_bearish or session_direction == "SHORT")
            and order_book_imbalance < 0.45
            and micro_liquidity_ok
        )
        late_short_chase = (
            float(row["rsi_14"]) < 18
            or float(row["distance_from_ema20_pct"]) < -0.007
        )
        extreme_short_overextension = (
            float(row["rsi_14"]) < 16
            or float(row["distance_from_ema20_pct"]) < -0.009
        )
        validated_zone_rejection = (
            zone_short_active
            and zone_short_probability >= 70
            and next_short_liquidity_distance >= 0.0025
            and not next_short_liquidity_induced
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and order_book_imbalance <= 0.18
            and micro_liquidity_ok
        )
        session_continuation = (
            session_direction == "SHORT"
            and session_probability >= 60
            and micro_probability >= 55
            and row["atr_pct"] >= self.config.rebound_min_atr_pct
            and micro_liquidity_ok
        )

        decision = self.advisor.evaluate_short(
            row,
            historical_context=historical_context,
            market_context=market_context,
            history_weight=history_weight,
        )

        setup_reasons: list[str] = []
        if breakdown_setup:
            setup_reasons.append("breakdown_momentum")
        if professional_continuation:
            setup_reasons.append("short_continuation")
        if professional_breakdown:
            setup_reasons.append("professional_breakdown")
        if crash_breakdown:
            setup_reasons.append("crash_breakdown")
        if validated_zone_rejection:
            setup_reasons.append("validated_supply_zone")
        if session_continuation:
            setup_reasons.append("session_continuation")

        vetoes: list[str] = []
        if market_regime == "tendencia_alcista" and not vertical_breakdown:
            vetoes.append("regimen mayor alcista")
        if float(market_context.get("rsi_1h", 50.0)) < 22 and not vertical_breakdown:
            vetoes.append("RSI 1h sobrevendido")
        if float(row["distance_from_ema20_pct"]) < -self.config.max_distance_from_ema20_pct and not vertical_breakdown:
            vetoes.append("precio lejos bajo EMA20")
        if float(row["lower_wick_pct"]) > float(row["candle_body_pct"]) * 1.8 and float(row["volume_ratio"]) > 1.2 and not crash_breakdown:
            vetoes.append("rechazo inferior con volumen")
        if float(row["atr_pct"]) < self.config.rebound_min_atr_pct:
            vetoes.append("volatilidad insuficiente")
        if order_book_imbalance >= 0.25 and not crash_breakdown:
            vetoes.append("muro comprador fuerte")
        if not micro_liquidity_ok:
            vetoes.append("liquidez micro insuficiente")
        if low_probability_block:
            vetoes.append("probabilidad baja no trade")
        if bool(market_context.get("zone_short_low_probability_block", False)):
            vetoes.append("zona oferta 40/45% no trade")
        elif zone_short_active and 0 < zone_short_probability < 50:
            vetoes.append("zona oferta debil")
        if zone_short_active and next_short_liquidity_induced and not crash_breakdown:
            vetoes.append("liquidez inferior inducida")
        if zone_short_active and next_short_liquidity_distance < 0.0015 and not crash_breakdown:
            vetoes.append("sin recorrido a liquidez inferior")
        if bool(market_context.get("session_no_trade", False)):
            vetoes.append("sesion neutralizada")
        if session_direction == "LONG" and not vertical_breakdown:
            vetoes.append("sesion favorece long")
        if late_short_chase and not crash_breakdown:
            vetoes.append("short tarde/sobrevendido")
        if extreme_short_overextension:
            vetoes.append("short extremo sobrevendido")

        if setup_reasons:
            decision.reason = f"{decision.reason}; setup={','.join(setup_reasons)}"
        if vetoes:
            decision.reason = f"{decision.reason}; veto={','.join(vetoes)}"

        has_setup = bool(
            breakdown_setup
            or professional_continuation
            or professional_breakdown
            or crash_breakdown
            or validated_zone_rejection
            or session_continuation
        )
        return bool(has_setup and decision.approved and not vetoes), decision

    def build_position(
        self,
        account_name: str,
        symbol: str,
        quantity: float,
        entry_price: float,
        cost_usdt: float,
        leverage: float,
        margin_used_usdt: float,
        mode: str,
        row,
        side: str = "LONG",
        order_id: str | None = None,
        market_regime: str = "normal",
    ) -> Position:
        atr_pct = max(float(row["atr_pct"]), 0.0001)
        stop_loss_pct = max(self.config.stop_loss_pct, atr_pct * self.config.atr_stop_loss_multiplier)
        take_profit_pct = max(
            self.config.take_profit_pct,
            atr_pct * self.config.atr_take_profit_multiplier,
        )

        # --- Stop-Loss Dinámico por Régimen (Componente 3) ---
        regime_sl_factor = {
            "compresion": 0.8,
            "sobreextendido": 0.7,
            "tendencia_alcista": 1.2,
            "bajista": 1.2,
            "rango_constructivo": 1.0,
            "mixto": 0.9,
        }.get(market_regime, 1.0)
        stop_loss_pct *= regime_sl_factor
        now_iso = datetime.now(timezone.utc).isoformat()
        return Position(
            account_name=account_name,
            symbol=symbol,
            quantity=quantity,
            initial_quantity=quantity,
            entry_price=entry_price,
            cost_usdt=cost_usdt,
            stop_loss_price=entry_price * (1 - stop_loss_pct) if side == "LONG" else entry_price * (1 + stop_loss_pct),
            take_profit_price=entry_price * (1 + take_profit_pct) if side == "LONG" else entry_price * (1 - take_profit_pct),
            mode=mode,
            side=side,
            leverage=leverage,
            margin_used_usdt=margin_used_usdt,
            liquidation_price=self.estimate_liquidation_price(entry_price, leverage) if side == "LONG" else self.estimate_short_liquidation_price(entry_price, leverage),
            highest_price=entry_price,
            trailing_stop_price=entry_price * (1 - self.config.trailing_distance_pct) if side == "LONG" else entry_price * (1 + self.config.trailing_distance_pct),
            opened_at=now_iso,
            last_funding_at=None,
            entry_order_id=order_id,
        )

    def evaluate_position(self, position: Position, row) -> dict[str, float | str | bool]:
        price = float(row["close"])
        if position.side == "SHORT":
            return self._evaluate_short_position(position, row)

        position.highest_price = max(position.highest_price, price)

        if (
            not position.breakeven_armed
            and price >= position.entry_price * (1 + self.config.breakeven_trigger_pct)
        ):
            position.breakeven_armed = True
            position.stop_loss_price = max(position.stop_loss_price, position.entry_price)

        if price >= position.entry_price * (1 + self.config.trailing_activation_pct):
            trailing_candidate = position.highest_price * (1 - self.config.trailing_distance_pct)
            position.trailing_stop_price = max(position.trailing_stop_price, trailing_candidate)
            position.stop_loss_price = max(position.stop_loss_price, position.trailing_stop_price)

        peak_profit_pct = (
            (position.highest_price - position.entry_price) / position.entry_price
            if position.entry_price > 0
            else 0.0
        )
        current_profit_pct = (
            (price - position.entry_price) / position.entry_price
            if position.entry_price > 0
            else 0.0
        )
        giveback_floor_pct = peak_profit_pct * (1 - self.config.trailing_profit_giveback_ratio)
        if (
            peak_profit_pct >= self.config.trailing_activation_pct
            and current_profit_pct <= giveback_floor_pct
        ):
            return {
                "should_close": True,
                "reason": "profit_giveback_exit",
                "quantity": position.quantity,
            }

        if position.ladder_step < len(self.config.take_profit_ladder):
            ladder = self.config.take_profit_ladder[position.ladder_step]
            target_price = position.entry_price * (1 + ladder.target_pct)
            if price >= target_price:
                quantity_to_close = min(
                    position.quantity,
                    position.initial_quantity * ladder.close_ratio,
                )
                return {
                    "should_close": True,
                    "reason": f"ladder_take_profit_{position.ladder_step + 1}",
                    "quantity": quantity_to_close,
                }

        # --- Salida rápida en micro-tendencia sin soporte macro (Componente 2) ---
        if (
            not position.macro_aligned
            and price < row["ema_20"]
            and row["macd"] < row["macd_signal"]
            and current_profit_pct > -self.config.stop_loss_pct * 0.75
        ):
            return {"should_close": True, "reason": "micro_trend_lost", "quantity": position.quantity}

        if price > position.entry_price * 1.004 and price < row["ema_20"] and row["macd"] < row["macd_signal"]:
            return {"should_close": True, "reason": "trend_lost_after_profit", "quantity": position.quantity}
        if (
            position.liquidation_price > 0
            and price <= position.liquidation_price * (1 + self.config.pre_liquidation_buffer_pct)
        ):
            return {"should_close": True, "reason": "pre_liquidation_exit", "quantity": position.quantity}
        if (
            current_profit_pct >= self.config.support_break_min_profit_pct
            and price < row["ema_20"]
            and row["close"] < row["breakout_low_20"]
        ):
            return {"should_close": True, "reason": "support_break_exit", "quantity": position.quantity}
        if row["macd"] < row["macd_signal"] and row["rsi_14"] < 40 and price < row["ema_20"] * 0.999:
            return {"should_close": True, "reason": "reversal_exit", "quantity": position.quantity}
        if price <= position.stop_loss_price:
            return {"should_close": True, "reason": "stop_loss", "quantity": position.quantity}
        if price >= position.take_profit_price and position.ladder_step >= len(self.config.take_profit_ladder):
            return {"should_close": True, "reason": "take_profit", "quantity": position.quantity}

        return {"should_close": False, "reason": "", "quantity": 0.0}

    def _evaluate_short_position(self, position: Position, row) -> dict[str, float | str | bool]:
        price = float(row["close"])
        position.highest_price = min(position.highest_price or position.entry_price, price)

        if (
            not position.breakeven_armed
            and price <= position.entry_price * (1 - self.config.breakeven_trigger_pct)
        ):
            position.breakeven_armed = True
            position.stop_loss_price = min(position.stop_loss_price, position.entry_price)

        peak_profit_pct = (
            (position.entry_price - position.highest_price) / position.entry_price
            if position.entry_price > 0
            else 0.0
        )
        current_profit_pct = (
            (position.entry_price - price) / position.entry_price
            if position.entry_price > 0
            else 0.0
        )
        if peak_profit_pct >= self.config.trailing_activation_pct:
            giveback_floor_pct = peak_profit_pct * (1 - self.config.trailing_profit_giveback_ratio)
            if current_profit_pct <= giveback_floor_pct:
                return {
                    "should_close": True,
                    "reason": "short_profit_giveback_exit",
                    "quantity": position.quantity,
                }

        # --- Salida rápida en micro-tendencia SHORT sin soporte macro (Componente 2) ---
        if (
            not position.macro_aligned
            and price > row["ema_20"]
            and row["macd"] > row["macd_signal"]
            and current_profit_pct > -self.config.stop_loss_pct * 0.75
        ):
            return {"should_close": True, "reason": "short_micro_trend_lost", "quantity": position.quantity}

        if price < position.entry_price * 0.996 and price > row["ema_20"] and row["macd"] > row["macd_signal"]:
            return {"should_close": True, "reason": "short_trend_lost_after_profit", "quantity": position.quantity}
        if (
            position.liquidation_price > 0
            and price >= position.liquidation_price * (1 - self.config.pre_liquidation_buffer_pct)
        ):
            return {"should_close": True, "reason": "short_pre_liquidation_exit", "quantity": position.quantity}
        if (
            current_profit_pct >= self.config.support_break_min_profit_pct
            and price > row["ema_20"]
            and row["close"] > row["breakout_high_20"]
        ):
            return {"should_close": True, "reason": "short_resistance_break_exit", "quantity": position.quantity}
        if row["macd"] > row["macd_signal"] and row["rsi_14"] > 65 and price > row["ema_20"] * 1.003:
            return {"should_close": True, "reason": "short_reversal_exit", "quantity": position.quantity}
        if price >= position.stop_loss_price:
            return {"should_close": True, "reason": "short_stop_loss", "quantity": position.quantity}
        if price <= position.take_profit_price:
            return {"should_close": True, "reason": "short_take_profit", "quantity": position.quantity}

        return {"should_close": False, "reason": "", "quantity": 0.0}
