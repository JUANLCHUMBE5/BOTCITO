# Bot de trading XRP con Python, AWS y Kraken

## 🛠️ Resumen del Trabajo Realizado en este Proyecto

En este proyecto se ha desarrollado un robusto sistema automatizado de trading cuantitativo para **XRP** (escalable a otros instrumentos como CFD de Forex/Oro) diseñado para ejecutarse 24/7 en servidores en la nube (AWS EC2). A continuación se detallan las áreas principales desarrolladas y optimizadas:

1. **Arquitectura Multicuenta y Multihilo Asíncrona (`bot/trader.py` y `main.py`)**:
   - Soporte para operar múltiples cuentas de manera independiente y en paralelo usando hilos dedicados.
   - Aislamiento de fallos: si una cuenta experimenta problemas de red o de API, el resto de las cuentas continúa operando sin afectarse.
   - Sincronización thread-safe del estado de las posiciones, snapshots de saldo y control global.

2. **Estrategia Cuantitativa de Scalping (`bot/strategy.py` e `indicators.py`)**:
   - Operaciones dinámicas en timeframes de 5m con un intervalo de monitoreo en tiempo real de 10s.
   - Cálculo e integración de múltiples indicadores técnicos: EMAs (20, 50, 200), RSI, MACD, Average True Range (ATR) y Breakouts.
   - Detección de anomalías en el volumen mediante factores RVOL y picos dinámicos ("god candles").

3. **Motor de Filtro de Decisión con IA Local (`bot/ai_filter.py`)**:
   - Sistema de puntuación (scoring) local de 0 a 100 y niveles de confianza ("alta", "media", "baja") para evaluar la probabilidad de éxito de cada señal.
   - Permite filtrar y denegar operaciones dudosas basadas en el comportamiento histórico sin la necesidad de consultar APIs externas de pago.

4. **Gestión Profesional del Riesgo**:
   - **Take Profit Escalonado (Laddering)**: Cierres parciales programados por tramos de beneficio.
   - **Trailing Stop Loss Activo**: Ajuste dinámico de protección de ganancias a medida que el precio se mueve a favor.
   - **Stop Loss basado en ATR**: Adaptabilidad a la volatilidad del mercado en cada ciclo.
   - **Circuit Breaker Diario**: Bloqueo automático del bot si se alcanza un porcentaje de pérdida diaria o un límite nominal especificado.
   - **Cooldowns Inteligentes**: Pausas obligatorias de trading tras ganancias o pérdidas para evitar sesgos emocionales del bot (overtrading).

5. **Panel de Notificaciones y Control Remoto vía Telegram (`bot/notifier.py`)**:
   - Notificaciones push en tiempo real de arranques, compras, ventas, errores y circuit breakers.
   - Reportes periódicos programables con balances consolidados en USDT y la divisa local (calculados dinámicamente).
   - Comandos interactivos remotos desde Telegram (`/status`, `/pnl`, `/stop`, `/resume`, `/close`, `/settings`) para control manual completo y configuración de perfiles (agresivo/conservador) al vuelo.

6. **Filtro de Sentimiento de Noticias (`bot/news_service.py`)**:
   - Integración con NewsAPI para monitorear titulares del mercado de criptomonedas y pausar automáticamente el trading ante eventos periodísticos con alta polaridad negativa.

7. **Base de Datos y Herramientas de Datos (`bot/storage.py` y `backfill_xrp_history.py`)**:
   - SQLite como motor de persistencia local rápido y liviano para auditar precios, ejecuciones, balances e histórico de mercado.
   - Script de backfilling para descargar y almacenar velas históricas directamente en la nube.

8. **Deployment Automático y Portabilidad (`deploy/`)**:
   - Integración como servicio de Linux (`systemd`) y script de watchdog para levantar el proceso de manera automática si se cae.
   - Script PowerShell (`package_release.ps1`) para empaquetar de forma automática versiones portables listas para migrar a cualquier otro servidor Ubuntu manteniendo el estado de la base de datos y configuración (.env).

---

Este proyecto es una base funcional para correr un bot de **XRP/USDT** con dos cuentas al mismo tiempo, usando:

