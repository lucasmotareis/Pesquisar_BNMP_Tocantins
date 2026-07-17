FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BNMP_DATA_DIR=/app/data \
    BNMP_DATA_FILE=/app/data/mandados_processados.json

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY painel_tocantins.py painel_tocantins.html ./
RUN mkdir -p /app/data

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3).read()"

CMD ["python", "painel_tocantins.py", "--host", "0.0.0.0", "--port", "8765"]
