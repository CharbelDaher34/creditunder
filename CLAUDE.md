# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Credit Underwriting Platform** for Alinma Bank. Automates credit application processing: consumes Kafka events from Siebel CRM, fetches documents from DMS, verifies and extracts structured data via AI Service, applies product business rules, generates a PDF report, and delivers outputs to DMS and EDW.

The full system design rationale is in [credit_underwriting_system_requirement.md](credit_underwriting_system_requirement.md).

## Tech Stack

- Python 3.14, **uv** for package management
- PostgreSQL + SQLAlchemy (asyncpg) + Alembic
- Kafka via `aiokafka` (local dev: Kafka KRaft via `apache/kafka:3.7.2` in docker-compose — no ZooKeeper)
- `pydantic-ai` for structured AI calls (OpenAI-compatible backend, configurable via env)
- FastAPI + uvicorn for the workbench API and mockup services
- Jinja2 + WeasyPrint for HTML-to-PDF report generation
- Vue 3 + Vite (TypeScript) for the Reviewer Workbench UI — served by nginx
- structlog for structured logging
- OpenTelemetry SDK scaffolded in `otel_observability.py` (not wired to the pipeline yet)

## Commands

```bash
# Install dependencies
uv sync

# Start all infrastructure, run migrations, and start the processor in one command
make infra

# Start infrastructure only (detached), run migrations, but skip the processor
make infra-bg

# Apply database migrations  (DB is internal — must run inside Docker or via make)
docker compose exec processor uv run alembic upgrade head

# Generate a new migration after model changes  (runs on the host against local uv env)
uv run alembic revision --autogenerate -m "description"

# Publish sample CRM events to Kafka
# Kafka is not exposed externally — publish through the CRM container:
docker compose exec crm python -m mockups.crm.publisher           # all samples
docker compose exec crm python -m mockups.crm.publisher --event-index 0  # single event
# Alternatively use the workbench UI Submit Application button (goes via CRM HTTP API)

# Access the database (postgres and postgres-replica are internal-only)
docker compose exec postgres psql -U creditunder
docker compose exec postgres-replica psql -U creditunder

# Run tests
uv run pytest
uv run pytest tests/unit/test_handlers.py          # single file
uv run pytest -k "test_name"                        # single test

# Format / lint
uv run black . && uv run isort .
uv run flake8 .

# Workbench UI — local dev (proxies /api to port 8004)
cd src/workbench-ui && npm install && npm run dev

# Workbench UI — production build (output goes to dist/, built into nginx Docker image)
cd src/workbench-ui && npm run build
```

## Directory Structure

