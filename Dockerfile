FROM python:3.11-slim

LABEL maintainer="Jose Florez"
LABEL description="Bot de Asignación de Juzgados por geolocalización"
LABEL version="1.0.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Bogota

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*


COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

RUN mkdir -p logs

EXPOSE 8000

RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]