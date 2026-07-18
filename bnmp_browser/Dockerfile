FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    SCREEN_WIDTH=1366 \
    SCREEN_HEIGHT=768 \
    NOVNC_PORT=6080 \
    EXPORT_PORT=7788 \
    CDP_PORT=9222 \
    BNMP_PORTAL_URL=https://portalbnmp.pdpj.jus.br/#/pesquisa-peca \
    BNMP_COOKIES_FILE=/app/data/bnmp_cookies.json

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        chromium \
        fluxbox \
        fonts-liberation \
        novnc \
        procps \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY bnmp_browser/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY bnmp_browser/session_exporter.py bnmp_browser/start.sh ./
RUN chmod +x /app/start.sh \
    && mkdir -p /app/data /browser-profile

EXPOSE 6080 7788

CMD ["bash", "/app/start.sh"]
