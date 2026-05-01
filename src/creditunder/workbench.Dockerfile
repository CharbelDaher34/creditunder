FROM python:3.14-slim

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.3.0 /uv /uvx /bin/

# Copy dependencies
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application
COPY src/creditunder/ ./src/creditunder/

ENV PYTHONPATH=/app/src:/app

CMD ["uv", "run", "uvicorn", "creditunder.workbench.app:app", "--host", "0.0.0.0", "--port", "8004"]
