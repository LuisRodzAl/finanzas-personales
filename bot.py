"""
bot.py - Asesor Financiero y Calculador de Rebalanceo en Telegram.

Estrategia Pasiva:
- 70% Renta Variable (ETF IVVPESO.MX vía yfinance)
- 30% Renta Fija (CETES / Bonddia)
- Rebalanceo óptimo por flujo de caja (Cash Flow Rebalancing) sin vender posiciones.
- Respaldo atómico de datos, historial de transacciones y recordatorios automáticos configurables.
"""

from datetime import datetime
from functools import wraps
import json
import logging
import math
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Tuple

import telebot
from telebot import types
import yfinance as yf

# =====================================================================
# CONFIGURACIÓN DE LOGGING
# =====================================================================
logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("bot-inversiones")

# =====================================================================
# VARIABLES DE ENTORNO Y SEGURIDAD
# =====================================================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
MI_CHAT_ID_RAW = os.getenv("MI_CHAT_ID")

if not TELEGRAM_TOKEN:
    logger.critical("Error fatal: La variable de entorno TELEGRAM_TOKEN no está configurada.")
    sys.exit(1)

if not MI_CHAT_ID_RAW:
    logger.critical("Error fatal: La variable de entorno MI_CHAT_ID no está configurada.")
    sys.exit(1)

try:
    MI_CHAT_ID = int(MI_CHAT_ID_RAW.strip())
except ValueError:
    logger.critical(f"Error fatal: MI_CHAT_ID debe ser un número entero válido. Valor recibido: '{MI_CHAT_ID_RAW}'")
    sys.exit(1)

# Inicialización de la instancia de TeleBot
bot = telebot.TeleBot(TELEGRAM_TOKEN, parse_mode="Markdown")

# =====================================================================
# CONSTANTES DE PERSISTENCIA Y FINANZAS
# =====================================================================
DATA_DIR = Path("data")
PORTAFOLIO_PATH = DATA_DIR / "portafolio.json"
HISTORIAL_PATH = DATA_DIR / "historial.json"

TICKER_RV = "IVVPESO.MX"
FALLBACK_PRECIO_IVV = 105.00  # Precio de reserva en MXN si Yahoo Finance no responde
PESO_OBJETIVO_IVV = 0.70       # 70% Renta Variable
PESO_OBJETIVO_CETES = 0.30     # 30% Renta Fija
DIA_RECORDATORIO_DEFAULT = 15 # Día del mes por defecto para recordatorio y backup

PORTAFOLIO_DEFAULT: Dict[str, Any] = {
    "titulos_ivv": 0,
    "saldo_cetes": 0.0,
    "peso_ivv": PESO_OBJETIVO_IVV,
    "peso_cetes": PESO_OBJETIVO_CETES,
    "dia_recordatorio": DIA_RECORDATORIO_DEFAULT,
    "ultimo_recordatorio_mes": "",
}


# =====================================================================
# DECORADOR DE SEGURIDAD (AUTORIZACIÓN ESTRICTA)
# =====================================================================
def solo_autorizado(func: Callable) -> Callable:
    """
    Decorador que restringe el uso del bot exclusivamente a MI_CHAT_ID.
    Cualquier interacción de otro usuario o grupo es ignorada en silencio.
    """
    @wraps(func)
    def wrapper(message: types.Message, *args, **kwargs):
        if message.chat.id != MI_CHAT_ID:
            logger.warning(
                f"Acceso no autorizado bloqueado silenciosamente: Chat ID {message.chat.id} "
                f"(@{message.from_user.username or 'sin_user'})."
            )
            return  # Ignorar silenciosamente
        return func(message, *args, **kwargs)
    return wrapper