- `ccxt` para conectarse al exchange
- `pandas` y `numpy` para indicadores
- `sqlite3` para guardar precios y operaciones
- hilos separados para aislar cada cuenta

## Qué hace el bot

- Lee mercado del exchange cada pocos segundos
- Calcula **EMA 20**, **EMA 50**, **RSI**, **MACD**, volumen medio y momentum
- Calcula además **EMA 200**, **ATR**, breakout de 20 velas y distancia frente a EMA 20
- Compra cuando detecta una subida fuerte con confirmación y evita entrar tarde en velas sobreextendidas
- Vende por:
  - `take profit`
  - `stop loss`
  - pérdida de tendencia tras ganancia
  - ruptura de soporte / reversión de señal
- Soporta `paper trading` y `real trading`
- Guarda datos en SQLite
- Si una cuenta falla, la otra sigue trabajando

## Estructura

- `main.py`: punto de entrada
- `bot/config.py`: carga configuración
- `bot/exchange_client.py`: exchange vía `ccxt`
- `bot/indicators.py`: indicadores técnicos
- `bot/strategy.py`: reglas de compra/venta
- `bot/trader.py`: worker por cuenta
- `bot/storage.py`: base SQLite
- `config/settings.example.json`: ejemplo de configuración
- `.env.example`: ejemplo para claves
- `deploy/setup_ubuntu.sh`: instalación inicial en Ubuntu
- `deploy/xrp-bot.service`: servicio para `systemd`
- `bot/notifier.py`: alertas a Telegram
- `bot/ai_filter.py`: motor de decisión local tipo IA básica
- `backfill_xrp_history.py`: añade histórico real de XRP a SQLite

## Paso 1: preparar archivos

1. Copia `config/settings.example.json` a `config/settings.json`
2. Copia `.env.example` a `.env`
3. Completa tus API keys del exchange
4. Si quieres alertas, completa `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`
5. Si luego quieres noticias, agrega `NEWS_API_KEY`

## Paso 2: instalar dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Paso 3: exportar variables

En Linux:

```bash
set -a
source .env
set +a
```

## Paso 4: ejecutar

```bash
python main.py
```

## Modos de uso

### `paper`

- No envía órdenes reales
- Usa precio real de Binance
- Simula balance en USDT y XRP por cuenta
- Recomendado durante 1 mes

### `real`

- Envía compras y ventas de mercado reales
- Requiere API key y secret activas
- Usa el mismo motor de estrategia

## Configuración importante

En `config/settings.json` puedes cambiar:

- `symbol`: por defecto `XRP/USDT`
- `timeframe`: por ejemplo `1m`, `5m`
- `poll_interval_seconds`: frecuencia de análisis
- `trade_amount_usdt`: monto fijo por operación
- `take_profit_pct`: ganancia objetivo
- `stop_loss_pct`: pérdida máxima
- `volume_spike_factor`: confirmación mínima de volumen
- `max_candle_pct`: evita comprar velas demasiado explosivas
- `max_distance_from_ema20_pct`: evita entrar demasiado lejos del promedio
- `require_breakout`: compra solo si rompe máximo reciente
- `telegram.enabled`: activa o apaga notificaciones

## Notificaciones por Telegram

1. En Telegram abre `@BotFather`
2. Crea un bot con `/newbot`
3. Copia el token y guárdalo en `.env` como `TELEGRAM_BOT_TOKEN`
4. Escribe un mensaje a tu bot
5. Obtén tu `chat_id` y guárdalo como `TELEGRAM_CHAT_ID`
6. En `config/settings.json` cambia:

```json
"telegram": {
  "enabled": true,
  "notify_startup": true,
  "notify_errors": true,
  "notify_buys": true,
  "notify_sells": true
}
```

El bot te avisará cuando:

- arranque
- ocurra un error en una cuenta
- haga una compra
- haga una venta
- bloquee compras por noticias negativas
- envíe un resumen periódico del estado del bot

Puedes ajustar estas alertas en `telegram`:

- `notify_news_blocks`
- `notify_status_summary`
- `status_interval_minutes`

