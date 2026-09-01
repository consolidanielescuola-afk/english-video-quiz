# Immagine per il deploy del backend su Render (o qualsiasi host che supporti Docker).
# Include Chromium (via Playwright) necessario per la generazione PDF.

FROM python:3.11-slim

WORKDIR /app

# Librerie di sistema minime richieste in fase di build/runtime da Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Scarica Chromium + tutte le dipendenze di sistema necessarie per eseguirlo headless
RUN playwright install --with-deps chromium

COPY . .

# Render (e la maggior parte dei PaaS) assegna la porta dinamicamente tramite $PORT
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
