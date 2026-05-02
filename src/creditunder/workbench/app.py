"""Reviewer Workbench API.

Implements the design specified in Section 12 of the system requirements:

- Spec-aligned endpoints under `/api/v1` are READ-ONLY. They run against the
  read replica when one is configured (`DATABASE_READ_URL`), otherwise the
  primary database.
- Every request is scoped to the authenticated caller's identity. A Validator
  sees only `application_case.validator_id == user_id`; a Supervisor sees
  every case where `application_case.supervisor_id == user_id`. Until the
  bank's identity provider is integrated, identity is read from request
  headers (`X-User-Id`, `X-User-Role`) — the auth dependency is the single
  swap-out point for OAuth2 / SAML.

A small `/api/_demo` namespace is preserved outside the spec for demo flows
(submission and SSE notifications). It is clearly marked as non-spec.
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from creditunder.config import settings
from creditunder.db.models import (
    ApplicationCase,
    AuditEvent,
    CaseDocument,
    CaseReport,
    CaseResultRow,
    EDWStaging,
    StageOutputVersion,
    ValidationResultRow,
)
from creditunder.db.session import AsyncReadSessionLocal, get_read_session

log = structlog.get_logger(__name__)


# --------------------------------------------------------------------- #
#  Auth — header-based stub                                               #
# --------------------------------------------------------------------- #


class UserRole(str, Enum):
    VALIDATOR = "VALIDATOR"
    SUPERVISOR = "SUPERVISOR"


class UserContext(BaseModel):
    """Identity resolved from the request. Replace `auth_user` with the real
    OAuth2 / SAML resolver when the bank's IDP is wired in."""
    user_id: str
    role: UserRole


async def auth_user(
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
) -> UserContext:
    if not x_user_id or not x_user_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-User-Id or X-User-Role header.",
        )
    try:
        role = UserRole(x_user_role.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unknown role: {x_user_role}. Expected VALIDATOR or SUPERVISOR.",
        )
    return UserContext(user_id=x_user_id, role=role)


def _scope_query(stmt, user: UserContext):
    """Apply role-based row scoping to any ApplicationCase select."""
    if user.role == UserRole.VALIDATOR:
        return stmt.where(ApplicationCase.validator_id == user.user_id)
    return stmt.where(ApplicationCase.supervisor_id == user.user_id)


# --------------------------------------------------------------------- #
#  Response models                                                        #
# --------------------------------------------------------------------- #


class CaseListItem(BaseModel):
    id: UUID
    application_id: str
    applicant_name: str | None
    product_type: str
    branch_name: str
    validator_id: str
    status: str
    recommendation: str | None
    manual_review_required: bool
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ExtractedFieldOut(BaseModel):
    label: str
    value: Any
    confidence: float | None
    page_reference: int | None


class DocumentDetail(BaseModel):
    id: UUID
    dms_document_id: str
    document_name: str | None
    document_type: str | None
    status: str
    verification_passed: bool | None
    verification_confidence: float | None
    fetched_at: datetime | None
    verified_at: datetime | None
    error_detail: str | None
    extracted_fields: list[ExtractedFieldOut]


class ValidationDetail(BaseModel):
    id: UUID
    rule_code: str
    outcome: str
    description: str
    field_name: str | None
    extracted_value: str | None
    expected_value: str | None
    confidence: float | None
    manual_review_required: bool
    evaluated_at: datetime


class ValidationGroups(BaseModel):
    """Validation rows grouped by outcome — the order matters for the UI."""
    hard_breach: list[ValidationDetail] = Field(default_factory=list)
    soft_mismatch: list[ValidationDetail] = Field(default_factory=list)
    low_confidence: list[ValidationDetail] = Field(default_factory=list)
    manual_review: list[ValidationDetail] = Field(default_factory=list)


class ReportSummary(BaseModel):
    status: str | None
    pdf_available: bool
    pdf_uploaded_at: datetime | None
    error_detail: str | None


class EDWSummary(BaseModel):
    status: str | None
    edw_confirmation_id: str | None
    exported_at: datetime | None
    export_error: str | None


class AuditEntryOut(BaseModel):
    id: UUID
    event_type: str
    actor: str | None
    detail: dict | None
    occurred_at: datetime


class CaseDetail(BaseModel):
    case: dict
    documents: list[DocumentDetail]
    validations: ValidationGroups
    report: ReportSummary
    edw: EDWSummary
    audit_timeline: list[AuditEntryOut]