## IA básica local

La V3 usa un motor de decisión local, sin API externa:

- evalúa tendencia con EMA 20/50/200
- evalúa volumen relativo
- evalúa momentum y MACD
- evita velas sobreextendidas
- evita entradas demasiado lejos de EMA 20
- exige breakout y ATR mínimo

El resultado es un `score` de 0 a 100 con confianza:

- `alta`: compra permitida
- `media`: observar
- `baja`: no entrar

Esto corre dentro de la EC2 y no añade costo extra.

## Añadir histórico real de XRP

Si quieres añadir histórico real de XRP directamente en la nube al SQLite del bot:

```bash
cd /home/ubuntu/xrp-trading-bot
source .venv/bin/activate
pip install -r requirements.txt
python3 backfill_xrp_history.py
```

Eso guarda en la tabla `xrp_market_history`:

- `1d` con periodo `max`
- `1h` con unos 730 días
- `15m` con unos 60 días

Así no inventamos datos: añadimos histórico real al proyecto en la EC2.

## Datos del modo `paper`

Sí, el modo `paper` ya deja datos útiles para pasar luego a real:

- `prices`: precio e indicadores por ciclo
- `trades`: compras, ventas y PnL simulado
- `positions`: posición abierta actual
- `news_events`: titulares y score de noticias
- `account_snapshots`: balance paper, XRP paper y equity estimada

Eso no garantiza que en real funcione “perfecto”, pero sí te deja una base mucho mejor para medir, ajustar y optimizar antes de arriesgar dinero real.

## Base de datos

La base SQLite guarda:

- tabla `prices`: precios e indicadores
- tabla `trades`: compras, ventas y PnL
- tabla `positions`: posición abierta por cuenta

## Recomendaciones de seguridad

- Crea API keys de Spot, nunca de retiros
- Desactiva `Enable Withdrawals`
- Restringe IP cuando ya tengas la EC2 fija
- Guarda `.env` con permisos `chmod 600 .env`
- Empieza en `paper` y con montos pequeños

## systemd para 24/7 en EC2

Copia `deploy/xrp-bot.service` a `/etc/systemd/system/xrp-bot.service`:

```ini
[Unit]
Description=XRP Trading Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/xrp-trading-bot
EnvironmentFile=/home/ubuntu/xrp-trading-bot/.env
ExecStart=/home/ubuntu/xrp-trading-bot/.venv/bin/python /home/ubuntu/xrp-trading-bot/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Luego:

```bash
sudo systemctl daemon-reload
sudo systemctl enable xrp-bot
sudo systemctl start xrp-bot
sudo systemctl status xrp-bot
journalctl -u xrp-bot -f
```

## Migrarlo a otro servidor

No hace falta convertirlo en `.exe`. Para Linux es mejor moverlo como bundle desplegable.

### Empaquetar en Windows

Desde PowerShell:

```powershell
cd C:\Users\leonc\OneDrive\Documentos\Playground\xrp-trading-bot
.\deploy\package_release.ps1
```

Eso crea un `.zip` dentro de `dist\`.

Si quieres copiar tambiÃ©n el estado actual del bot, incluyendo `.env`, base SQLite y logs:

```powershell
.\deploy\package_release.ps1 -IncludeState
```

### Instalar en otro Ubuntu

1. Sube el `.zip` al nuevo servidor
2. DescomprÃ­melo
3. Entra a la carpeta `payload`
4. Ejecuta:

```bash
chmod +x deploy/install_bundle.sh
./deploy/install_bundle.sh
```

Luego:

```bash
sudo systemctl restart xrp-bot xrp-watchdog
sudo systemctl status xrp-bot --no-pager
journalctl -u xrp-bot -n 100 --no-pager
```

Si copiaste un bundle con `-IncludeState`, el bot puede migrar con su base de datos y configuraciÃ³n ya existentes.

## Notas importantes

- Para simulación de 1 mes, es más estable usar `paper` local que depender de testnet.
- Antes de pasar a `real`, revisa logs, PnL y cantidad de señales falsas.
- Este bot es educativo y debes validarlo bien antes de arriesgar dinero real.