# =====================================================================
# CAPA DE PERSISTENCIA ATÓMICA (JSON + FSYNC)
# =====================================================================
def asegurar_directorio_datos() -> None:
    """Garantiza la existencia del directorio data/."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _guardar_json_atomico(ruta_destino: Path, datos: Any) -> None:
    """
    Guarda datos JSON de forma atómica en disco usando fsync y renombrado del sistema.
    Inmune a cortes repentinos de energía o reinicios abruptos de contenedores.
    """
    asegurar_directorio_datos()
    ruta_tmp = ruta_destino.with_suffix(".tmp")
    try:
        with open(ruta_tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        ruta_tmp.replace(ruta_destino)
        logger.info(f"Guardado atómico completado en {ruta_destino}.")
    except Exception as e:
        logger.error(f"Falla crítica en guardado atómico para {ruta_destino}: {e}")
        if ruta_tmp.exists():
            ruta_tmp.unlink(missing_ok=True)
        raise


def cargar_portafolio() -> Dict[str, Any]:
    """
    Carga el estado del portafolio desde data/portafolio.json.
    Si el archivo no existe, lo crea con la estructura por defecto.
    """
    asegurar_directorio_datos()
    if not PORTAFOLIO_PATH.exists():
        logger.info(f"Archivo {PORTAFOLIO_PATH} no encontrado. Inicializando con valores por defecto.")
        guardar_portafolio(PORTAFOLIO_DEFAULT)
        return PORTAFOLIO_DEFAULT.copy()

    try:
        with open(PORTAFOLIO_PATH, "r", encoding="utf-8") as f:
            datos = json.load(f)
            return {
                "titulos_ivv": int(datos.get("titulos_ivv", 0)),
                "saldo_cetes": float(datos.get("saldo_cetes", 0.0)),
                "peso_ivv": float(datos.get("peso_ivv", PESO_OBJETIVO_IVV)),
                "peso_cetes": float(datos.get("peso_cetes", PESO_OBJETIVO_CETES)),
                "dia_recordatorio": int(datos.get("dia_recordatorio", DIA_RECORDATORIO_DEFAULT)),
                "ultimo_recordatorio_mes": str(datos.get("ultimo_recordatorio_mes", "")),
            }
    except Exception as e:
        logger.error(f"Error al leer {PORTAFOLIO_PATH}: {e}. Retornando valores seguros.")
        return PORTAFOLIO_DEFAULT.copy()


def guardar_portafolio(datos: Dict[str, Any]) -> None:
    """Guarda el portafolio atómicamente."""
    _guardar_json_atomico(PORTAFOLIO_PATH, datos)


def cargar_historial() -> List[Dict[str, Any]]:
    """Carga la lista de aportaciones confirmadas desde data/historial.json."""
    asegurar_directorio_datos()
    if not HISTORIAL_PATH.exists():
        return []
    try:
        with open(HISTORIAL_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error al leer historial: {e}")
        return []


def guardar_historial(historial: List[Dict[str, Any]]) -> None:
    """Guarda el historial de operaciones atómicamente."""
    _guardar_json_atomico(HISTORIAL_PATH, historial)


def registrar_en_historial(titulos: int, dinero_cetes: float, precio_ivv: float) -> Dict[str, Any]:
    """Registra una nueva aportación en data/historial.json."""
    historial = cargar_historial()
    dinero_ivv = round(titulos * precio_ivv, 2)
    dinero_cetes_redondeado = round(dinero_cetes, 2)
    nuevo_registro = {
        "id": len(historial) + 1,
        "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "titulos_ivv": titulos,
        "precio_ivv_ref": precio_ivv,
        "dinero_ivv": dinero_ivv,
        "dinero_cetes": dinero_cetes_redondeado,
        "total_aportado": round(dinero_ivv + dinero_cetes_redondeado, 2),
    }
    historial.append(nuevo_registro)
    guardar_historial(historial)
    return nuevo_registro


# =====================================================================
# CONSULTA DE MERCADO EN TIEMPO REAL (yfinance)
# =====================================================================
def obtener_precio_ivv() -> Tuple[float, bool]:
    """
    Consulta el último precio de cierre de IVVPESO.MX en Yahoo Finance.
    Retorna una tupla (precio: float, es_fallback: bool).
    """
    try:
        ticker = yf.Ticker(TICKER_RV)
        precio: float | None = None

        if hasattr(ticker, "fast_info") and ticker.fast_info:
            precio = ticker.fast_info.get("last_price") or ticker.fast_info.get("lastPrice")

        if precio is None or precio <= 0:
            hist = ticker.history(period="5d")
            if not hist.empty and "Close" in hist:
                precio = float(hist["Close"].iloc[-1])

        if precio is not None and precio > 0:
            logger.info(f"Precio obtenido en tiempo real para {TICKER_RV}: ${precio:.2f} MXN")
            return round(float(precio), 2), False

    except Exception as e:
        logger.warning(f"Excepción al consultar {TICKER_RV} en Yahoo Finance: {e}")

    logger.warning(f"Utilizando precio de reserva (fallback): ${FALLBACK_PRECIO_IVV:.2f} MXN")
    return FALLBACK_PRECIO_IVV, True


# =====================================================================
# MATEMÁTICA Y LÓGICA DE REBALANCEO PASIVO (CASH-FLOW REBALANCING)
# =====================================================================
def calcular_distribucion_rebalanceo(
    titulos_ivv: int,
    saldo_cetes: float,
    nuevo_capital: float,
    precio_ivv: float,
    peso_ivv: float = PESO_OBJETIVO_IVV,
    peso_cetes: float = PESO_OBJETIVO_CETES,
) -> Dict[str, Any]:
    """
    Calcula la distribución óptima del nuevo capital sin vender activos.
    Convierte la porción de RV a títulos enteros y transfiere
    cualquier remanente directamente a CETES.
    """
    valor_ivv_actual = titulos_ivv * precio_ivv
    valor_cetes_actual = saldo_cetes
    patrimonio_actual = valor_ivv_actual + valor_cetes_actual
    patrimonio_proyectado = patrimonio_actual + nuevo_capital

    meta_valor_ivv = patrimonio_proyectado * peso_ivv
    meta_valor_cetes = patrimonio_proyectado * peso_cetes

    # Asignación teórica continua
    if valor_ivv_actual >= meta_valor_ivv:
        asignacion_teorica_ivv = 0.0
        asignacion_teorica_cetes = nuevo_capital
    elif valor_cetes_actual >= meta_valor_cetes:
        asignacion_teorica_ivv = nuevo_capital
        asignacion_teorica_cetes = 0.0
    else:
        asignacion_teorica_ivv = meta_valor_ivv - valor_ivv_actual
        asignacion_teorica_cetes = meta_valor_cetes - valor_cetes_actual

    # Regla: Títulos enteros en IVV
    titulos_a_comprar = math.floor(asignacion_teorica_ivv / precio_ivv) if precio_ivv > 0 else 0
    dinero_real_ivv = titulos_a_comprar * precio_ivv

    # Todo el remanente en centavos/pesos que no alcance para un título va a CETES
    dinero_real_cetes = nuevo_capital - dinero_real_ivv

    # Métricas post-ejecución
    nuevos_titulos_ivv = titulos_ivv + titulos_a_comprar
    nuevo_saldo_cetes = saldo_cetes + dinero_real_cetes
    nuevo_valor_ivv = nuevos_titulos_ivv * precio_ivv
    patrimonio_final = nuevo_valor_ivv + nuevo_saldo_cetes

    pct_final_ivv = (nuevo_valor_ivv / patrimonio_final * 100) if patrimonio_final > 0 else 0.0
    pct_final_cetes = (nuevo_saldo_cetes / patrimonio_final * 100) if patrimonio_final > 0 else 0.0

    return {
        "titulos_a_comprar": titulos_a_comprar,
        "dinero_real_ivv": dinero_real_ivv,
        "dinero_real_cetes": dinero_real_cetes,
        "nuevos_titulos_ivv": nuevos_titulos_ivv,
        "nuevo_saldo_cetes": nuevo_saldo_cetes,
        "nuevo_valor_ivv": nuevo_valor_ivv,
        "patrimonio_final": patrimonio_final,
        "pct_final_ivv": pct_final_ivv,
        "pct_final_cetes": pct_final_cetes,
    }


def formato_moneda(monto: float) -> str:
    """Formatea cantidades monetarias a formato legible mexicano."""
    return f"${monto:,.2f} MXN"


def enviar_archivos_backup(chat_id: int, motivo: str = "Respaldo manual") -> None:
    """Envía los archivos JSON como documentos adjuntos de Telegram."""
    asegurar_directorio_datos()
    enviados = 0

    if PORTAFOLIO_PATH.exists():
        with open(PORTAFOLIO_PATH, "rb") as f:
            bot.send_document(
                chat_id,
                f,
                caption=f"📁 *Respaldo de Portafolio*\n{motivo} • `{datetime.now().strftime('%Y-%m-%d %H:%M')}`"
            )
        enviados += 1

    if HISTORIAL_PATH.exists():
        with open(HISTORIAL_PATH, "rb") as f:
            bot.send_document(
                chat_id,
                f,
                caption=f"📜 *Respaldo de Historial de Operaciones*\n{motivo} • `{datetime.now().strftime('%Y-%m-%d %H:%M')}`"
            )
        enviados += 1

    if enviados == 0:
        bot.send_message(chat_id, "⚠️ No se encontraron archivos de datos para respaldar.")


# =====================================================================
# COMANDOS DE TELEGRAM
# =====================================================================

@bot.message_handler(commands=["start", "ayuda", "help"])
@solo_autorizado
def comando_ayuda(message: types.Message) -> None:
    """Muestra el menú de bienvenida, explicación de la estrategia y comandos disponibles."""
    texto = (
        "🏛 *Asesor Financiero y Rebalanceo Pasivo*\n"
        "Estrategia de Asignación Fija para México:\n"
        "• *70% Renta Variable:* ETF `IVVPESO.MX` (S&P 500 cubierto en pesos)\n"
        "• *30% Renta Fija:* CETES / Bonddia (Cetesdirecto)\n\n"
        "📌 *Operaciones y Análisis:*\n"
        "📊 `/portafolio` - Consulta el estado actual y porcentajes de tu patrimonio.\n"
        "💵 `/aportar <monto>` - Calcula compras óptimas sin vender nada.\n"
        "✅ `/confirmar_compra <titulos> <cetes>` - Registra compras en portafolio e historial.\n"
        "🔄 `/actualizar <titulos> <saldo>` - Sobrescribe saldos manualmente ante ajustes.\n\n"
        "⚙️ *Automatización y Respaldo:*\n"
        "📜 `/historial` - Consulta tus últimas aportaciones registradas.\n"
        "⏰ `/recordatorio [dia]` - Consulta o cambia el día del mes para la alerta y backup automático.\n"
        "💾 `/backup` - Descarga tus archivos de datos directamente a este chat.\n\n"
        "ℹ️ `/ayuda` - Vuelve a mostrar este mensaje."
    )
    bot.reply_to(message, texto)


@bot.message_handler(commands=["portafolio"])
@solo_autorizado
def comando_portafolio(message: types.Message) -> None:
    """Muestra el resumen cuantitativo del portafolio actual."""
    portafolio = cargar_portafolio()
    titulos_ivv = portafolio["titulos_ivv"]
    saldo_cetes = portafolio["saldo_cetes"]

    precio_ivv, es_fallback = obtener_precio_ivv()
    valor_ivv = titulos_ivv * precio_ivv
    patrimonio_total = valor_ivv + saldo_cetes

    pct_ivv = (valor_ivv / patrimonio_total * 100) if patrimonio_total > 0 else 0.0
    pct_cetes = (saldo_cetes / patrimonio_total * 100) if patrimonio_total > 0 else 0.0

    fuente_precio = "⚠️ Precio estimado (fallback)" if es_fallback else "🟢 Mercado en vivo"
    dia_rec = portafolio.get("dia_recordatorio", DIA_RECORDATORIO_DEFAULT)

    texto = (
        "📊 *Estado Actual del Portafolio*\n\n"
        f"📈 *Renta Variable (IVVPESO.MX):*\n"
        f"• Títulos en posesión: `{titulos_ivv}`\n"
        f"• Cotización: `{formato_moneda(precio_ivv)}` ({fuente_precio})\n"
        f"• Valor acumulado: `{formato_moneda(valor_ivv)}`\n"
        f"• Asignación real: *{pct_ivv:.2f}%* (Meta: 70.00%)\n\n"
        f"🛡 *Renta Fija (CETES / Bonddia):*\n"
        f"• Saldo en cuenta: `{formato_moneda(saldo_cetes)}`\n"
        f"• Asignación real: *{pct_cetes:.2f}%* (Meta: 30.00%)\n\n"
        f"💼 *Patrimonio Neto Total:* `{formato_moneda(patrimonio_total)}`\n"
        f"⏰ *Recordatorio mensual:* Día {dia_rec} de cada mes"
    )
    bot.reply_to(message, texto)


@bot.message_handler(commands=["actualizar"])
@solo_autorizado
def comando_actualizar(message: types.Message) -> None:
    """
    Sobrescribe manualmente los títulos y saldos.
    Uso: /actualizar <titulos_ivv> <saldo_cetes>
    """
    partes = message.text.strip().split()
    if len(partes) != 3:
        bot.reply_to(
            message,
            "❌ *Sintaxis incorrecta.*\n"
            "Uso: `/actualizar <titulos_ivv> <saldo_cetes>`\n"
            "Ejemplo: `/actualizar 150 12500.50`"
        )
        return

    try:
        nuevos_titulos = int(partes[1])
        nuevo_saldo = float(partes[2])

        if nuevos_titulos < 0 or nuevo_saldo < 0:
            bot.reply_to(message, "❌ Los valores no pueden ser negativos.")
            return

        portafolio = cargar_portafolio()
        portafolio["titulos_ivv"] = nuevos_titulos
        portafolio["saldo_cetes"] = nuevo_saldo
        guardar_portafolio(portafolio)

        precio_ivv, _ = obtener_precio_ivv()
        nuevo_patrimonio = (nuevos_titulos * precio_ivv) + nuevo_saldo

        bot.reply_to(
            message,
            "✅ *Portafolio actualizado correctamente.*\n\n"
            f"• Nuevos títulos IVVPESO: `{nuevos_titulos}`\n"
            f"• Nuevo saldo CETES: `{formato_moneda(nuevo_saldo)}`\n"
            f"• Patrimonio total estimado: `{formato_moneda(nuevo_patrimonio)}`"
        )
    except ValueError:
        bot.reply_to(
            message,
            "❌ *Error de formato.* Asegúrate de que los títulos sean un número entero y el saldo sea un número válido."
        )


@bot.message_handler(commands=["aportar"])
@solo_autorizado
def comando_aportar(message: types.Message) -> None:
    """
    Calcula el rebalanceo óptimo con un nuevo aporte de capital.
    Uso: /aportar <monto>
    """
    partes = message.text.strip().split()
    if len(partes) != 2:
        bot.reply_to(
            message,
            "❌ *Sintaxis incorrecta.*\n"
            "Uso: `/aportar <monto>`\n"
            "Ejemplo: `/aportar 6500`"
        )
        return

    try:
        monto_aporte = float(partes[1])
        if monto_aporte <= 0:
            bot.reply_to(message, "❌ El monto a aportar debe ser mayor a cero.")
            return
    except ValueError:
        bot.reply_to(message, "❌ El monto ingresado no es un número válido.")
        return

    portafolio = cargar_portafolio()
    precio_ivv, es_fallback = obtener_precio_ivv()

    resultado = calcular_distribucion_rebalanceo(
        titulos_ivv=portafolio["titulos_ivv"],
        saldo_cetes=portafolio["saldo_cetes"],
        nuevo_capital=monto_aporte,
        precio_ivv=precio_ivv,
        peso_ivv=portafolio.get("peso_ivv", PESO_OBJETIVO_IVV),
        peso_cetes=portafolio.get("peso_cetes", PESO_OBJETIVO_CETES),
    )

    fuente_precio = "⚠️ Precio estimado (fallback)" if es_fallback else "🟢 Mercado en vivo"

    titulos_a_comprar = resultado["titulos_a_comprar"]
    dinero_ivv = resultado["dinero_real_ivv"]
    dinero_cetes = resultado["dinero_real_cetes"]

    texto = (
        "🎯 *Plan de Rebalanceo Óptimo*\n\n"
        f"💵 *Capital inyectado:* `{formato_moneda(monto_aporte)}`\n"
        f"🏷 *Cotización IVVPESO.MX:* `{formato_moneda(precio_ivv)}` ({fuente_precio})\n\n"
        "📋 *Instrucciones de Compra:*\n"
        f"• 📈 *GBM / Casa de Bolsa:* Comprar *{titulos_a_comprar}* títulos de `IVVPESO.MX` "
        f"(Costo total: `{formato_moneda(dinero_ivv)}`)\n"
        f"• 🛡 *Cetesdirecto:* Transferir `{formato_moneda(dinero_cetes)}` a CETES / Bonddia\n\n"
        "📊 *Proyección Resultante:*\n"
        f"• RV (IVVPESO): *{resultado['pct_final_ivv']:.2f}%*\n"
        f"• RF (CETES): *{resultado['pct_final_cetes']:.2f}%*\n"
        f"• Nuevo Patrimonio Estimado: `{formato_moneda(resultado['patrimonio_final'])}`\n\n"
        "⚡️ *Para confirmar una vez ejecutadas las operaciones:*\n"
        f"`/confirmar_compra {titulos_a_comprar} {dinero_cetes:.2f}`"
    )
    bot.reply_to(message, texto)


@bot.message_handler(commands=["confirmar_compra"])
@solo_autorizado
def comando_confirmar_compra(message: types.Message) -> None:
    """
    Suma las operaciones al portafolio y las almacena en el historial.
    Uso: /confirmar_compra <titulos_comprados> <dinero_cetes>
    """
    partes = message.text.strip().split()
    if len(partes) != 3:
        bot.reply_to(
            message,
            "❌ *Sintaxis incorrecta.*\n"
            "Uso: `/confirmar_compra <titulos_comprados> <dinero_cetes>`\n"
            "Ejemplo: `/confirmar_compra 32 1530.50`"
        )
        return

    try:
        titulos_comprados = int(partes[1])
        dinero_cetes = float(partes[2])

        if titulos_comprados < 0 or dinero_cetes < 0:
            bot.reply_to(message, "❌ Los valores comprados no pueden ser negativos.")
            return

        portafolio = cargar_portafolio()
        precio_ivv, _ = obtener_precio_ivv()

        # Actualizar portafolio persistente
        portafolio["titulos_ivv"] += titulos_comprados
        portafolio["saldo_cetes"] = round(portafolio["saldo_cetes"] + dinero_cetes, 2)
        guardar_portafolio(portafolio)

        # Registrar en bitácora histórica
        registro = registrar_en_historial(titulos_comprados, dinero_cetes, precio_ivv)

        valor_ivv = portafolio["titulos_ivv"] * precio_ivv
        saldo_cetes = portafolio["saldo_cetes"]
        patrimonio_total = valor_ivv + saldo_cetes

        pct_ivv = (valor_ivv / patrimonio_total * 100) if patrimonio_total > 0 else 0.0
        pct_cetes = (saldo_cetes / patrimonio_total * 100) if patrimonio_total > 0 else 0.0

        bot.reply_to(
            message,
            "🎉 *¡Operación registrada con éxito!*\n\n"
            f"• Títulos totales IVVPESO: `{portafolio['titulos_ivv']}` (+{titulos_comprados})\n"
            f"• Saldo total CETES: `{formato_moneda(saldo_cetes)}` (+{formato_moneda(dinero_cetes)})\n"
            f"• Total inyectado hoy: `{formato_moneda(registro['total_aportado'])}`\n"
            f"• Nuevo Patrimonio Total: `{formato_moneda(patrimonio_total)}`\n"
            f"• Balance actual: *{pct_ivv:.2f}% RV / {pct_cetes:.2f}% RF*"
        )
    except ValueError:
        bot.reply_to(
            message,
            "❌ *Error de formato.* Verifica que los títulos sean enteros y el dinero sea un valor numérico."
        )


@bot.message_handler(commands=["backup", "respaldo"])
@solo_autorizado
def comando_backup(message: types.Message) -> None:
    """Envía los archivos JSON de datos directamente al chat de Telegram."""
    bot.reply_to(message, "📦 Preparando archivos de copia de seguridad...")
    enviar_archivos_backup(message.chat.id, motivo="Respaldo manual bajo demanda")


@bot.message_handler(commands=["historial"])
@solo_autorizado
def comando_historial(message: types.Message) -> None:
    """Muestra las últimas aportaciones registradas en el archivo de historial."""
    historial = cargar_historial()
    if not historial:
        bot.reply_to(
            message,
            "📜 *Sin historial de operaciones.*\n"
            "Aún no has registrado compras con `/confirmar_compra`."
        )
        return

    ultimos = historial[-5:]  # Últimos 5 movimientos
    total_historico = sum(item.get("total_aportado", 0.0) for item in historial)

    lineas = ["📜 *Historial de Últimas Aportaciones:*"]
    for reg in reversed(ultimos):
        lineas.append(
            f"\n🗓 *{reg['fecha']}* (ID #{reg['id']})\n"
            f"• IVVPESO: `+{reg['titulos_ivv']} títulos` ({formato_moneda(reg['dinero_ivv'])})\n"
            f"• CETES: `+{formato_moneda(reg['dinero_cetes'])}`\n"
            f"• Total aportado: *{formato_moneda(reg['total_aportado'])}*"
        )

    lineas.append(f"\n💰 *Capital Histórico Total Inyectado:* `{formato_moneda(total_historico)}`")
    bot.reply_to(message, "\n".join(lineas))


@bot.message_handler(commands=["recordatorio"])
@solo_autorizado
def comando_recordatorio(message: types.Message) -> None:
    """
    Muestra o configura el día del mes para el recordatorio de inversión y backup automático.
    Uso: /recordatorio [dia_del_mes]
    """
    partes = message.text.strip().split()
    portafolio = cargar_portafolio()
    dia_actual = portafolio.get("dia_recordatorio", DIA_RECORDATORIO_DEFAULT)

    # Si solo escribe /recordatorio, mostrar configuración actual
    if len(partes) == 1:
        bot.reply_to(
            message,
            f"⏰ *Configuración de Recordatorio y Respaldo Mensual*\n\n"
            f"Actualmente programado para el día *{dia_actual}* de cada mes.\n\n"
            "Para cambiar el día de tu pago/inversión, escribe:\n"
            "`/recordatorio <nuevo_dia>`\n"
            "_Ejemplos:_ `/recordatorio 20` o `/recordatorio 1`"
        )
        return

    if len(partes) == 2:
        try:
            nuevo_dia = int(partes[1])
            if nuevo_dia < 1 or nuevo_dia > 31:
                bot.reply_to(message, "❌ El día debe estar entre el 1 y el 31 del mes.")
                return

            portafolio["dia_recordatorio"] = nuevo_dia
            # Resetear para permitir que se ejecute si coincide hoy
            portafolio["ultimo_recordatorio_mes"] = ""
            guardar_portafolio(portafolio)

            bot.reply_to(
                message,
                f"✅ *¡Configuración guardada exitosamente!*\n\n"
                f"Tu recordatorio de inversión y respaldo automático ahora se ejecutarán "
                f"el día *{nuevo_dia}* de cada mes."
            )
        except ValueError:
            bot.reply_to(message, "❌ Por favor introduce un número entero válido (1 al 31).")
        return

    bot.reply_to(message, "❌ Sintaxis: `/recordatorio [dia_del_mes]`")


# =====================================================================
# HILO PLANIFICADOR (RECORDATORIO Y BACKUP AUTOMÁTICO)
# =====================================================================
def bucle_planificador_mensual() -> None:
    """
    Hilo daemon que revisa periódicamente el calendario.
    Cuando llega el día configurado por el usuario, envía el recordatorio
    y la copia de seguridad de portafolio e historial automáticamente.
    """
    logger.info("Hilo planificador de recordatorios y backups automáticos iniciado.")
    while True:
        try:
            ahora = datetime.now()
            mes_actual = ahora.strftime("%Y-%m")
            portafolio = cargar_portafolio()
            dia_configurado = int(portafolio.get("dia_recordatorio", DIA_RECORDATORIO_DEFAULT))
            ultimo_mes_procesado = str(portafolio.get("ultimo_recordatorio_mes", ""))

            # Si hoy es el día configurado y aún no se ha disparado este mes
            if ahora.day == dia_configurado and ultimo_mes_procesado != mes_actual:
                logger.info(f"Disparando recordatorio mensual y backup automático del día {ahora.day}...")
                
                # 1. Enviar alerta de aportación
                bot.send_message(
                    MI_CHAT_ID,
                    f"🔔 *¡Recordatorio de Inversión Mensual! (Día {ahora.day})*\n\n"
                    "Llegó tu fecha programada de aportación de capital.\n"
                    "Ejecuta `/aportar <monto>` para calcular la distribución óptima entre `IVVPESO.MX` y `CETES`.\n\n"
                    "📦 *Adjunto encontrarás tu respaldo automático mensual de seguridad.*"
                )

                # 2. Enviar archivos adjuntos de respaldo
                enviar_archivos_backup(MI_CHAT_ID, motivo="Copia de seguridad mensual automática")

                # 3. Marcar mes como procesado para no repetir
                portafolio["ultimo_recordatorio_mes"] = mes_actual
                guardar_portafolio(portafolio)
                logger.info("Recordatorio y backup mensual completados con éxito.")

        except Exception as e:
            logger.error(f"Excepción en bucle planificador: {e}")

        # Revisar cada 15 minutos (900 segundos)
        time.sleep(900)


# =====================================================================
# INICIALIZACIÓN Y BUCLE CONTINUO
# =====================================================================
def iniciar_bot() -> None:
    """Inicia el bot con Long Polling infinito, planificador y notificación de arranque."""
    logger.info("Verificando almacenamiento persistente...")
    asegurar_directorio_datos()
    cargar_portafolio()
    cargar_historial()

    # Iniciar hilo en segundo plano para recordatorios y backups automáticos
    hilo_scheduler = threading.Thread(target=bucle_planificador_mensual, daemon=True)
    hilo_scheduler.start()

    logger.info("Iniciando servicio de Long Polling continuo...")
    logger.info(f"Bot configurado exclusivamente para MI_CHAT_ID: {MI_CHAT_ID}")

    # Notificación de arranque exitoso al chat privado
    try:
        portafolio = cargar_portafolio()
        dia_rec = portafolio.get("dia_recordatorio", DIA_RECORDATORIO_DEFAULT)
        bot.send_message(
            MI_CHAT_ID,
            "🟢 *Bot de Inversiones en línea*\n"
            "El servicio se ha iniciado y está listo para operar.\n"
            f"📅 Recordatorio programado para el día *{dia_rec}* de cada mes.\n"
            "Escribe `/ayuda` para ver la lista de comandos."
        )
        logger.info("Notificación de arranque enviada a Telegram con éxito.")
    except Exception as e:
        logger.warning(f"No se pudo enviar la notificación de arranque a Telegram: {e}")

    try:
        bot.infinity_polling(timeout=20, long_polling_timeout=20)
    except Exception as e:
        logger.critical(f"Falla inesperada en infinity_polling: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    iniciar_bot()