class CaseStatusCounts(BaseModel):
    """Header KPI for the dashboard."""
    total: int
    in_progress: int
    completed: int
    failed: int
    manual_intervention_required: int
    approve: int
    hold: int
    decline: int


# --------------------------------------------------------------------- #
#  Helpers                                                                #
# --------------------------------------------------------------------- #


def _applicant_name(case: ApplicationCase) -> str | None:
    data = case.applicant_data or {}
    return data.get("name") if isinstance(data, dict) else None


def _latest_extraction(versions: list[StageOutputVersion]) -> dict | None:
    if not versions:
        return None
    latest = max(versions, key=lambda v: v.version)
    return latest.raw_extraction


def _to_extracted_fields(raw: dict | None) -> list[ExtractedFieldOut]:
    """Convert the JSONB blob `{field_key: {value, confidence, page_reference, normalized_label}}`
    into the API output list."""
    if not raw:
        return []
    out: list[ExtractedFieldOut] = []
    for key, ef in raw.items():
        if not isinstance(ef, dict):
            continue
        out.append(
            ExtractedFieldOut(
                label=ef.get("normalized_label") or key,
                value=ef.get("value"),
                confidence=ef.get("confidence"),
                page_reference=ef.get("page_reference"),
            )
        )
    return out


def _validation_to_detail(v: ValidationResultRow) -> ValidationDetail:
    return ValidationDetail(
        id=v.id,
        rule_code=v.rule_code,
        outcome=v.outcome,
        description=v.description,
        field_name=v.field_name,
        extracted_value=v.extracted_value,
        expected_value=v.expected_value,
        confidence=v.confidence,
        manual_review_required=v.manual_review_required,
        evaluated_at=v.evaluated_at,
    )


def _group_validations(rows: list[ValidationResultRow]) -> ValidationGroups:
    groups = ValidationGroups()
    for v in rows:
        d = _validation_to_detail(v)
        match v.outcome:
            case "HARD_BREACH":
                groups.hard_breach.append(d)
            case "SOFT_MISMATCH":
                groups.soft_mismatch.append(d)
            case "LOW_CONFIDENCE":
                groups.low_confidence.append(d)
            case "MANUAL_REVIEW_REQUIRED":
                groups.manual_review.append(d)
    return groups


# --------------------------------------------------------------------- #
#  App + middleware                                                       #
# --------------------------------------------------------------------- #


app = FastAPI(
    title="Reviewer Workbench API",
    version="1.0.0",
    description=(
        "Read-only API serving the Reviewer Workbench. Section 12 of the "
        "system design spec is the source of truth for endpoint semantics."
    ),
)

