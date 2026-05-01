FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY src/creditunder/pyproject.toml src/creditunder/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/creditunder/ ./src/creditunder/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src:/app

CMD ["uvicorn", "creditunder.workbench.app:app", "--host", "0.0.0.0", "--port", "8004"]
