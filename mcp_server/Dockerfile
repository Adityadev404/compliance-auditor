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

# MCP uses stdio transport — no port needed when used with Claude Desktop
# Port 8080 is exposed for HTTP-based MCP clients (future use)
EXPOSE 8080

CMD ["python", "server.py"]
