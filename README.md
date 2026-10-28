# 📈 Bot de Inversiones y Rebalanceo Pasivo (Telegram)

Bot de Telegram desarrollado en **Python 3.11** y contenedorizado con **Docker & Docker Compose**, diseñado como asesor financiero automatizado y calculador de rebalanceo mensual de portafolio para inversionistas en México.

---

## 🎯 Estrategia Financiera

- **Asignación Pasiva 70/30:**
  - **70% Renta Variable:** ETF `IVVPESO.MX` (S&P 500 con cobertura cambiaria a pesos, cotizado en la Bolsa Mexicana de Valores vía SIC).
  - **30% Renta Fija:** CETES / Bonddia (a través de Cetesdirecto).
- **Filosofía de Rebalanceo:**
  - **Cash-Flow Rebalancing:** No vende posiciones ni genera eventos fiscales por venta de activos.
  - El rebalanceo se realiza **exclusivamente mediante la inyección mensual de nuevo capital**, asignando los fondos al activo subponderado para converger al objetivo 70/30.
  - **Discretización Entera:** Como no se pueden comprar fracciones de títulos de `IVVPESO.MX` en casas de bolsa mexicanas (GBM, Bursanet, Vector), el bot calcula los títulos enteros exactos y **reubica automáticamente todo el remanente en centavos y pesos a CETES**.

---

## 📁 Estructura del Proyecto

```text
finanzas-personales/
├── .github/
│   ├── dependabot.yml             # Mantenimiento automático semanal de librerías
│   └── workflows/
│       ├── docker-publish.yml     # Pipeline CI/CD: Quality Gate + Publicación en GHCR
│       └── deploy-ssh.yml.example # Plantilla alternativa para despliegue por SSH
├── data/                          # Volumen persistente (montado en Docker)
│   ├── portafolio.json            # Base de datos atómica con títulos, saldos y configs
│   └── historial.json             # Bitácora histórica auditable de aportaciones
├── .env.example                   # Plantilla de credenciales y variables de entorno
├── .gitignore                     # Excluye .env, data/, caché de Python y herramientas
├── bot.py                         # Código fuente principal, rebalanceo, scheduler y comandos
├── Dockerfile                     # Imagen ligera basada en python:3.11-slim
├── docker-compose.yml             # Orquestación del bot + servicio Watchtower para auto-update
├── README.md                      # Documentación del proyecto
└── requirements.txt               # Dependencias exactas (pyTelegramBotAPI, yfinance)
```

---

## 💬 Comandos Disponibles en Telegram

### Operaciones y Análisis Financiero
| Comando | Descripción | Ejemplo de Uso |
| :--- | :--- | :--- |
| `/start` o `/ayuda` | Despliega el menú de comandos y explicación de la estrategia. | `/ayuda` |
| `/portafolio` | Muestra títulos actuales, cotización en tiempo real vía Yahoo Finance, saldo en CETES y balance real vs meta. | `/portafolio` |
| `/rendimiento` | Genera una infografía gráfica con desglose de activos y cálculo de plusvalía/retorno. | `/rendimiento` |
| `/aportar <monto>` | Calcula la compra óptima de títulos enteros y el envío a CETES para acercarte al 70/30 sin vender nada. | `/aportar 7500` |
| `/confirmar_compra <titulos> <cetes>` | Suma los títulos comprados y el dinero a `portafolio.json` y los registra en `historial.json`. | `/confirmar_compra 48 2450.50` |
| `/actualizar <titulos> <saldo>` | Sobrescribe manualmente los valores del portafolio en caso de discrepancias con estados de cuenta. | `/actualizar 150 14200.00` |

### Automatización, Auditoría y Respaldos
| Comando | Descripción | Ejemplo de Uso |
| :--- | :--- | :--- |
| `/historial` | Muestra las últimas 5 aportaciones realizadas y la suma total de capital inyectado a lo largo del tiempo. | `/historial` |
| `/recordatorio [dia]` | Consulta o cambia el día del mes en que recibirás la alerta de inversión y tu respaldo mensual automático. | `/recordatorio 20` |
| `/backup` | Envía los archivos `portafolio.json` e `historial.json` directamente al chat de Telegram como documentos adjuntos. | `/backup` |

---

## ⏰ Recordatorio, Infografía y Respaldo Automático Mensual

El bot cuenta con un **hilo planificador en segundo plano (Scheduler)**:
1. Por defecto viene programado para el día **15** de cada mes (puedes cambiarlo en cualquier momento con `/recordatorio <dia>`).
2. En la fecha elegida, el bot:
   - Te envía una notificación a Telegram recordando tu aportación mensual.
   - **Genera y adjunta automáticamente una infografía gráfica** con el estado de tu dinero, porcentajes y plusvalía generada.
   - Adjunta automáticamente una copia de seguridad fresca de tus archivos de datos (`portafolio.json` e `historial.json`).
3. Registra el mes en curso para garantizar que **solo se ejecute una vez por mes**.

---

## 🔄 Despliegue Continuo Automático (GitOps con Watchtower)

Cada vez que haces un `git push` a la rama `main`:
1. **GitHub Actions** valida la sintaxis y tipos del código mediante un *Quality Gate*.
2. Compila la imagen Docker en la nube de GitHub y la publica en GitHub Container Registry (`ghcr.io/luisrodzal/finanzas-personales:latest`).
3. **Watchtower** (corriendo en tu servidor) detecta la nueva versión en un lapso de 60 segundos, reinicia el contenedor de forma limpia y el bot te manda un mensaje a Telegram avisando que ya está en línea y actualizado.

---

## 🚀 Despliegue con Docker Compose

1. **Configurar credenciales:**
   ```bash
   cp .env.example .env
   ```
   Rellena tu `TELEGRAM_TOKEN` y `MI_CHAT_ID`.

2. **Levantar los servicios:**
   ```bash
   docker compose up -d
   ```

3. **Verificar logs:**
   ```bash
   docker compose logs -f
   ```
