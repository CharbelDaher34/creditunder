FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY mockups/dms/pyproject.toml mockups/dms/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mockups/dms/app.py ./mockups/dms/app.py
COPY mockups/__init__.py ./mockups/__init__.py
COPY mockups/dms/__init__.py ./mockups/dms/__init__.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8001
CMD ["python", "-m", "mockups.dms.app"]
