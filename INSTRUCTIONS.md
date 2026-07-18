# Guía Detallada de Instalación, Configuración y Despliegue

Este documento contiene toda la información técnica, instrucciones de configuración y guías para el despliegue del bot de trading XRP en servidores de producción.

---

## Estructura del Código

- `main.py`: Punto de entrada principal y bucle de comandos de Telegram.
- `bot/config.py`: Carga y validación de la configuración del sistema.
- `bot/exchange_client.py`: Wrapper para la API del exchange usando `ccxt`.
- `bot/indicators.py`: Cálculo de indicadores técnicos (EMA, RSI, MACD, ATR).
- `bot/strategy.py`: Reglas de entrada, salida y lógica de trailing.
- `bot/trader.py`: Loop de ejecución concurrente y control de posiciones por cuenta.
- `bot/storage.py`: Base de datos SQLite para persistencia.
- `bot/notifier.py`: Integración con Telegram para alertas y control remoto.
- `bot/ai_filter.py`: Motor local de scoring y decisión de operaciones.
- `bot/news_service.py`: Filtro de sentimiento de noticias vía NewsAPI.
- `watchdog.py`: Monitoriza el estado del bot y gestiona reinicios autónomos.
- `backfill_xrp_history.py`: Descarga y almacena velas históricas reales.

---

## Paso 1: Preparación de Archivos

1. Copia `config/settings.example.json` a `config/settings.json`
2. Copia `.env.example` a `.env`
3. Rellena las credenciales de API del exchange en `.env`
4. Rellena `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` si quieres control remoto.
5. Rellena `NEWS_API_KEY` para activar el filtro de noticias.

## Paso 2: Instalación de Dependencias

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Paso 3: Exportar Variables de Entorno

En Linux:
```bash
set -a
source .env
set +a
```

## Paso 4: Ejecución

```bash
python main.py
```

---

## Modos de Operación

### 1. Modo `paper` (Simulación)
- Utiliza precios reales de mercado del exchange pero simula los balances y ejecuciones en memoria y SQLite.
- Útil para testear estrategias y optimizar parámetros durante periodos de prueba (ej. 1 mes) sin arriesgar capital real.

### 2. Modo `real`
- Envía órdenes reales al exchange.
- Requiere credenciales de API con permisos de trading habilitados (desactivando retiros por seguridad).

---

## Notificaciones y Comandos de Telegram

1. Crea un bot de Telegram con `@BotFather` y obtén el Token.
2. Escribe un mensaje al bot y obtén tu `chat_id` personal.
3. Habilita las notificaciones en `config/settings.json`:

```json
"telegram": {
  "enabled": true,
  "notify_startup": true,
  "notify_errors": true,
  "notify_buys": true,
  "notify_sells": true,
  "allow_commands": true
}
```

El bot enviará reportes automáticos y responderá a los siguientes comandos:
- `/status` — Estado actual de las cuentas y posiciones abiertas.
- `/pnl` — Resumen de operaciones y PnL del día actual.
- `/stop` — Detiene temporalmente la apertura de nuevas posiciones.
- `/resume` — Reanuda el trading normal.
- `/close` — Solicita el cierre manual de la posición activa en la siguiente iteración.
- `/settings` — Consulta o ajusta el perfil de riesgo (`conservador`/`agresivo`) y parámetros de capital.

---

## Histórico de Mercado e IA Local

### Llenado de Histórico
Para dotar al motor cuantitativo de datos reales para evaluar tendencias a largo plazo:
```bash
python3 backfill_xrp_history.py
```
Esto creará velas de `15m`, `1h` y `1d` en la base de datos local SQLite.

### Filtro IA Local
El bot evalúa si el mercado está sobreextendido o si la tendencia global apoya la entrada analizando las velas históricas. Devuelve un score de 0 a 100:
- **Score Alto (Ej. > 80):** Entrada aprobada con confianza alta.
- **Score Medio/Bajo:** Operación bloqueada para reducir falsos positivos.

---

## Despliegue en Servidores (AWS EC2 / Ubuntu)

### Configurar como Servicio Systemd

Crea `/etc/systemd/system/xrp-bot.service`:
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

Habilitar e iniciar:
```bash
sudo systemctl daemon-reload
sudo systemctl enable xrp-bot
sudo systemctl start xrp-bot
sudo systemctl status xrp-bot
```

### Empaquetado y Migración de Servidores

Para generar un zip listo para transferir con toda la configuración y bases de datos actuales desde Windows:
```powershell
.\deploy\package_release.ps1 -IncludeState
```

Para instalar el bundle comprimido en el nuevo servidor Linux:
```bash
chmod +x deploy/install_bundle.sh
./deploy/install_bundle.sh
```
