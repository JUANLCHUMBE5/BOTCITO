from __future__ import annotations

import logging
import os
import time
from typing import Any

import MetaTrader5 as mt5

from bot.config import AccountConfig, AppConfig


class ExchangeClient:
    def __init__(self, app_config: AppConfig, account: AccountConfig) -> None:
        self.app_config = app_config
        self.account = account
        self.logger = logging.getLogger("ExchangeClient")

        # Intentar conectar con el terminal MT5 local
        if not mt5.initialize():
            error_code, error_desc = mt5.last_error()
            self.logger.error("Error al inicializar MetaTrader 5: %d - %s", error_code, error_desc)
            raise RuntimeError(f"No se pudo conectar al terminal de MetaTrader 5. Error: {error_desc}")

        # Intentar loguearse si las credenciales están definidas
        login_id = int(self.app_config.mt5.login)
        server = str(self.app_config.mt5.server)
        password = os.getenv(self.app_config.mt5.password_env_var, "")

        if login_id > 0 and password:
            self.logger.info("Intentando login en MT5: Cuenta=%d | Servidor=%s", login_id, server)
            if not mt5.login(login=login_id, password=password, server=server):
                error_code, error_desc = mt5.last_error()
                self.logger.error("Error al iniciar sesión en MT5: %d - %s", error_code, error_desc)
                raise RuntimeError(f"Error de login en Exness MT5: {error_desc}")
            self.logger.info("Login en Exness MT5 exitoso.")
        else:
            self.logger.info("No se proporcionaron credenciales completas de MT5. Conectado al terminal activo.")

    def load_markets(self) -> None:
        # En MT5 nos aseguramos de que todos los símbolos configurados estén visibles
        for symbol in self.app_config.symbols:
            if not mt5.symbol_select(symbol, True):
                self.logger.warning("No se pudo seleccionar/mostrar el símbolo %s en MT5", symbol)

    def configure_derivatives(self, symbol: str) -> None:
        # MT5 no requiere configurar margen o apalancamiento por símbolo mediante API.
        # El apalancamiento se hereda de la configuración de la cuenta de Exness.
        pass

    def _map_timeframe(self, timeframe: str) -> int:
        mapping = {
            "1m": mt5.TIMEFRAME_M1,
            "5m": mt5.TIMEFRAME_M5,
            "15m": mt5.TIMEFRAME_M15,
            "30m": mt5.TIMEFRAME_M30,
            "1h": mt5.TIMEFRAME_H1,
            "4h": mt5.TIMEFRAME_H4,
            "1d": mt5.TIMEFRAME_D1,
        }
        if timeframe not in mapping:
            raise ValueError(f"Timeframe '{timeframe}' no soportado en MT5.")
        return mapping[timeframe]

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 150) -> list[list[float]]:
        tf_mt5 = self._map_timeframe(timeframe)
        # Asegurarse de que el símbolo esté seleccionado
        mt5.symbol_select(symbol, True)
        
        # Copiar las tasas desde la vela previa
        rates = mt5.copy_rates_from_pos(symbol, tf_mt5, 1, limit)
        if rates is None or len(rates) == 0:
            error_code, error_desc = mt5.last_error()
            self.logger.warning("No se pudieron copiar rates para %s: %s (código %d)", symbol, error_desc, error_code)
            return []

        # Convertir a formato CCXT: [timestamp_ms, open, high, low, close, volume]
        ohlcv = []
        for r in rates:
            # r[0] es el tiempo en segundos (Epoch)
            ohlcv.append([
                float(r[0]) * 1000.0,  # timestamp en milisegundos
                float(r[1]),          # open
                float(r[2]),          # high
                float(r[3]),          # low
                float(r[4]),          # close
                float(r[5]),          # volume (tick volume)
            ])
        return ohlcv

    def get_last_price(self, symbol: str) -> float:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            info = mt5.symbol_info(symbol)
            if info is not None:
                return float(info.last if info.last > 0 else (info.bid + info.ask) / 2)
            raise RuntimeError(f"No se pudo obtener el último precio de {symbol}")
        return float(tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2)

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            info = mt5.symbol_info(symbol)
            if info is None:
                raise RuntimeError(f"Símbolo no encontrado: {symbol}")
            return {
                "bid": float(info.bid),
                "ask": float(info.ask),
                "last": float(info.last if info.last > 0 else (info.bid + info.ask) / 2),
            }
        return {
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "last": float(tick.last if tick.last > 0 else (tick.bid + tick.ask) / 2),
        }

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        # En Forex/CFD en MT5, no siempre está disponible el Level 2.
        # Intentamos obtener la profundidad del mercado de MT5.
        mt5.market_book_add(symbol)
        book = mt5.market_book_get(symbol)
        mt5.market_book_release(symbol)

        bids = []
        asks = []
        if book is not None:
            for item in book:
                # item.type es mt5.BOOK_TYPE_BUY o mt5.BOOK_TYPE_SELL
                price = float(item.price)
                volume = float(item.volume)
                if item.type == mt5.BOOK_TYPE_BUY:
                    bids.append([price, volume])
                elif item.type == mt5.BOOK_TYPE_SELL:
                    asks.append([price, volume])

            # Ordenar
            bids.sort(key=lambda x: x[0], reverse=True)
            asks.sort(key=lambda x: x[0])
        else:
            # Fallback en caso de que el bróker no provea Depth of Market
            tick = self.fetch_ticker(symbol)
            bids = [[tick["bid"], 10.0]]
            asks = [[tick["ask"], 10.0]]

        return {
            "bids": bids[:limit],
            "asks": asks[:limit],
            "timestamp": int(time.time() * 1000),
        }

    def fetch_balance(self) -> dict[str, Any]:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("No se pudo obtener la información de la cuenta en MT5")
        
        balance_usd = float(info.balance)
        free_usd = float(info.margin_free)
        used_usd = float(info.margin)

        return {
            "USD": {"free": free_usd, "used": used_usd, "total": balance_usd},
            "USDT": {"free": free_usd, "used": used_usd, "total": balance_usd},
            "free": {"USD": free_usd, "USDT": free_usd},
            "used": {"USD": used_usd, "USDT": used_usd},
            "total": {"USD": balance_usd, "USDT": balance_usd},
        }

    def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        # Obtiene las posiciones abiertas de MT5
        raw_positions = mt5.positions_get()
        if raw_positions is None:
            return []

        formatted_positions = []
        for p in raw_positions:
            # Filtrar por símbolos si se especifican
            if symbols and p.symbol not in symbols:
                continue

            # Mapear al formato que espera trader.py
            side = "LONG" if p.type == mt5.POSITION_TYPE_BUY else "SHORT"
            formatted_positions.append({
                "symbol": p.symbol,
                "id": str(p.ticket),
                "side": side,
                "contracts": float(p.volume),
                "entryPrice": float(p.price_open),
                "unrealizedPnl": float(p.profit),
                "initialMargin": float(p.margin),
                "stopLoss": float(p.sl),
                "takeProfit": float(p.tp),
            })
        return formatted_positions

    def _execute_order(
        self,
        symbol: str,
        volume: float,
        order_type: int,
        params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        params = params or {}
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No se pudo obtener el tick para {symbol}")

        price = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Si es una orden de cierre (reduceOnly), debemos cerrarla por Ticket (posición de MT5)
        is_close = bool(params.get("reduceOnly"))

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": float(price),
            "deviation": 20,
            "magic": 234567,
            "comment": str(params.get("reason", "bot_trade")),
            "type_time": mt5.ORDER_TIME_GTC,
        }

        if is_close:
            # Encontrar la posición de MT5 correspondiente para cerrarla por ticket
            open_positions = mt5.positions_get(symbol=symbol)
            ticket_to_close = None
            if open_positions:
                # Buscar la posición opuesta para cerrar
                expected_pos_type = mt5.POSITION_TYPE_SELL if order_type == mt5.ORDER_TYPE_BUY else mt5.POSITION_TYPE_BUY
                for p in open_positions:
                    if p.type == expected_pos_type:
                        ticket_to_close = p.ticket
                        request["volume"] = min(float(volume), float(p.volume))
                        break
            if ticket_to_close is not None:
                request["position"] = ticket_to_close
            else:
                self.logger.warning("Intento de cierre de posición (reduceOnly) pero no se encontró ninguna posición abierta opuesta para %s", symbol)
                return {"id": "0", "price": price, "volume": 0.0, "fee": 0.0}
        else:
            # Si no es de cierre, incluimos el SL y TP de entrada
            request["sl"] = float(params.get("sl", 0.0))
            request["tp"] = float(params.get("tp", 0.0))

        # Intentar ejecutar con diferentes tipos de llenado (Filling type)
        # Exness a veces requiere FOK (Fill or Kill) o IOC (Immediate or Cancel)
        filling_types = [
            mt5.ORDER_FILLING_FOK,
            mt5.ORDER_FILLING_IOC,
            mt5.ORDER_FILLING_RETURN
        ]

        result = None
        for fill in filling_types:
            request["type_filling"] = fill
            result = mt5.order_send(request)
            if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
                break

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            retcode = result.retcode if result is not None else -1
            comment = result.comment if result is not None else "Unknown error"
            self.logger.error("Error al enviar orden a MT5: Código %d - %s", retcode, comment)
            raise RuntimeError(f"Error al enviar orden a MT5 (retcode={retcode}): {comment}")

        self.logger.info("Orden MT5 completada con éxito. Ticket: %d", result.order)
        return {
            "id": str(result.order),
            "price": float(result.price),
            "volume": float(result.volume),
            "fee": float(result.fee) if hasattr(result, "fee") else 0.0,
        }

    def create_market_buy(
        self,
        symbol: str,
        amount: float,
        params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._execute_order(symbol, amount, mt5.ORDER_TYPE_BUY, params)

    def create_market_sell(
        self,
        symbol: str,
        amount: float,
        params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return self._execute_order(symbol, amount, mt5.ORDER_TYPE_SELL, params)

    def normalize_amount(self, symbol: str, amount: float) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            return round(amount, 2)
        step = info.volume_step
        if step <= 0.0:
            step = 0.01
        
        # Redondear al paso de volumen más cercano
        precision = 0
        if step < 1.0:
            precision = len(str(step).split(".")[1])
        return round(round(amount / step) * step, precision)

    def get_contract_size(self, symbol: str) -> float:
        info = mt5.symbol_info(symbol)
        if info is None:
            return 1.0
        return float(info.trade_contract_size)

    def close(self) -> None:
        mt5.shutdown()
