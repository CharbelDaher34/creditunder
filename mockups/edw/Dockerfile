FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/

WORKDIR /app

COPY mockups/edw/pyproject.toml mockups/edw/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY mockups/edw/app.py ./mockups/edw/app.py
COPY mockups/__init__.py ./mockups/__init__.py
COPY mockups/edw/__init__.py ./mockups/edw/__init__.py

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8002
CMD ["python", "-m", "mockups.edw.app"]
