# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Credit Underwriting Platform** for Alinma Bank. Automates credit application processing: consumes Kafka events from Siebel CRM, fetches documents from DMS, verifies and extracts structured data via AI Service, applies product business rules, and generates a PDF report delivered back to DMS.

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
├── config.py               # pydantic-settings: all env vars (DATABASE_URL, AI_*, DMS_*, CRM_*)
├── validation_config.yaml  # Versioned thresholds + recommendation mapping consumed by handlers.
│                           #   Bump `version` after any change. Stamped onto every validation_result
│                           #   row (`config_version`) so the audit trail is reproducible.
├── validation_config.py    # Loader (pydantic) — get_validation_config() returns a cached instance.
├── observability.py        # structlog configuration
├── otel_observability.py   # OpenTelemetry scaffolding — Telemetry singleton (not wired to pipeline yet)
├── __main__.py             # entrypoint → ApplicationProcessor.run()
├── Dockerfile              # Dockerfile for the processor service
├── entrypoint.sh           # Docker entrypoint (waits for infra, runs migrations, starts processor)
├── domain/
│   ├── enums.py            # ProductType, DocumentType, ValidationOutcome, Recommendation,
│   │                       #   CaseStatus, DocumentStatus, CaseReportStatus,
│   │                       #   InboundEventStatus, JobType, JobStatus
│   └── models.py           # ExtractedField[T], DocumentResult, CaseResult, ApplicationEvent,
│                           #   EmployerSnapshot (employer_class
│                           #   A/B/C/D/GOV + rule_version), RequiredDocumentSet
├── documents/
│   ├── __init__.py         # EXTRACTION_SCHEMA_REGISTRY (DocumentType → schema class)
│   ├── base.py             # BaseExtractionSchema
│   ├── id_document.py      # IDDocumentExtraction (pydantic-ai output schema)
│   └── salary_certificate.py  # SalaryCertificateExtraction (pydantic-ai output schema)
├── handlers/
│   ├── base.py             # BaseProductHandler (ABC). required_documents(applicant_data) returns
│   │                       #   RequiredDocumentSet so the set can branch on employer_class.
│   │                       #   _stamp_versions() writes config_version + employer_rule_version
│   │                       #   onto every ValidationResult.
│   ├── registry.py         # get_handler(ProductType) → handler instance
│   └── personal_finance.py # PersonalFinanceHandler — rules: ID_NUMBER_MISMATCH, ID_NAME_CHECK,
│                           #   ID_EXPIRED, SALARY_EMPLOYER_MATCH, SALARY_DEVIATION (tolerance from
│                           #   validation_config.yaml). Encodes BR-13 employer-class doc matrix.
├── services/
│   ├── ai_client.py        # AIClient — verify_and_extract(), generate_narrative()
│   └── dms_client.py       # DMSClient — fetch_document(), upload_document()
├── db/
│   ├── models.py           # SQLAlchemy ORM (all tables). Key tables: ApplicationCase,
│   │                       #   CaseDocument, StageOutputVersion, ValidationResultRow,
│   │                       #   CaseReport, EdwStaging (workbench source of truth),
│   │                       #   AuditEvent (partitioned), ProcessingJob, DeadLetterEvent.
│   └── session.py          # AsyncSessionLocal (write), AsyncReadSessionLocal (read replica),
│                           #   get_read_session() FastAPI dependency
└── pipeline/
    ├── processor.py        # ApplicationProcessor — Kafka consumer + full pipeline orchestration
    ├── report_generator.py # ReportGenerator — AI narrative + Jinja2 HTML + WeasyPrint PDF
    │                       #   Fetches each source document from DMS and embeds as base64 data URI
    │                       #   so the PDF is fully self-contained (no external refs at render time).
    └── templates/
        └── report.html     # Jinja2 report template (WeasyPrint-optimised: 4-col validation table,
                            #   CSS-table two-column doc layout, page-break guards on every row/card)

