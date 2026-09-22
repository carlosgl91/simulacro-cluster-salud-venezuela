# Imagen del tablero, pensada para Cloud Run (o cualquier hosting de
# contenedores). No trae datos ni credenciales: los datos salen de Postgres a
# través de TABLERO_DB_URL, que se inyecta como variable de entorno del
# servicio (en Cloud Run, idealmente desde Secret Manager). Si esa variable no
# está, el tablero cae a los CSV de data/ que vienen en la imagen.
FROM python:3.12-slim

# - PYTHONDONTWRITEBYTECODE: no escribir .pyc en un contenedor efímero.
# - PYTHONUNBUFFERED: que los logs salgan al instante en Cloud Logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias primero, en su propia capa: así un cambio en app.py no
# obliga a reinstalar todo en cada build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Sin privilegios: si algo se compromete, no corre como root.
RUN useradd --create-home --uid 1000 tablero && chown -R tablero:tablero /app
USER tablero

# Cloud Run inyecta PORT (8080 por defecto). Se respeta esa variable en vez de
# fijar un puerto, que es lo que exige la plataforma.
ENV PORT=8080
EXPOSE 8080

# --server.address 0.0.0.0: el contenedor tiene que escuchar fuera de localhost.
# --server.headless: sin intentar abrir un navegador ni pedir correo.
# --browser.gatherUsageStats false: sin telemetría a terceros.
CMD streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --browser.gatherUsageStats=false
