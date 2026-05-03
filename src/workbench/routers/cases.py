import base64

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from creditunder.db.models import EdwStaging
from workbench.auth import UserContext, auth_user, scope_query
from workbench.config import settings
from workbench.db import get_read_session
from workbench.schemas import (
    AuditEntryOut,
    CaseDetail,
    CaseListItem,
    CaseStatusCounts,
    DocumentDetail,
    ManualCheckItem,
    ReportSummary,
    TechnicalException,
    ValidationDetail,
    ValidationGroups,
)

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.get("/me", response_model=UserContext)
async def me(user: UserContext = Depends(auth_user)) -> UserContext:
    """Echo resolved identity. Used by the UI to render the role banner."""
    return user


@router.get("/cases/counts", response_model=CaseStatusCounts)
async def case_counts(
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> CaseStatusCounts:
    rows = (
        await db.execute(
            scope_query(select(EdwStaging.status, EdwStaging.recommendation), user)
        )
    ).all()

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
        match r.status:
            case "IN_PROGRESS" | "CREATED":
                counts.in_progress += 1
            case "COMPLETED":
                counts.completed += 1
            case "FAILED":
                counts.failed += 1
            case "MANUAL_INTERVENTION_REQUIRED":
                counts.manual_intervention_required += 1
        match r.recommendation:
            case "APPROVE":
                counts.approve += 1
            case "HOLD":
                counts.hold += 1
            case "DECLINE":
                counts.decline += 1
    return counts


@router.get("/cases", response_model=list[CaseListItem])
async def list_cases(
    case_status: str | None = Query(default=None, alias="status"),
    recommendation: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    search: str | None = Query(
        default=None,
        description="Substring match on application_id or applicant name",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> list[CaseListItem]:
    """List cases visible to the caller, server-side filtered.

    MANUAL_INTERVENTION_REQUIRED cases are pinned to the top so the UI can
    surface them without client-side re-sorting.
    """
    stmt = scope_query(
        select(EdwStaging).order_by(
            desc(EdwStaging.status == "MANUAL_INTERVENTION_REQUIRED"),
            desc(EdwStaging.created_at),
        ),
        user,
    )
    if case_status:
        stmt = stmt.where(EdwStaging.status == case_status)
    if recommendation:
        stmt = stmt.where(EdwStaging.recommendation == recommendation)
    if product_type:
        stmt = stmt.where(EdwStaging.product_type == product_type)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            EdwStaging.application_id.ilike(like)
            | EdwStaging.applicant_name.ilike(like)
        )
    stmt = stmt.limit(limit)

    rows = (await db.execute(stmt)).scalars().all()
    return [
        CaseListItem(
            id=s.id,
            application_id=s.application_id,
            applicant_name=s.applicant_name,
            product_type=s.product_type,
            branch_name=s.branch_name,
            validator_id=s.validator_id,
            status=s.status,
            recommendation=s.recommendation,
            manual_review_required=s.manual_review_required,
            error_detail=s.error_detail,
            created_at=s.created_at,
            updated_at=s.updated_at,
            completed_at=s.completed_at,
        )
        for s in rows
    ]


@router.get("/cases/{application_id}", response_model=CaseDetail)
async def get_case(
    application_id: str,
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> CaseDetail:
    """Full case detail — reads entirely from the edw_staging payload."""
    staging = (
        await db.execute(
            scope_query(
                select(EdwStaging).where(EdwStaging.application_id == application_id),
                user,
            )
        )
    ).scalar_one_or_none()
    if staging is None:
        raise HTTPException(status_code=404, detail="Case not found or not accessible.")

    p = staging.payload
    valids = p.get("validations", {})

    return CaseDetail(
        case=p["case"],
        employer_snapshot=p.get("employer_snapshot"),
        documents=[DocumentDetail(**d) for d in p.get("documents", [])],
        validations=ValidationGroups(
            passed=[ValidationDetail(**v) for v in valids.get("passed", [])],
            hard_breach=[ValidationDetail(**v) for v in valids.get("hard_breach", [])],
            soft_mismatch=[ValidationDetail(**v) for v in valids.get("soft_mismatch", [])],
            low_confidence=[ValidationDetail(**v) for v in valids.get("low_confidence", [])],
            manual_review=[ValidationDetail(**v) for v in valids.get("manual_review", [])],
        ),
        manual_checks=[ManualCheckItem(**m) for m in p.get("manual_checks", [])],
        technical_exceptions=[TechnicalException(**t) for t in p.get("technical_exceptions", [])],
        report=ReportSummary(**p["report"]),
        audit_timeline=[AuditEntryOut(**a) for a in p.get("audit_timeline", [])],
    )


@router.get("/cases/{application_id}/documents/{dms_document_id}/preview")
async def get_document_preview(
    application_id: str,
    dms_document_id: str,
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> Response:
    """Proxy the raw document bytes from DMS after verifying case ownership."""
    staging = (
        await db.execute(
            scope_query(
                select(EdwStaging).where(EdwStaging.application_id == application_id),
                user,
            )
        )
    ).scalar_one_or_none()
    if staging is None:
        raise HTTPException(status_code=404, detail="Case not found or not accessible.")

    doc_ids = {d["dms_document_id"] for d in staging.payload.get("documents", [])}
    if dms_document_id not in doc_ids:
        raise HTTPException(status_code=404, detail="Document not found for this case.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{settings.dms_base_url}/documents/{dms_document_id}/raw")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch document from DMS.")

    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/cases/{application_id}/report")
async def get_report(
    application_id: str,
    user: UserContext = Depends(auth_user),
    db: AsyncSession = Depends(get_read_session),
) -> Response:
    """Stream the case PDF report from DMS."""
    staging = (
        await db.execute(
            scope_query(
                select(EdwStaging).where(EdwStaging.application_id == application_id),
                user,
            )
        )
    ).scalar_one_or_none()
    if staging is None:
        raise HTTPException(status_code=404, detail="Case not found or not accessible.")

    if not staging.pdf_dms_document_id:
        raise HTTPException(status_code=404, detail="Report not yet available.")

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{settings.dms_base_url}/documents/{staging.pdf_dms_document_id}"
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch report from DMS.")

    pdf_bytes = base64.b64decode(resp.json()["content_base64"])
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="report_{application_id}.pdf"'},
    )
