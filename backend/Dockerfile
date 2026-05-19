FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN adduser --disabled-password --gecos "" --home /home/appuser appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Increased timeout to 180s to handle slow first-run LLM inference
CMD ["gunicorn", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "180", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "main:app"]