_origins = [o.strip() for o in (settings.cors_origins or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False if _origins == ["*"] else True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------- #
#  Spec-aligned router                                                    #
#    /api/v1                                                              #
# --------------------------------------------------------------------- #


api_v1 = APIRouter(prefix="/api/v1", tags=["v1"])


@api_v1.get("/me", response_model=UserContext)
async def me(user: UserContext = Depends(auth_user)) -> UserContext:
    """Echo the resolved identity. Used by the UI to render the role banner."""
    return user


@api_v1.get("/cases/counts", response_model=CaseStatusCounts)
async def case_counts(
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> CaseStatusCounts:
    base = _scope_query(select(ApplicationCase.status, ApplicationCase.recommendation), user)
    rows = (await db.execute(base)).all()

    counts = CaseStatusCounts(
        total=len(rows),
        in_progress=0,
        completed=0,
        failed=0,
        manual_intervention_required=0,
        approve=0,
        hold=0,
        decline=0,
    )
    for r in rows:
        s = r.status
        if s == "IN_PROGRESS" or s == "CREATED":
            counts.in_progress += 1
        elif s == "COMPLETED":
            counts.completed += 1
        elif s == "FAILED":
            counts.failed += 1
        elif s == "MANUAL_INTERVENTION_REQUIRED":
            counts.manual_intervention_required += 1

        rec = r.recommendation
        if rec == "APPROVE":
            counts.approve += 1
        elif rec == "HOLD":
            counts.hold += 1
        elif rec == "DECLINE":
            counts.decline += 1
    return counts


@api_v1.get("/cases", response_model=list[CaseListItem])
async def list_cases(
    case_status: str | None = Query(default=None, alias="status"),
    recommendation: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    search: str | None = Query(
        default=None,
        description="Substring match on application_id or applicant.name",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> list[CaseListItem]:
    """List cases visible to the caller, server-side filtered.

    `MANUAL_INTERVENTION_REQUIRED` cases are pinned to the top of the
    response so the UI can surface them without client-side re-sorting.
    """
    stmt = select(ApplicationCase).order_by(
        # MANUAL_INTERVENTION_REQUIRED first, then most recent first.
        desc(ApplicationCase.status == "MANUAL_INTERVENTION_REQUIRED"),
        desc(ApplicationCase.created_at),
    )
    stmt = _scope_query(stmt, user)
    if case_status:
        stmt = stmt.where(ApplicationCase.status == case_status)
    if recommendation:
        stmt = stmt.where(ApplicationCase.recommendation == recommendation)
    if product_type:
        stmt = stmt.where(ApplicationCase.product_type == product_type)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            ApplicationCase.application_id.ilike(like)
            | ApplicationCase.applicant_data["name"].astext.ilike(like)
        )
    stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        CaseListItem(
            id=c.id,
            application_id=c.application_id,
            applicant_name=_applicant_name(c),
            product_type=c.product_type,
            branch_name=c.branch_name,
            validator_id=c.validator_id,
            status=c.status,
            recommendation=c.recommendation,
            manual_review_required=c.manual_review_required,
            error_detail=c.error_detail,
            created_at=c.created_at,
            updated_at=c.updated_at,
            completed_at=c.completed_at,
        )
        for c in rows
    ]


@api_v1.get("/cases/{application_id}", response_model=CaseDetail)
async def get_case(
    application_id: str,
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> CaseDetail:
    """Full case detail in a single round trip.

    Eagerly loads documents (with their extraction versions), report, and
    EDW staging via `selectinload`. Validation rows and audit events are
    fetched in two extra `IN`-style queries to keep the row counts bounded.
    """
    stmt = (
        _scope_query(select(ApplicationCase), user)
        .where(ApplicationCase.application_id == application_id)
        .options(
            selectinload(ApplicationCase.documents).selectinload(
                CaseDocument.extraction_versions
            ),
            selectinload(ApplicationCase.report),
            selectinload(ApplicationCase.edw_staging),
        )
    )
    case = (await db.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found or not accessible.")

    # Validations.
    val_rows = (
        await db.execute(
            select(ValidationResultRow).where(ValidationResultRow.case_id == case.id)
        )
    ).scalars().all()

    # Audit timeline — most recent 200 events.
    audit_rows = (
        await db.execute(
            select(AuditEvent)
            .where(AuditEvent.case_id == case.id)
            .order_by(AuditEvent.occurred_at.asc())
            .limit(200)
        )
    ).scalars().all()

    documents = [
        DocumentDetail(
            id=d.id,
            dms_document_id=d.dms_document_id,
            document_name=d.document_name,
            document_type=d.document_type,
            status=d.status,
            verification_passed=d.verification_passed,
            verification_confidence=d.verification_confidence,
            fetched_at=d.fetched_at,
            verified_at=d.verified_at,
            error_detail=d.error_detail,
            extracted_fields=_to_extracted_fields(_latest_extraction(d.extraction_versions)),
        )
        for d in case.documents
    ]

    report = case.report
    report_summary = ReportSummary(
        status=report.status if report else None,
        pdf_available=bool(report and report.pdf_dms_document_id),
        pdf_uploaded_at=report.pdf_uploaded_at if report else None,
        error_detail=report.error_detail if report else None,
    )

    staging = case.edw_staging
    edw_summary = EDWSummary(
        status=staging.status if staging else None,
        edw_confirmation_id=staging.edw_confirmation_id if staging else None,
        exported_at=staging.exported_at if staging else None,
        export_error=staging.export_error if staging else None,
    )

    return CaseDetail(
        case={
            "id": str(case.id),
            "application_id": case.application_id,
            "applicant_name": _applicant_name(case),
            "product_type": case.product_type,
            "branch_name": case.branch_name,
            "validator_id": case.validator_id,
            "supervisor_id": case.supervisor_id,
            "status": case.status,
            "recommendation": case.recommendation,
            "recommendation_rationale": case.recommendation_rationale,
            "manual_review_required": case.manual_review_required,
            "error_detail": case.error_detail,
            "applicant_data": case.applicant_data,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "updated_at": case.updated_at.isoformat() if case.updated_at else None,
            "completed_at": case.completed_at.isoformat() if case.completed_at else None,
        },
        documents=documents,
        validations=_group_validations(val_rows),
        report=report_summary,
        edw=edw_summary,
        audit_timeline=[
            AuditEntryOut(
                id=a.id,
                event_type=a.event_type,
                actor=a.actor,
                detail=a.detail,
                occurred_at=a.occurred_at,
            )
            for a in audit_rows
        ],
    )


@api_v1.get("/cases/{application_id}/report")
async def get_report(
    application_id: str,
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> Response:
    """Stream the case PDF report from DMS."""
    stmt = _scope_query(
        select(ApplicationCase).where(ApplicationCase.application_id == application_id),
        user,
    )
    case = (await db.execute(stmt)).scalar_one_or_none()
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found or not accessible.")

    report = (
        await db.execute(select(CaseReport).where(CaseReport.case_id == case.id))
    ).scalar_one_or_none()
    if report is None or not report.pdf_dms_document_id:
        raise HTTPException(status_code=404, detail="Report not yet available.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{settings.dms_base_url}/documents/{report.pdf_dms_document_id}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch report from DMS.")
    pdf_bytes = base64.b64decode(resp.json()["content_base64"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="report_{application_id}.pdf"'},
    )


app.include_router(api_v1)


# --------------------------------------------------------------------- #
#  Demo namespace — outside the spec                                      #
#    Kept so the bundled UI can still publish events without standing up  #
#    the CRM mockup CORS path. Production deployments must remove these.  #
# --------------------------------------------------------------------- #


demo = APIRouter(prefix="/api/_demo", tags=["demo"])


class DMSUploadRequest(BaseModel):
    document_name: str
    document_type: str
    content_base64: str
    content_type: str = "image/png"
    related_application_id: str | None = None


@demo.post("/dms/upload")
async def demo_dms_upload(req: DMSUploadRequest):
    """Proxy a file upload to the DMS mockup so the UI never needs to reach
    the DMS port directly (avoids CORS issues in dev and in production)."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.dms_base_url}/documents",
            json=req.model_dump(),
        )
    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"DMS upload failed: {resp.text}")
    return resp.json()


class SubmitApplicationRequest(BaseModel):
    product_type: str
    branch_name: str
    validator_id: str
    supervisor_id: str
    document_ids: list[str]
    applicant_data: dict


@demo.post("/submit")
async def demo_submit(req: SubmitApplicationRequest):
    """Forward a demo submission to the CRM mockup, which publishes to Kafka."""
    payload = req.model_dump()
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.crm_base_url}/publish", json=payload)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"CRM publish failed: {resp.text}")
    return resp.json()


async def _case_status_stream(request: Request, user: UserContext) -> AsyncGenerator[dict, None]:
    """Yield SSE events whenever a case in the caller's scope transitions."""
    last_checked = datetime.now(timezone.utc)
    while True:
        if await request.is_disconnected():
            break
        async with AsyncReadSessionLocal() as session:
            stmt = _scope_query(
                select(ApplicationCase).where(ApplicationCase.updated_at > last_checked),
                user,
            )
            updated = (await session.execute(stmt)).scalars().all()
        if updated:
            for case in updated:
                yield {
                    "event": "case_updated",
                    "data": json.dumps(
                        {
                            "application_id": case.application_id,
                            "status": case.status,
                            "recommendation": case.recommendation,
                            "manual_review_required": case.manual_review_required,
                            "error_detail": case.error_detail,
                        }
                    ),
                }
            last_checked = max(c.updated_at for c in updated)
        await asyncio.sleep(2)


@demo.get("/notifications")
async def notifications(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    x_user_role: str | None = Header(default=None, alias="X-User-Role"),
    _uid: str | None = Query(default=None),
    _role: str | None = Query(default=None),
):
    """SSE stream of case-status updates. Scoped to the caller's identity.

    Accepts identity via either headers (preferred) or `_uid` / `_role` query
    params, since the EventSource browser API cannot attach custom headers.
    Production must replace this with a session-cookie or signed-token auth.
    """
    user_id = x_user_id or _uid
    role_value = x_user_role or _role
    if not user_id or not role_value:
        raise HTTPException(status_code=401, detail="Missing identity.")
    try:
        role = UserRole(role_value.upper())
    except ValueError:
        raise HTTPException(status_code=401, detail=f"Unknown role: {role_value}")
    user = UserContext(user_id=user_id, role=role)
    return EventSourceResponse(_case_status_stream(request, user))


app.include_router(demo)


# --------------------------------------------------------------------- #
#  Health                                                                 #
# --------------------------------------------------------------------- #


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
