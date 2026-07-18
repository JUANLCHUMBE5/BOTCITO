# 🤖 Algorithmic Trading Bot & Decision Engine (XRP/USDT)

Este es un proyecto de **Trading Algorítmico y Cuantitativo** desarrollado en Python para la ejecución autónoma de estrategias de scalping en mercados de criptomonedas y CFDs. Diseñado para funcionar 24/7 en la nube (AWS EC2) bajo un enfoque profesional de concurrencia, gestión estricta del riesgo y control remoto en tiempo real.

---

### 🚀 Aspectos Clave del Proyecto (CV & Portfolio)

*   **Arquitectura Concurrente Multihilo:** Operación paralela y aislada de múltiples cuentas usando `threading`. Si una cuenta experimenta fallas de API o red, las demás continúan operando sin interrupción.
*   **Filtro de Decisión Cuantitativo (IA Local):** Módulo predictivo local (`bot/ai_filter.py`) que evalúa el histórico y múltiples indicadores técnicos para generar un Score de Confianza (0-100%) antes de autorizar entradas al mercado.
*   **Gestión Estricta del Riesgo:** Implementación de Trailing Stop Loss, Take Profit escalonado (laddering), Stop Loss dinámico adaptado a la volatilidad real (ATR) y *Circuit Breakers* automáticos por pérdida máxima diaria.
*   **Filtro de Sentimiento de Noticias:** Integración en tiempo real con NewsAPI para bloquear transacciones automáticamente ante noticias con polaridad negativa en el ecosistema.
*   **Control y Alertas vía Telegram:** Panel bidireccional interactivo. Envía reportes dinámicos de PnL y estado del bot al móvil, y permite control manual completo (pausar, reanudar, forzar cierres, cambiar perfiles de riesgo) con comandos como `/status` o `/settings`.
*   **Despliegue y Resiliencia (Ops):** Configuración como demonio de Linux (`systemd`), servicio watchdog autónomo de auto-recuperación ante caídas de proceso, y scripts de empaquetado portable (`package_release.ps1`) para fácil migración entre servidores EC2.

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
