# ==========================================
# Dockerfile para Bot de Inversiones Telegram
# Python 3.11 Slim - Producción
# ==========================================
FROM python:3.11-slim

# Evitar generación de archivos .pyc y forzar salida inmediata de logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias primero para aprovechar el sistema de capas de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código fuente de la aplicación
COPY . .

# Directorio de datos para persistencia (montado en volumen)
RUN mkdir -p /app/data

# Ejecutar el bot
CMD ["python", "bot.py"]

