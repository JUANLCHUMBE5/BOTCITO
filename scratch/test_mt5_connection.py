import MetaTrader5 as mt5
import sys

def test_connection():
    print("=== INICIALIZANDO METATRADER 5 ===")
    
    # 1. Intentar inicializar la conexión con el terminal abierto
    if not mt5.initialize():
        print(f"Error al inicializar MetaTrader 5: {mt5.last_error()}")
        print("\n[INFO] Asegúrate de tener la aplicación de escritorio de Exness MT5 abierta en tu computadora.")
        sys.exit(1)
        
    print("Conexión inicializada con éxito con el terminal MT5 local.")
    
    # 2. Obtener información del terminal
    terminal_info = mt5.terminal_info()
    if terminal_info is not None:
        print(f"Terminal: {terminal_info.name} | Compañía: {terminal_info.company}")
        print(f"Ruta del terminal: {terminal_info.path}")
    
    # 3. Obtener información de la cuenta actual conectada
    account_info = mt5.account_info()
    if account_info is not None:
        print("\n=== INFORMACIÓN DE LA CUENTA ===")
        print(f"ID de Cuenta: {account_info.login}")
        print(f"Servidor: {account_info.server}")
        print(f"Bróker: {account_info.company}")
        print(f"Nombre: {account_info.name}")
        print(f"Balance: {account_info.balance} USD")
        print(f"Margen Libre: {account_info.margin_free} USD")
    else:
        print("\n[ALERTA] No se pudo obtener la información de la cuenta. Asegúrate de estar logueado en Exness MT5.")
        
    # 4. Intentar consultar precios de algunos activos
    print("\n=== CONSULTANDO TICKERS EN VIVO ===")
    symbols_to_test = ["XAUUSD", "EURUSD", "BTCUSD", "ETHUSD", "XRPUSD"]
    
    for symbol in symbols_to_test:
        # Asegurarse de que el símbolo esté visible en el Market Watch (Observación de Mercado)
        mt5.symbol_select(symbol, True)
        
        info = mt5.symbol_info(symbol)
        if info is not None:
            tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                print(f"Símbolo: {symbol:<7} | Bid: {tick.bid:<9} | Ask: {tick.ask:<9} | Spread: {round((tick.ask - tick.bid), 5)}")
            else:
                print(f"Símbolo: {symbol:<7} | Visible pero sin ticks activos.")
        else:
            # A veces en Exness los símbolos de cripto tienen un sufijo (ej: XRPUSDm para cuentas Standard)
            # Probamos con el sufijo 'm'
            symbol_m = symbol + "m"
            mt5.symbol_select(symbol_m, True)
            info_m = mt5.symbol_info(symbol_m)
            if info_m is not None:
                tick_m = mt5.symbol_info_tick(symbol_m)
                if tick_m is not None:
                    print(f"Símbolo: {symbol_m:<7} | Bid: {tick_m.bid:<9} | Ask: {tick_m.ask:<9} | Spread: {round((tick_m.ask - tick_m.bid), 5)}")
                else:
                    print(f"Símbolo: {symbol_m:<7} | Visible pero sin ticks activos.")
            else:
                print(f"Símbolo: {symbol:<7} | No encontrado en el bróker (verifica si tu cuenta usa sufijos como 'm').")
                
    # 5. Cerrar la conexión limpia
    mt5.shutdown()
    print("\nConexión con MetaTrader 5 cerrada correctamente.")

if __name__ == "__main__":
    test_connection()