```
src/creditunder/
├── config.py               # pydantic-settings: all env vars (DATABASE_URL, AI_*, DMS_*, CRM_*, WORKBENCH_*)
├── observability.py        # structlog configuration
├── otel_observability.py   # OpenTelemetry scaffolding — Telemetry singleton (not wired to pipeline yet)
├── __main__.py             # entrypoint → ApplicationProcessor.run()
├── Dockerfile              # Dockerfile for the processor service
├── workbench.Dockerfile    # Dockerfile for the workbench API service
├── entrypoint.sh           # Docker entrypoint (waits for infra, runs migrations, starts processor)
├── domain/
│   ├── enums.py            # ProductType, DocumentType, ValidationOutcome, Recommendation,
│   │                       #   CaseStatus, DocumentStatus, EDWStatus, CaseReportStatus,
│   │                       #   InboundEventStatus, JobType, JobStatus
│   └── models.py           # ExtractedField[T], DocumentResult, CaseResult, ApplicationEvent
├── documents/
│   ├── __init__.py         # EXTRACTION_SCHEMA_REGISTRY (DocumentType → schema class)
│   ├── base.py             # BaseExtractionSchema
│   ├── id_document.py      # IDDocumentExtraction (pydantic-ai output schema)
│   └── salary_certificate.py  # SalaryCertificateExtraction (pydantic-ai output schema)
├── handlers/
│   ├── base.py             # BaseProductHandler (ABC) + _derive_recommendation()
│   ├── registry.py         # get_handler(ProductType) → handler instance
│   └── personal_finance.py # PersonalFinanceHandler — rules: ID_NUMBER_MISMATCH, ID_NAME_CHECK,
│                           #   ID_EXPIRED, SALARY_EMPLOYER_MATCH, SALARY_DEVIATION
├── services/
│   ├── ai_client.py        # AIClient — verify_and_extract(), generate_narrative()
│   ├── dms_client.py       # DMSClient — fetch_document(), upload_document()
│   └── edw_client.py       # EDWClient — export()
├── db/
│   ├── models.py           # SQLAlchemy ORM (all tables)
│   └── session.py          # AsyncSessionLocal (write), AsyncReadSessionLocal (read replica),
│                           #   get_read_session() FastAPI dependency
├── pipeline/
│   ├── processor.py        # ApplicationProcessor — Kafka consumer + full pipeline orchestration
│   ├── report_generator.py # ReportGenerator — AI narrative + Jinja2 HTML + WeasyPrint PDF
│   └── templates/
│       └── report.html     # Jinja2 report template
└── workbench/
    └── app.py              # Reviewer Workbench API (FastAPI, port 8004)
                            #   /api/v1 — spec-aligned read-only endpoints (uses read replica)
                            #   /api/_demo — demo helpers (submit, DMS upload proxy, SSE notifications)

src/workbench-ui/           # Vue 3 + Vite (TypeScript) — Reviewer Workbench SPA
├── src/
│   ├── App.vue             # Shell: sticky app bar, SSE reconnect on identity change
│   ├── api.ts              # apiGet/apiPost/apiBlob/eventSource — injects X-User-Id/X-User-Role headers
│   ├── userStore.ts        # Reactive identity store — 4 demo users, localStorage persistence
│   ├── style.css           # Design system: CSS custom properties, panel, kpi-grid, chip, timeline, toast
│   └── components/
│       ├── Dashboard.vue      # KPI cards, filter bar, cases table (search, status, rec, product filters)
│       ├── CaseDetails.vue    # Slide-in drawer: hero card, 4 tabs (Overview/Documents/Validations/Timeline)
│       ├── SubmitModal.vue    # Demo submit: file upload per doc type, identity-scoped IDs, template hints
│       ├── Notifications.vue  # Toast stack fed by SSE case_updated events
│       └── UserSwitcher.vue   # Avatar + dropdown to switch demo identity

mockups/
├── dms/app.py              # FastAPI DMS mockup (port 8001) — disk-backed document store
│                           #   GET /documents/{id}, GET /documents/{id}/raw
│                           #   POST /documents (base64 JSON body), GET /documents, GET /health
├── edw/app.py              # FastAPI EDW mockup (port 8002) — idempotent on application_id
│                           #   POST /export, GET /exports/{application_id}, GET /health
└── crm/
    ├── app.py              # FastAPI CRM mockup (port 8003) — POST /publish → Kafka producer
    ├── publisher.py        # CLI: publishes Kafka events to credit-applications topic
    └── sample_data.py      # SAMPLE_EVENTS: two Personal Finance cases
```

## Architecture

### Processing flow

```
Siebel CRM → Kafka → ApplicationProcessor
                           │
                           ├─ _recover_stuck_cases()  [on startup only]
                           │
                           ↓ (per message)
                      InboundApplicationEvent (dedup on event_id)
                           ↓
                      ApplicationCase.status = CREATED → IN_PROGRESS
                           │
                           ├─ (per document_id, concurrent via asyncio.gather)
                           │   DMSClient.fetch_document()
                           │        ↓
                           │   AIClient.verify_and_extract()      → StageOutputVersion (append-only)
                           │        ↓ TYPE_MISMATCH → DocumentResult(verification_passed=False)
                           │        ↓ AI technical error → raise  → _fail_case → FAILED
                           │
                           ↓
                      completeness check (required document types present?)
                           ↓ missing → MANUAL_INTERVENTION_REQUIRED + dead_letter_event
                           ↓
                      ProductHandler.validate()                    → ValidationResultRow (append-only)
                           ↓
                      ApplicationCase.status = COMPLETED (recommendation set)
                           │
                           ├── ReportGenerator.generate()
                           │     ├── AIClient.generate_narrative()
                           │     ├── Jinja2 HTML → CaseReport.html_content
                           │     ├── WeasyPrint PDF
                           │     └── DMSClient.upload_document() → CaseReport.pdf_dms_document_id
                           │
                           └── EDWClient.export()                  → EDWStaging.status = EXPORTED
                                    ↓ (both ok)
                           ApplicationCase.completed_at = now()
```

