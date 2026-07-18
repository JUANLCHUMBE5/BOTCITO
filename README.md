# 🤖 Algorithmic Trading Bot & Decision Engine

Un sistema automatizado de trading cuantitativo para la ejecución de estrategias de scalping en mercados financieros, diseñado para operar 24/7 en la nube (AWS EC2).

## 🛠️ Tecnologías Clave

*   **Lenguaje:** Python
*   **Conectividad:** CCXT (integración multiexchange)
*   **Análisis Técnico:** Pandas, NumPy
*   **Persistencia:** SQLite
*   **Alertas & Control:** Telegram Bot API
*   **Operaciones (Ops):** AWS EC2, Linux Systemd, Watchdog daemon

## 🚀 Características Clave

*   **Arquitectura Concurrente Multihilo:** Operación paralela y aislada de múltiples cuentas usando `threading`, previniendo caídas globales si una cuenta falla.
*   **Filtro de Decisión (IA Local):** Algoritmo predictivo local que evalúa el histórico y múltiples indicadores técnicos para generar un Score de Confianza (0-100%) antes de autorizar entradas.
*   **Gestión Estricta del Riesgo:** Implementación de Trailing Stop, Take Profit escalonado, Stop Loss dinámico adaptado a la volatilidad real (ATR) y *Circuit Breakers* automáticos por pérdida máxima diaria.
*   **Filtro de Sentimiento de Noticias:** Integración en tiempo real con NewsAPI para bloquear transacciones automáticamente ante noticias con polaridad negativa en el ecosistema.
*   **Control y Alertas vía Telegram:** Panel bidireccional interactivo. Envía reportes dinámicos de PnL y permite control manual completo (pausar, reanudar, forzar cierres, cambiar perfiles de riesgo) con comandos como `/status` o `/settings`.
*   **Despliegue y Resiliencia (Ops):** Configuración como demonio de Linux (`systemd`), servicio watchdog autónomo de auto-recuperación y scripts de empaquetado portable para fácil migración entre servidores.

---

*Para ver la guía de instalación detallada, configuraciones y el manual de comandos, consulta la [Guía de Instalación y Despliegue (INSTRUCTIONS.md)](./INSTRUCTIONS.md).*
