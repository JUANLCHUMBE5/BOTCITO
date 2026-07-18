from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SignalDecision:
    approved: bool
    score: float
    confidence: str
    reason: str


class SignalEngine:
    """Motor local de decisión tipo IA básica, sin APIs externas."""

    def evaluate(
        self,
        row,
        historical_context: dict[str, float] | None = None,
        history_weight: float = 1.0,
        market_context: dict[str, float | str | bool] | None = None,
    ) -> SignalDecision:
        score = 0.0
        reasons: list[str] = []
        historical_context = historical_context or {}
        market_context = market_context or {}
        history_weight = max(0.0, min(1.5, history_weight))

        if row["close"] > row["ema_20"]:
            score += 12
            reasons.append("precio sobre EMA20")
        if row["ema_20"] > row["ema_50"]:
            score += 12
            reasons.append("EMA20 sobre EMA50")
        if row["ema_50"] > row["ema_200"]:
            score += 12
            reasons.append("EMA50 sobre EMA200")
        if row["macd"] > row["macd_signal"]:
            score += 12
            reasons.append("MACD alcista")
        if row["macd_hist"] > 0:
            score += 8
            reasons.append("histograma MACD positivo")
        if 50 <= row["rsi_14"] <= 66:
            score += 12
            reasons.append("RSI sano")
        elif 66 < row["rsi_14"] <= 76:
            score += 6
            reasons.append("RSI alto pero aceptable")
        if row["volume_ratio"] >= 1.5:
            score += 12
            reasons.append("volumen fuerte confirma")
        elif row["volume_ratio"] >= 1.0:
            score += 6
            reasons.append("volumen suficiente")
        if row["momentum_5"] >= 0.0025:
            score += 10
            reasons.append("momentum positivo")
        elif row["momentum_5"] >= -0.0005 and row["close"] >= row["ema_50"]:
            score += 4
            reasons.append("momentum estable sobre EMA50")
        if row["atr_pct"] >= 0.0015:
            score += 5
            reasons.append("volatilidad útil")
        elif row["atr_pct"] >= 0.00025:
            score += 3
            reasons.append("volatilidad suficiente para rebote")
        if row["close"] > row["breakout_high_20"]:
            score += 10
            reasons.append("rompe máximo reciente")
        if (
            row["ema_50"] > row["ema_200"]
            and row["close"] >= row["ema_50"] * 0.997
            and 38 <= row["rsi_14"] <= 58
            and row["distance_from_ema20_pct"] >= -0.012
        ):
            score += 18
            reasons.append("rebote técnico sobre tendencia mayor")

        market_regime = str(market_context.get("market_regime", "desconocido"))
        if market_regime == "tendencia_alcista":
            score += 14
            reasons.append("régimen mayor alcista")
        elif market_regime == "rango_constructivo":
            score += 7
            reasons.append("rango constructivo")
        elif market_regime == "bajista":
            score -= 22
            reasons.append("régimen mayor bajista")

        trend_1h_aligned = bool(market_context.get("trend_1h_aligned", False))
        trend_15m_aligned = bool(market_context.get("trend_15m_aligned", False))
        macd_1h_bullish = bool(market_context.get("macd_1h_bullish", False))
        macd_15m_bullish = bool(market_context.get("macd_15m_bullish", False))

        if trend_1h_aligned:
            score += 10
            reasons.append("1h alineado")
        if trend_15m_aligned:
            score += 8
            reasons.append("15m alineado")
        if macd_1h_bullish:
            score += 5
            reasons.append("MACD 1h acompaña")
        if macd_15m_bullish:
            score += 4
            reasons.append("MACD 15m acompaña")
        if bool(market_context.get("above_15m_vwap", False)):
            score += 4
            reasons.append("sobre VWAP 15m")

        # --- Filtro de alineación multi-TF (Componente 1) ---
        if not trend_15m_aligned and not trend_1h_aligned:
            score -= 15
            reasons.append("sin alineación timeframe mayor")
        elif not trend_1h_aligned:
            score -= 8
            reasons.append("1h no alinea")
        if trend_1h_aligned and trend_15m_aligned and macd_1h_bullish and macd_15m_bullish:
            score += 6
            reasons.append("stack completo multi-TF")

        zone_probability = float(market_context.get("zone_long_probability", 0.0))
        zone_active = bool(market_context.get("zone_long_active", False))
        zone_grade = int(market_context.get("zone_long_grade", 0))
        liquidity_distance = float(market_context.get("next_long_liquidity_distance_pct", 0.0))
        liquidity_induced = bool(market_context.get("next_long_liquidity_induced", True))
        if zone_active and zone_probability >= 75:
            score += 16
            reasons.append(f"zona demanda {zone_probability:.0f}% ({zone_grade} fuegos)")
        elif zone_active and zone_probability >= 65:
            score += 10
            reasons.append(f"zona demanda {zone_probability:.0f}%")
        elif zone_active and 0 < zone_probability < 50:
            score -= 10
            reasons.append("zona demanda poco fiable")
        if bool(market_context.get("zone_long_low_probability_block", False)):
            score -= 18
            reasons.append("zona demanda 40/45%: observar")
        if liquidity_distance >= 0.006 and not liquidity_induced:
            score += 8
            reasons.append("liquidez superior con recorrido")
        elif zone_active and liquidity_distance < 0.002:
            score -= 8
            reasons.append("poco recorrido a liquidez")
        if liquidity_induced and zone_active:
            score -= 6
            reasons.append("liquidez superior inducida")

        order_book_imbalance = float(market_context.get("order_book_imbalance", 0.0))
        if order_book_imbalance >= 0.12:
            score += 8
            reasons.append("presión compradora en libro")
        elif order_book_imbalance <= -0.20:
            score -= 12
            reasons.append("muro vendedor en libro")

        micro_spread_pct = float(market_context.get("micro_spread_pct", 0.0))
        if 0 < micro_spread_pct <= 0.0004:
            score += 3
            reasons.append("spread micro ajustado")

        htf_rsi = float(market_context.get("rsi_1h", 50.0))
        rsi_15m = float(market_context.get("rsi_15m", 50.0))
        if 45 <= htf_rsi <= 68:
            score += 5
            reasons.append("RSI 1h sano")
        elif htf_rsi > 78:
            score -= 12
            reasons.append("1h sobreextendido")

        # --- RSI Multi-Timeframe Guard (Componente 4) ---
        if rsi_15m > 72 and row["rsi_14"] > 66:
            score -= 10
            reasons.append("sobrecompra cruzada 1m+15m")
        if htf_rsi > 72 and rsi_15m > 68:
            score -= 12
            reasons.append("sobrecompra multi-TF confirmada")

        risk_state = str(market_context.get("risk_state", "normal"))
        if risk_state == "sobreextendido":
            score -= 12
            reasons.append("entrada perseguida")
        elif risk_state == "compresion":
            score += 4
            reasons.append("compresión previa")

        if bool(market_context.get("session_low_probability_block", False)):
            low_probability_value = float(market_context.get("session_low_probability_value", 50.0))
            score -= 35
            reasons.append(f"probabilidad baja {low_probability_value:.0f}%: no trade")
        elif bool(market_context.get("session_no_trade", False)):
            score -= 30
            reasons.append("sesion neutralizada sin trade")
        elif str(market_context.get("session_direction", "NEUTRAL")) == "LONG":
            session_probability = float(market_context.get("session_continuation_probability", 50.0))
            micro_probability = float(market_context.get("micro_continuation_probability", 50.0))
            if session_probability >= 60 and micro_probability >= 55:
                score += 10
                reasons.append(f"continuacion sesion {session_probability:.0f}%")
        elif str(market_context.get("session_direction", "NEUTRAL")) == "SHORT":
            score -= 10
            reasons.append("sesion favorece short")

        if bool(market_context.get("economic_holiday", False)):
            score -= 6
            reasons.append("feriado baja actividad")

        avg_hourly_return = float(historical_context.get("avg_hourly_return", 0.0))
        positive_hour_ratio = float(historical_context.get("positive_hour_ratio", 0.0))
        historical_volatility = float(historical_context.get("avg_hourly_range_pct", 0.0))
        projected_hourly_volume_ratio = float(
            historical_context.get("projected_hourly_volume_ratio", 0.0)
        )

        if avg_hourly_return > 0.0015:
            score += 8 * history_weight
            reasons.append("hora históricamente favorable")
        elif avg_hourly_return < -0.0015:
            score -= 8 * history_weight
            reasons.append("hora históricamente débil")

        if positive_hour_ratio >= 0.58:
            score += 8 * history_weight
            reasons.append("alta frecuencia histórica de cierres positivos")
        elif 0 < positive_hour_ratio <= 0.42:
            score -= 8 * history_weight
            reasons.append("baja frecuencia histórica de cierres positivos")

        if historical_volatility >= 0.01:
            score += 4 * history_weight
            reasons.append("franja con rango histórico útil")

        if projected_hourly_volume_ratio >= 3.0:
            score += 12
            reasons.append("god candle en volumen")

        if row["candle_body_pct"] > 0.012:
            score -= 15
            reasons.append("vela demasiado extendida")
        if row["distance_from_ema20_pct"] > 0.02:
            score -= 15
            reasons.append("precio muy lejos de EMA20")
        if row["close"] < row["ema_50"] * 0.992:
            score -= 10
            reasons.append("precio pierde soporte de EMA50")
        if row["rsi_14"] > 80 and projected_hourly_volume_ratio < 3.0:
            score -= 20
            reasons.append("sobrecompra")
        if row["momentum_5"] > 0.03 and projected_hourly_volume_ratio < 3.0:
            score -= 12
            reasons.append("subida demasiado rápida")
        if row["momentum_5"] < -0.002:
            score -= 15
            reasons.append("momentum en contra (caída libre)")

        score = max(0.0, min(100.0, score))
        if score >= 90:
            confidence = "muy_alta"
        elif score >= 75:
            confidence = "alta"
        elif score >= 60:
            confidence = "media"
        else:
            confidence = "baja"

        approved = score >= 75
        reason = ", ".join(reasons[:7]) if reasons else "sin confirmaciones suficientes"
        return SignalDecision(
            approved=approved,
            score=round(score, 2),
            confidence=confidence,
            reason=reason,
        )

    def evaluate_short(
        self,
        row,
        historical_context: dict[str, float] | None = None,
        history_weight: float = 1.0,
        market_context: dict[str, float | str | bool] | None = None,
    ) -> SignalDecision:
        score = 0.0
        reasons: list[str] = []
        historical_context = historical_context or {}
        market_context = market_context or {}
        history_weight = max(0.0, min(1.5, history_weight))

        if row["close"] < row["ema_20"]:
            score += 12
            reasons.append("precio bajo EMA20")
        if row["ema_20"] < row["ema_50"]:
            score += 12
            reasons.append("EMA20 bajo EMA50")
        if row["ema_50"] < row["ema_200"]:
            score += 12
            reasons.append("EMA50 bajo EMA200")
        if row["macd"] < row["macd_signal"]:
            score += 12
            reasons.append("MACD bajista")
        if row["macd_hist"] < 0:
            score += 8
            reasons.append("histograma MACD negativo")
        if 34 <= row["rsi_14"] <= 50:
            score += 12
            reasons.append("RSI debil sano")
        elif 28 <= row["rsi_14"] < 34:
            score += 4
            reasons.append("RSI bajo pero con momentum")
        elif row["rsi_14"] < 28:
            score -= 8
            reasons.append("RSI sobrevendido extremo")
        if row["volume_ratio"] >= 1.5:
            score += 12
            reasons.append("volumen vendedor fuerte")
        elif row["volume_ratio"] >= 1.0:
            score += 6
            reasons.append("volumen suficiente")
        if row["momentum_5"] <= -0.0025:
            score += 10
            reasons.append("momentum negativo")
        elif row["momentum_5"] <= 0.0005 and row["close"] <= row["ema_50"]:
            score += 4
            reasons.append("momentum debil bajo EMA50")
        if row["atr_pct"] >= 0.0015:
            score += 5
            reasons.append("volatilidad util")
        elif row["atr_pct"] >= 0.00025:
            score += 3
            reasons.append("volatilidad suficiente")
        if row["close"] < row["breakout_low_20"]:
            score += 10
            reasons.append("rompe minimo reciente")

        market_regime = str(market_context.get("market_regime", "desconocido"))
        if market_regime == "bajista":
            score += 14
            reasons.append("regimen mayor bajista")
        elif market_regime == "mixto":
            score += 5
            reasons.append("regimen mixto")
        elif market_regime == "tendencia_alcista":
            score -= 24
            reasons.append("regimen mayor alcista")

        trend_1h_bearish = bool(market_context.get("trend_1h_bearish", False))
        trend_15m_bearish = bool(market_context.get("trend_15m_bearish", False))
        macd_1h_bearish = bool(market_context.get("macd_1h_bearish", False))
        macd_15m_bearish = bool(market_context.get("macd_15m_bearish", False))

        if trend_1h_bearish:
            score += 10
            reasons.append("1h bajista")
        if trend_15m_bearish:
            score += 8
            reasons.append("15m bajista")
        if macd_1h_bearish:
            score += 5
            reasons.append("MACD 1h bajista")
        if macd_15m_bearish:
            score += 4
            reasons.append("MACD 15m bajista")
        if bool(market_context.get("below_15m_vwap", False)):
            score += 4
            reasons.append("bajo VWAP 15m")

        # --- Filtro de alineación multi-TF SHORT (Componente 1) ---
        if not trend_15m_bearish and not trend_1h_bearish:
            score -= 15
            reasons.append("sin alineación bajista timeframe mayor")
        elif not trend_1h_bearish:
            score -= 8
            reasons.append("1h no confirma baja")
        if trend_1h_bearish and trend_15m_bearish and macd_1h_bearish and macd_15m_bearish:
            score += 6
            reasons.append("stack completo bajista multi-TF")

        zone_probability = float(market_context.get("zone_short_probability", 0.0))
        zone_active = bool(market_context.get("zone_short_active", False))
        zone_grade = int(market_context.get("zone_short_grade", 0))
        liquidity_distance = float(market_context.get("next_short_liquidity_distance_pct", 0.0))
        liquidity_induced = bool(market_context.get("next_short_liquidity_induced", True))
        if zone_active and zone_probability >= 75:
            score += 16
            reasons.append(f"zona oferta {zone_probability:.0f}% ({zone_grade} fuegos)")
        elif zone_active and zone_probability >= 65:
            score += 10
            reasons.append(f"zona oferta {zone_probability:.0f}%")
        elif zone_active and 0 < zone_probability < 50:
            score -= 10
            reasons.append("zona oferta poco fiable")
        if bool(market_context.get("zone_short_low_probability_block", False)):
            score -= 18
            reasons.append("zona oferta 40/45%: observar")
        if liquidity_distance >= 0.006 and not liquidity_induced:
            score += 8
            reasons.append("liquidez inferior con recorrido")
        elif zone_active and liquidity_distance < 0.002:
            score -= 8
            reasons.append("poco recorrido a liquidez inferior")
        if liquidity_induced and zone_active:
            score -= 6
            reasons.append("liquidez inferior inducida")

        if bool(market_context.get("session_low_probability_block", False)):
            low_probability_value = float(market_context.get("session_low_probability_value", 50.0))
            score -= 35
            reasons.append(f"probabilidad baja {low_probability_value:.0f}%: no trade")
        elif bool(market_context.get("session_no_trade", False)):
            score -= 30
            reasons.append("sesion neutralizada sin trade")
        elif str(market_context.get("session_direction", "NEUTRAL")) == "SHORT":
            session_probability = float(market_context.get("session_continuation_probability", 50.0))
            micro_probability = float(market_context.get("micro_continuation_probability", 50.0))
            if session_probability >= 60 and micro_probability >= 55:
                score += 10
                reasons.append(f"continuacion sesion {session_probability:.0f}%")
        elif str(market_context.get("session_direction", "NEUTRAL")) == "LONG":
            score -= 10
            reasons.append("sesion favorece long")

        if bool(market_context.get("economic_holiday", False)):
            score -= 6
            reasons.append("feriado baja actividad")

        order_book_imbalance = float(market_context.get("order_book_imbalance", 0.0))
        if order_book_imbalance <= -0.12:
            score += 8
            reasons.append("presion vendedora en libro")
        elif order_book_imbalance >= 0.20:
            score -= 12
            reasons.append("muro comprador en libro")

        htf_rsi = float(market_context.get("rsi_1h", 50.0))
        rsi_15m = float(market_context.get("rsi_15m", 50.0))
        if 32 <= htf_rsi <= 55:
            score += 5
            reasons.append("RSI 1h permite short")
        elif htf_rsi < 22:
            score -= 14
            reasons.append("1h sobrevendido")

        # --- RSI Multi-Timeframe Guard SHORT (Componente 4) ---
        if rsi_15m < 25:
            score -= 18
            reasons.append("RSI 15m sobrevendido extremo")
        elif rsi_15m < 30 and row["rsi_14"] < 34:
            score -= 12
            reasons.append("sobreventa cruzada 5m+15m")
        if htf_rsi < 28 and rsi_15m < 32:
            score -= 14
            reasons.append("sobreventa multi-TF confirmada")

        # --- Penalizar entrada cuando el movimiento ya esta agotado ---
        if abs(float(row["distance_from_ema20_pct"])) > 0.008:
            score -= 10
            reasons.append("short ya estirado desde EMA20")

        avg_hourly_return = float(historical_context.get("avg_hourly_return", 0.0))
        positive_hour_ratio = float(historical_context.get("positive_hour_ratio", 0.0))
        projected_hourly_volume_ratio = float(
            historical_context.get("projected_hourly_volume_ratio", 0.0)
        )

        if avg_hourly_return < -0.0015:
            score += 8 * history_weight
            reasons.append("hora historicamente debil")
        elif avg_hourly_return > 0.0015:
            score -= 8 * history_weight
            reasons.append("hora historicamente alcista")
        if 0 < positive_hour_ratio <= 0.42:
            score += 8 * history_weight
            reasons.append("baja frecuencia de cierres positivos")
        elif positive_hour_ratio >= 0.58:
            score -= 8 * history_weight
            reasons.append("alta frecuencia de cierres positivos")

        if projected_hourly_volume_ratio >= 3.0 and row["close"] < row["open"]:
            score += 12
            reasons.append("volumen extremo bajista")

        if row["candle_body_pct"] > 0.012:
            score -= 10
            reasons.append("vela demasiado extendida")
        if row["distance_from_ema20_pct"] < -0.02:
            score -= 15
            reasons.append("precio muy lejos bajo EMA20")
        if row["rsi_14"] < 20 and projected_hourly_volume_ratio < 3.0:
            score -= 20
            reasons.append("sobreventa")
        if row["momentum_5"] < -0.03 and projected_hourly_volume_ratio < 3.0:
            score -= 12
            reasons.append("caida demasiado rapida")

        score = max(0.0, min(100.0, score))
        if score >= 90:
            confidence = "muy_alta"
        elif score >= 78:
            confidence = "alta"
        elif score >= 65:
            confidence = "media"
        else:
            confidence = "baja"

        approved = score >= 78
        reason = ", ".join(reasons[:7]) if reasons else "sin confirmaciones bajistas suficientes"
        return SignalDecision(
            approved=approved,
            score=round(score, 2),
            confidence=confidence,
            reason=reason,
        )
