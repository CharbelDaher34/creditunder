FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

# Reuse creditunder's locked dependency set — both packages share the same deps.
COPY src/creditunder/pyproject.toml src/creditunder/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy both packages so workbench can import creditunder's ORM models.
COPY src/creditunder/ ./src/creditunder/
COPY src/workbench/ ./src/workbench/

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH=/app/src

CMD ["uvicorn", "workbench.main:app", "--host", "0.0.0.0", "--port", "8004"]
