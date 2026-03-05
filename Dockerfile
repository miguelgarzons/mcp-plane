FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MCP_DATA_DIR=/app/data \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-compile -r /app/requirements.txt

COPY server.py /app/server.py
COPY task_mcp /app/task_mcp

RUN mkdir -p /app/data && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["fastmcp", "run", "server.py:mcp", "--transport", "http", "--host", "0.0.0.0", "--port", "8000", "--path", "/mcp"]
