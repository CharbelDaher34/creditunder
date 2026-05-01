import asyncio
import json
import os
from datetime import datetime, timezone
from typing import AsyncGenerator

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from creditunder.db.models import ApplicationCase, CaseDocument, CaseReport, ValidationResultRow
from creditunder.db.session import AsyncSessionLocal, get_session

CRM_BASE_URL = os.getenv("CRM_BASE_URL", "http://localhost:8003")

app = FastAPI(title="Reviewer Workbench API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SubmitApplicationRequest(BaseModel):
    product_type: str
    branch_name: str
    validator_id: str
    supervisor_id: str
    document_ids: list[str]
    applicant_data: dict


# ------------------------------------------------------------------ #
#  Case list                                                           #
# ------------------------------------------------------------------ #

@app.get("/api/cases")
async def get_cases(
    status: str | None = Query(default=None),
    recommendation: str | None = Query(default=None),
    product_type: str | None = Query(default=None),
    db: AsyncSession = Depends(get_session),
):
    stmt = select(ApplicationCase).order_by(ApplicationCase.created_at.desc())
    result = await db.execute(stmt)
    cases = result.scalars().all()

    # Apply optional filters
    if status:
        cases = [c for c in cases if c.status == status]
    if recommendation:
        cases = [c for c in cases if c.recommendation == recommendation]
    if product_type:
        cases = [c for c in cases if c.product_type == product_type]

    return [
        {
            "id": str(c.id),
            "application_id": c.application_id,
            "product_type": c.product_type,
            "branch_name": c.branch_name,
            "validator_id": c.validator_id,
            "status": c.status,
            "recommendation": c.recommendation,
            "manual_review_required": c.manual_review_required,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            "completed_at": c.completed_at.isoformat() if c.completed_at else None,
        }
        for c in cases
    ]


# ------------------------------------------------------------------ #
#  Case detail (keyed by application_id)                               #
# ------------------------------------------------------------------ #

@app.get("/api/cases/{application_id}")
async def get_case(application_id: str, db: AsyncSession = Depends(get_session)):
    result = await db.execute(
        select(ApplicationCase).where(ApplicationCase.application_id == application_id)
    )
    case = result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    docs_result = await db.execute(
        select(CaseDocument).where(CaseDocument.case_id == case.id)
    )
    docs = docs_result.scalars().all()

    vals_result = await db.execute(
        select(ValidationResultRow).where(ValidationResultRow.case_id == case.id)
    )
    vals = vals_result.scalars().all()

    report_result = await db.execute(
        select(CaseReport).where(CaseReport.case_id == case.id)
    )
    report = report_result.scalar_one_or_none()

    return {
        "case": {
            "id": str(case.id),
            "application_id": case.application_id,
            "product_type": case.product_type,
            "branch_name": case.branch_name,
            "validator_id": case.validator_id,
            "supervisor_id": case.supervisor_id,
            "status": case.status,
            "recommendation": case.recommendation,
            "recommendation_rationale": case.recommendation_rationale,
            "manual_review_required": case.manual_review_required,
            "applicant_data": case.applicant_data,
            "created_at": case.created_at.isoformat() if case.created_at else None,
            "completed_at": case.completed_at.isoformat() if case.completed_at else None,
        },
        "documents": [
            {
                "id": str(d.id),
                "dms_document_id": d.dms_document_id,
                "document_name": d.document_name,
                "document_type": d.document_type,
                "status": d.status,
                "verification_passed": d.verification_passed,
                "verification_confidence": d.verification_confidence,
                "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None,
                "verified_at": d.verified_at.isoformat() if d.verified_at else None,
            }
            for d in docs
        ],
        "validations": [
            {
                "id": str(v.id),
                "rule_code": v.rule_code,
                "outcome": v.outcome,
                "description": v.description,
                "field_name": v.field_name,
                "extracted_value": v.extracted_value,
                "expected_value": v.expected_value,
                "confidence": v.confidence,
                "manual_review_required": v.manual_review_required,
            }
            for v in vals
        ],
        "report": {
            "status": report.status if report else None,
            "pdf_available": bool(report and report.pdf_dms_document_id),
            "pdf_uploaded_at": report.pdf_uploaded_at.isoformat() if report and report.pdf_uploaded_at else None,
        },
    }


# ------------------------------------------------------------------ #
#  Report PDF streaming                                                #
# ------------------------------------------------------------------ #

@app.get("/api/cases/{application_id}/report")
async def get_report(application_id: str, db: AsyncSession = Depends(get_session)):
    case_result = await db.execute(
        select(ApplicationCase).where(ApplicationCase.application_id == application_id)
    )
    case = case_result.scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    report_result = await db.execute(
        select(CaseReport).where(CaseReport.case_id == case.id)
    )
    report = report_result.scalar_one_or_none()
    if not report or not report.pdf_dms_document_id:
        raise HTTPException(status_code=404, detail="Report not yet available")

    import base64
    dms_base_url = os.getenv("DMS_BASE_URL", "http://localhost:8001")
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{dms_base_url}/documents/{report.pdf_dms_document_id}")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to fetch report from DMS")

    dms_doc = resp.json()
    pdf_bytes = base64.b64decode(dms_doc["content_base64"])

    from fastapi.responses import Response
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="report_{application_id}.pdf"'
        },
    )


# ------------------------------------------------------------------ #
#  Submit new case (forwards to CRM → Kafka)                           #
# ------------------------------------------------------------------ #

@app.post("/api/cases")
async def submit_case(req: SubmitApplicationRequest):
    payload = {
        "product_type": req.product_type,
        "branch_name": req.branch_name,
        "validator_id": req.validator_id,
        "supervisor_id": req.supervisor_id,
        "document_ids": req.document_ids,
        "applicant_data": req.applicant_data,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CRM_BASE_URL}/publish", json=payload)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"CRM publish failed: {resp.text}")
    return resp.json()


# ------------------------------------------------------------------ #
#  SSE — real-time case status notifications                            #
# ------------------------------------------------------------------ #

async def case_status_generator(request: Request) -> AsyncGenerator:
    last_checked_time = datetime.now(timezone.utc)

    while True:
        if await request.is_disconnected():
            break

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ApplicationCase).where(
                    ApplicationCase.updated_at > last_checked_time
                )
            )
            updated_cases = result.scalars().all()
            if updated_cases:
                for case in updated_cases:
                    if case.status in ("COMPLETED", "FAILED", "MANUAL_INTERVENTION_REQUIRED"):
                        yield {
                            "event": "case_updated",
                            "data": json.dumps(
                                {
                                    "application_id": case.application_id,
                                    "status": case.status,
                                    "recommendation": case.recommendation,
                                    "manual_review_required": case.manual_review_required,
                                }
                            ),
                        }
                last_checked_time = max(c.updated_at for c in updated_cases)

        await asyncio.sleep(2)


@app.get("/api/notifications")
async def notifications(request: Request):
    return EventSourceResponse(case_status_generator(request))