### Key design constraints

- **Idempotent on `event_id`** — duplicate Kafka messages dropped at `InboundApplicationEvent` unique constraint.
- **Idempotent on `application_id`** — `ApplicationCase` unique constraint; re-consumed events find the existing case.
- **Append-only audit** — `StageOutputVersion` and `ValidationResultRow` are never updated, only inserted.
- **EDW write is last** — `edw_staging` row is written first; if export fails, the row stays `EXPORT_FAILED` and retries without re-running the pipeline.
- **Delivery is independent of business processing** — `ApplicationCase.status` becomes `COMPLETED` after `handler.validate()` succeeds. Report upload and EDW export failures are tracked on `case_report.error_detail` and `edw_staging.export_error` respectively; `completed_at` is only set when both deliver successfully.
- **AI technical error → FAILED, TYPE_MISMATCH → DECLINE** — if `verify_and_extract` raises (timeout, context exceeded, 500…) the exception propagates and the case lands on `FAILED`. If the AI runs successfully but detects the wrong document type, a `DocumentResult(verification_passed=False)` is returned; the handler emits a `HARD_BREACH` → `DECLINE`. These are intentionally different outcomes.
- **Startup recovery** — `_recover_stuck_cases()` runs before the Kafka consumer starts. Any `CREATED` or `IN_PROGRESS` case found at startup is marked `FAILED` with a clear error message (the processor was killed mid-flight). Ops must resubmit those applications.
- **Product handler strategy pattern** — adding a new product = new handler class + registry entry only.
- **Audit partitioning** — `audit_event` is a RANGE-partitioned table on `occurred_at`; composite PK is `(id, occurred_at)`. Monthly partitions are pre-created.
- **Read replica routing** — workbench API uses `AsyncReadSessionLocal` bound to `postgres-replica:5432` (set via `DATABASE_READ_URL` in docker-compose.yml). The replica streams WAL from the primary in real time. `session.py` falls back to the primary URL if `DATABASE_READ_URL` is blank, so local dev outside Docker still works.
- **Role-based row scoping** — workbench enforces: VALIDATOR sees only `validator_id == user_id`; SUPERVISOR sees only `supervisor_id == user_id`. Identity from `X-User-Id` / `X-User-Role` headers (swap-out point for OAuth2/SAML).

### Adding a new product type

1. Add value to `ProductType` in [domain/enums.py](src/creditunder/domain/enums.py)
2. Create `handlers/your_product.py` extending `BaseProductHandler`
3. Register in [handlers/registry.py](src/creditunder/handlers/registry.py)

### Adding a new document type

1. Add value to `DocumentType` in [domain/enums.py](src/creditunder/domain/enums.py)
2. Create `documents/your_document.py` extending `BaseExtractionSchema`
3. Register in [documents/\_\_init\_\_.py](src/creditunder/documents/__init__.py) `EXTRACTION_SCHEMA_REGISTRY`

## AI Client (`services/ai_client.py`)

Uses `pydantic-ai` with an OpenAI-compatible backend. Configured via `.env`:
- `AI_BASE_URL` + `AI_API_KEY` — any OpenAI-compatible endpoint
- `OPENAI_API_KEY` — use OpenAI directly (overrides the above)
- `AI_MODEL` — model name (e.g. `gpt-4o`)
- `AI_CONFIDENCE_THRESHOLD` — default 0.7; fields below this emit `LOW_CONFIDENCE` validation results

Two operations on `AIClient`:

