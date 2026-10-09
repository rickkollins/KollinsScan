FROM python:3.12-slim

# Tesseract plus its language packs. English is always installed; add more
# with e.g. TESSERACT_LANGS="eng spa fra deu" (see README).
ARG TESSERACT_LANGS="eng"
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
      $(for l in $TESSERACT_LANGS; do echo "tesseract-ocr-$l"; done) \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY kollinsscan ./kollinsscan

RUN useradd --system --uid 10001 kollinsscan \
 && mkdir /data && chown kollinsscan /data
USER kollinsscan
ENV KOLLINSSCAN_DATA_DIR=/data PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

# Only Caddy can reach this port (it isn't published), so trusting its
# X-Forwarded-For header is safe.
CMD ["uvicorn", "kollinsscan.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
