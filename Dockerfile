FROM python:3.11-slim

# Node is required because the filesystem MCP server (and many community
# MCP servers) ship as npx-run Node packages, launched as subprocesses by
# the Python MCP client over stdio.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data

EXPOSE 8000
# Shell form so $PORT (supplied by Render and most PaaS) expands; 8000 locally.
# exec replaces the shell so uvicorn receives SIGTERM directly on shutdown.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