| Method | Purpose |
|--------|---------|
| `verify_and_extract(document_content, content_type, document_name, expected_type)` | Single LLM call: confirms the document matches `expected_type` AND extracts all fields. Returns `VerifyAndExtractResult` with `is_correct_type`, `verification_confidence`, `detected_type`, and `extracted_fields`. |
| `generate_narrative(case_result, applicant_data, branch_name, validator_id)` | Writes the prose narrative section of the PDF report. |

All calls use `pydantic-ai` structured output (schema per `DocumentType`). `generate_narrative` uses `temperature=0.3`; everything else `temperature=0`.

## Services

### Exposed to the host (configurable via `.env`)

| Service | Default port | Notes |
|---------|-------------|-------|
| DMS | 8001 | Disk-backed (`/data`). Pre-seeded: `DMS-00192` (ID doc), `DMS-00193` (salary cert). Accepts base64 JSON on `POST /documents`. |
| EDW | 8002 | Idempotent on `application_id`. `POST /export`, `GET /exports/{application_id}`. |
| CRM | 8003 | `POST /publish` → Kafka producer. Used by the workbench demo submit flow and the publisher CLI. |
| Workbench API | 8004 | FastAPI. `GET /api/v1/cases`, `/api/v1/cases/{id}`, `/api/v1/cases/{id}/report`, `/api/_demo/submit`, `/api/_demo/dms/upload`, `/api/_demo/notifications` (SSE). |
| Workbench UI | 8081 | Vue 3 SPA served by nginx. Proxies `/api` to the workbench API container. |

Host-side ports are overridable: `DMS_PORT`, `EDW_PORT`, `CRM_PORT`, `WORKBENCH_API_PORT`, `WORKBENCH_UI_PORT`.

### Internal-only (Docker network only, never exposed)

| Service | Internal address | Notes |
|---------|----------------|-------|
| PostgreSQL primary | `postgres:5432` | Write path — pipeline processor only. Access via `docker compose exec postgres psql -U creditunder`. |
| PostgreSQL replica | `postgres-replica:5432` | Read path — workbench API (`DATABASE_READ_URL`). Streams WAL from primary. Access via `docker compose exec postgres-replica psql -U creditunder`. |
| Kafka | `kafka:9092` | KRaft mode, no ZooKeeper. Publish events via `docker compose exec crm python -m mockups.crm.publisher` or the workbench UI. |

## Sample Data

[mockups/crm/sample_data.py](mockups/crm/sample_data.py) contains two Personal Finance events:
- `APP-2026-089341` — happy path; declared salary matches certificate → `APPROVE`
- `APP-2026-089342` — salary mismatch; declared 25,000 vs certificate 18,500 (>10% deviation) → `SALARY_DEVIATION` SOFT_MISMATCH → `HOLD`

Pre-seeded DMS documents for these cases: `DMS-00192` (national ID, Mohammed Al-Harbi), `DMS-00193` (salary certificate, Saudi Aramco). The workbench submit modal uses these IDs in its "Use DMS IDs instead" testing mode.

## Environment

Copy `.env.example` to `.env`.

**`.env` only contains:**
- Host port mappings for the five exposed services (`DMS_PORT`, `EDW_PORT`, `CRM_PORT`, `WORKBENCH_API_PORT`, `WORKBENCH_UI_PORT`)
- Kafka app-level config (`KAFKA_TOPIC`, `KAFKA_CONSUMER_GROUP`) — not a connection address
- AI service credentials (`OPENAI_API_KEY` or `AI_BASE_URL` + `AI_API_KEY`, `AI_MODEL`, `AI_CONFIDENCE_THRESHOLD`)
- Mockup service base URLs for local-dev access (`DMS_BASE_URL`, `EDW_BASE_URL`, `CRM_BASE_URL`)

**Not in `.env`** (internal-only, set directly in docker-compose.yml `environment:` blocks):
- `DATABASE_URL` — `postgres:5432` (internal)
- `DATABASE_READ_URL` — `postgres-replica:5432` (internal)
- `KAFKA_BOOTSTRAP_SERVERS` — `kafka:9092` (internal)

**AI key (pick one):**
- Set `OPENAI_API_KEY=sk-...` to use OpenAI directly.
- OR set `AI_BASE_URL` + `AI_API_KEY` for any OpenAI-compatible endpoint.