src/workbench/              # Reviewer Workbench API — separate package, read-only, read-replica-backed
├── Dockerfile              # Reuses creditunder's pyproject.toml + uv.lock; PYTHONPATH=/app/src
├── main.py                 # FastAPI app, CORS middleware, mounts both routers, GET /health
├── config.py               # Settings: database_url, database_read_url, dms_base_url, crm_base_url,
│                           #   cors_origins. effective_read_url falls back to primary if blank.
├── db.py                   # AsyncReadSessionLocal bound to effective_read_url; get_read_session()
├── auth.py                 # UserRole enum, UserContext model, auth_user() dependency
│                           #   (X-User-Id / X-User-Role headers),
│                           #   scope_query() applies per-role WHERE filter on EdwStaging —
│                           #   fail-closed: unknown role returns no rows.
├── schemas.py              # CaseListItem, ExtractedFieldOut, DocumentDetail, ValidationDetail,
│                           #   ValidationGroups, ReportSummary, AuditEntryOut, ManualCheckItem,
│                           #   TechnicalException, CaseDetail, CaseStatusCounts
└── routers/
    ├── cases.py            # GET /api/v1/me, /cases/counts, /cases, /cases/{id},
    │                       #   /cases/{id}/documents/{dms_id}/preview (DMS proxy, auth-gated),
    │                       #   /cases/{id}/report (PDF proxy from DMS).
    │                       #   All queries target edw_staging exclusively — no pipeline tables.
    └── demo.py             # POST /api/_demo/dms/upload, /api/_demo/submit,
                            #   POST /api/_demo/notifications/token (mint 60 s single-use token),
                            #   GET /api/_demo/notifications?token=… (SSE — case_updated events).
                            #   Token pattern avoids embedding identity in URLs/logs.

src/workbench-ui/           # Vue 3 + Vite (TypeScript) — Reviewer Workbench SPA
├── src/
│   ├── App.vue             # Shell: sticky app bar, SSE reconnect on identity change
│   ├── api.ts              # apiGet/apiPost/apiBlob/eventSource — injects X-User-Id/X-User-Role headers.
│                      #   eventSource() is async: mints a token via POST then opens EventSource with ?token=…
│   ├── userStore.ts        # Reactive identity store — 4 demo users, localStorage persistence
│   ├── style.css           # Design system: CSS custom properties, panel, kpi-grid, chip, timeline, toast
│   └── components/
│       ├── Dashboard.vue      # KPI cards, filter bar, cases table (search, status, rec, product filters)
│       ├── CaseDetails.vue    # Slide-in drawer: hero card, 4 tabs (Overview/Documents/Validations/Timeline)
│       │                      #   Documents tab: side-by-side document preview (image/PDF) + extracted fields
│       ├── SubmitModal.vue    # Demo submit: file upload per doc type, identity-scoped IDs, template hints
│       ├── Notifications.vue  # Toast stack fed by SSE case_updated events
│       └── UserSwitcher.vue   # Avatar + dropdown to switch demo identity

mockups/
├── dms/app.py              # FastAPI DMS mockup (port 8001) — disk-backed document store
│                           #   GET /documents/{id}, GET /documents/{id}/raw
│                           #   POST /documents (base64 JSON body), GET /documents, GET /health
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
                           └── ReportGenerator.generate()
                                 ├── AIClient.generate_narrative()
                                 ├── DMSClient.fetch_document() × N  (embed docs as base64 data URIs)
                                 ├── Jinja2 HTML → CaseReport.html_content
                                 ├── WeasyPrint PDF
                                 └── DMSClient.upload_document() → CaseReport.pdf_dms_document_id
                                          ↓ (ok)
                                 ApplicationCase.completed_at = now()
                                          ↓
                                 _write_edw_staging() → EdwStaging row
                                   (queries all committed rows, builds full
                                    CaseDetail-shaped payload, inserts once)
