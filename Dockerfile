FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# cairosvg requires cairo/pango/gdk-pixbuf system libraries.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-xlib-2.0-0 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY assets ./assets
COPY app ./app

# Common PaaS providers expose PORT. We support PORT (preferred) and SERVICE_PORT (fallback).
EXPOSE 9000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-${SERVICE_PORT:-9000}}"]