```

### Key design constraints

- **Idempotent on `event_id`** — duplicate Kafka messages dropped at `InboundApplicationEvent` unique constraint.
- **Idempotent on `application_id`** — `ApplicationCase` unique constraint; re-consumed events find the existing case.
- **`applicant_data.employer_snapshot` is the employer-rules source** — CRM resolves the governed rules source and embeds the snapshot (class, restrictions, max-limit, `rule_version`, `rule_source_date`) at publish time. Handlers read from there; this system never fetches employer data directly. `rule_version` is stamped onto every `validation_result` row that consumed employer data so the BR-30 PDF can show *which* rules version produced any outcome.
- **Versioned thresholds in `validation_config.yaml`** — confidence cutoffs, salary-deviation tolerance, and the outcome→recommendation mapping live in YAML; logic stays in code. Every `validation_result` row carries `config_version` from the YAML so changes are audit-traceable. Restart the processor to pick up edits.
- **Append-only audit** — `StageOutputVersion` and `ValidationResultRow` are never updated, only inserted.
- **Delivery is independent of business processing** — `ApplicationCase.status` becomes `COMPLETED` after `handler.validate()` succeeds. Report upload failure is tracked on `case_report.error_detail`; `completed_at` is only set after the PDF is successfully delivered to DMS.
- **AI technical error → FAILED, TYPE_MISMATCH → DECLINE** — if `verify_and_extract` raises (timeout, context exceeded, 500…) the exception propagates and the case lands on `FAILED`. If the AI runs successfully but detects the wrong document type, a `DocumentResult(verification_passed=False)` is returned; the handler emits a `HARD_BREACH` → `DECLINE`. These are intentionally different outcomes.
- **Startup recovery** — `_recover_stuck_cases()` runs before the Kafka consumer starts. Any `CREATED` or `IN_PROGRESS` case found at startup is marked `FAILED` with a clear error message (the processor was killed mid-flight). Ops must resubmit those applications.
- **Product handler strategy pattern** — adding a new product = new handler class + registry entry only.
- **Audit partitioning** — `audit_event` is a RANGE-partitioned table on `occurred_at`; composite PK is `(id, occurred_at)`. Monthly partitions are pre-created.
- **`edw_staging` is the workbench's single source of truth** — after `completed_at` is committed, `_write_edw_staging()` snapshots every pipeline row (case, documents + extractions, validations, report metadata, audit timeline) into a single `EdwStaging` row with a `payload` JSONB. The workbench reads **only** from `edw_staging`; it never touches `application_case`, `case_document`, `validation_result`, or any other pipeline table. Promoted columns (`validator_id`, `supervisor_id`, `status`, `recommendation`, `product_type`, `applicant_name`, `pdf_dms_document_id`) support indexed filtering and role scoping without JSONB access. A staging-write failure is logged but not propagated — the case stays `COMPLETED`; the `edw_staging` row must be written manually or by resubmission.
- **Read replica routing** — workbench API uses `AsyncReadSessionLocal` bound to `postgres-replica:5432` (set via `DATABASE_READ_URL` in docker-compose.yml). The replica streams WAL from the primary in real time. `session.py` falls back to the primary URL if `DATABASE_READ_URL` is blank, so local dev outside Docker still works.
- **Role-based row scoping** — workbench enforces: VALIDATOR sees only `validator_id == user_id`; SUPERVISOR sees only `supervisor_id == user_id` — both filtered on `edw_staging` columns. `scope_query()` fails closed (returns no rows) for any unrecognised role. Identity from `X-User-Id` / `X-User-Role` headers. `validator_id` is the **reviewer** of the case, not the original CRM submitter.
- **Workbench is read-only** — case actions (approve / reject / override) happen in Siebel CRM, not in the Workbench. The BRD's BR-34 in-system override capture is owned by CRM in this architecture; this system only persists results and exposes them.
- **Tech-exception vs business-outcome split** — anything in `*.error_detail` is a *technical exception*. The Workbench surfaces these in a dedicated `technical_exceptions` projection and the PDF renders them in a "Technical Exceptions" panel with a neutral-grey, dashed-border palette so they are never confused with a `ValidationOutcome` business decline (BR-37).
- **Manual checks panel** — aggregates rule rows with `manual_review_required = true` (or outcome `MANUAL_REVIEW_REQUIRED`) plus a stamp/signature visual-review entry per source document (BR-33). Surfaced in both the Workbench detail view and the PDF.

### Adding a new product type

1. Add value to `ProductType` in [domain/enums.py](src/creditunder/domain/enums.py)
2. Create `handlers/your_product.py` extending `BaseProductHandler`. Implement `required_documents(applicant_data) -> RequiredDocumentSet` (branch on `applicant_data["employer_snapshot"]["employer_class"]` if relevant) and `validate(...)`. Wrap every `ValidationResult` with `self._stamp_versions(...)` so `config_version` (and `employer_rule_version` when the rule consumed employer data) is recorded.
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

The **per-document confidence threshold** applied to LOW_CONFIDENCE rows lives in [src/creditunder/validation_config.yaml](src/creditunder/validation_config.yaml) (`confidence_thresholds.<DOCUMENT_TYPE>` with a required `default` fallback). Tune it there without a code deployment; `confidence_thresholds.default` must always be present or the processor will refuse to start.

Two operations on `AIClient`:

| Method | Purpose |
|--------|---------|
| `verify_and_extract(document_content, content_type, document_name, expected_type)` | Single LLM call: confirms the document matches `expected_type` AND extracts all fields. Returns `VerifyAndExtractResult` with `is_correct_type`, `verification_confidence`, `detected_type`, and `extracted_fields`. |
| `generate_narrative(case_summary)` | Writes one concise paragraph for the PDF report: opens with APPROVE/HOLD/DECLINE, cites the key finding(s), flags mandatory manual checks. |

All calls use `pydantic-ai` structured output (schema per `DocumentType`). `generate_narrative` uses `temperature=0.3`; everything else `temperature=0`.

## Services

### Exposed to the host (configurable via `.env`)

| Service | Default port | Notes |
|---------|-------------|-------|
| DMS | 8001 | Disk-backed (`/data`). Pre-seeded: `DMS-00192` (ID doc), `DMS-00193` (salary cert). Accepts base64 JSON on `POST /documents`. |
| CRM | 8003 | `POST /publish` → Kafka producer. Used by the workbench demo submit flow and the publisher CLI. |
| Workbench API | 8004 | FastAPI (`src/workbench/`). `GET /api/v1/cases`, `/api/v1/cases/{id}`, `/api/v1/cases/{id}/documents/{dms_id}/preview`, `/api/v1/cases/{id}/report`, `/api/_demo/submit`, `/api/_demo/dms/upload`, `/api/_demo/notifications` (SSE). |
| Workbench UI | 8081 | Vue 3 SPA served by nginx. Proxies `/api` to the workbench API container. |

Host-side ports are overridable: `DMS_PORT`, `CRM_PORT`, `WORKBENCH_API_PORT`, `WORKBENCH_UI_PORT`.

### Internal-only (Docker network only, never exposed)

| Service | Internal address | Notes |
|---------|----------------|-------|
| PostgreSQL primary | `postgres:5432` | Write path — pipeline processor only. Access via `docker compose exec postgres psql -U creditunder`. |
| PostgreSQL replica | `postgres-replica:5432` | Read path — workbench API (`DATABASE_READ_URL`). Streams WAL from primary. Access via `docker compose exec postgres-replica psql -U creditunder`. |
| Kafka | `kafka:9092` | KRaft mode, no ZooKeeper. Publish events via `docker compose exec crm python -m mockups.crm.publisher` or the workbench UI. |

## Sample Data

[mockups/crm/sample_data.py](mockups/crm/sample_data.py) contains two Personal Finance events. Both carry an `applicant_data.employer_snapshot` block (Class A, Saudi Aramco, rule_version `2026.04`):
- `APP-2026-089341` — happy path; declared salary matches certificate → `APPROVE`
- `APP-2026-089342` — salary mismatch; declared 25,000 vs certificate 18,500 (deviation > the tolerance in `validation_config.yaml`) → `SALARY_DEVIATION` SOFT_MISMATCH → `HOLD`

Pre-seeded DMS documents for these cases: `DMS-00192` (national ID, Mohammed Al-Harbi), `DMS-00193` (salary certificate, Saudi Aramco). The workbench submit modal uses these IDs in its "Use DMS IDs instead" testing mode.

## Environment

Copy `.env.example` to `.env`.

**`.env` only contains:**
- Host port mappings for the four exposed services (`DMS_PORT`, `CRM_PORT`, `WORKBENCH_API_PORT`, `WORKBENCH_UI_PORT`)
- Kafka app-level config (`KAFKA_TOPIC`, `KAFKA_CONSUMER_GROUP`) — not a connection address
- AI service credentials (`OPENAI_API_KEY` or `AI_BASE_URL` + `AI_API_KEY`, `AI_MODEL`)
- Mockup service base URLs for local-dev access (`DMS_BASE_URL`, `CRM_BASE_URL`)

**Not in `.env`** (internal-only, set directly in docker-compose.yml `environment:` blocks):
- `DATABASE_URL` — `postgres:5432` (internal)
- `DATABASE_READ_URL` — `postgres-replica:5432` (internal)
- `KAFKA_BOOTSTRAP_SERVERS` — `kafka:9092` (internal)

**AI key (pick one):**
- Set `OPENAI_API_KEY=sk-...` to use OpenAI directly.
- OR set `AI_BASE_URL` + `AI_API_KEY` for any OpenAI-compatible endpoint.
