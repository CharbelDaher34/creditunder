"""Demo namespace — outside the v1 spec.

Kept so the bundled UI can submit events and receive SSE notifications
without standing up a separate CRM CORS path. Remove or gate behind a
feature flag before production deployment.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from creditunder.db.models import EdwStaging
from workbench.auth import UserContext, UserRole, auth_user, scope_query
from workbench.config import settings
from workbench.db import AsyncReadSessionLocal

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/_demo", tags=["demo"])

# Short-lived token store: token → (UserContext, expires_at).
# Tokens are minted by POST /notifications/token (header-authed) and consumed
# once by GET /notifications?token=…. The browser EventSource API cannot send
# custom headers, so the token is the only safe way to carry identity.
_TOKEN_TTL_SECONDS = 60
_pending_tokens: dict[str, tuple[UserContext, datetime]] = {}


class DMSUploadRequest(BaseModel):
    document_name: str
    document_type: str
    content_base64: str
    content_type: str = "image/png"
    related_application_id: str | None = None


@router.post("/dms/upload")
async def demo_dms_upload(req: DMSUploadRequest) -> dict:
    """Proxy a file upload to the DMS mockup (avoids CORS issues in dev)."""
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


@router.post("/submit")
async def demo_submit(req: SubmitApplicationRequest) -> dict:
    """Forward a demo submission to the CRM mockup, which publishes to Kafka."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{settings.crm_base_url}/publish", json=req.model_dump())
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"CRM publish failed: {resp.text}")
    return resp.json()


async def _case_status_stream(request: Request, user: UserContext):
    """Yield SSE events whenever a case in the caller's scope transitions.

    Reads from edw_staging (the workbench's only source of truth). Cases that
    never reach completion never appear here — that matches the rest of the
    workbench surface.
    """
    last_checked = datetime.now(timezone.utc)
    while True:
        if await request.is_disconnected():
            break
        async with AsyncReadSessionLocal() as session:
            stmt = scope_query(
                select(EdwStaging).where(EdwStaging.updated_at > last_checked),
                user,
            )
            updated = (await session.execute(stmt)).scalars().all()
        if updated:
            for staging in updated:
                yield {
                    "event": "case_updated",
                    "data": json.dumps({
                        "application_id": staging.application_id,
                        "status": staging.status,
                        "recommendation": staging.recommendation,
                        "manual_review_required": staging.manual_review_required,
                        "error_detail": staging.error_detail,
                    }),
                }
            last_checked = max(s.updated_at for s in updated)
        await asyncio.sleep(2)


@router.post("/notifications/token")
async def mint_notification_token(user: UserContext = Depends(auth_user)) -> dict:
    """Mint a short-lived (60 s) single-use token for the SSE stream.

    The browser EventSource API cannot send custom headers, so callers first
    POST here (with normal X-User-Id / X-User-Role headers) to get a token,
    then open the EventSource URL with ?token=<value>.
    """
    _prune_expired_tokens()
    token = str(uuid4())
    _pending_tokens[token] = (user, datetime.now(timezone.utc) + timedelta(seconds=_TOKEN_TTL_SECONDS))
    return {"token": token}


@router.get("/notifications")
async def notifications(request: Request, token: str) -> EventSourceResponse:
    """SSE stream of case-status updates scoped to the caller's identity.

    Requires a short-lived token from POST /notifications/token. The token is
    consumed on first use so it cannot be replayed.
    """
    _prune_expired_tokens()
    entry = _pending_tokens.pop(token, None)
    if entry is None:
        raise HTTPException(status_code=401, detail="Invalid or expired notification token.")
    user, _ = entry
    return EventSourceResponse(_case_status_stream(request, user))


def _prune_expired_tokens() -> None:
    now = datetime.now(timezone.utc)
    expired = [t for t, (_, exp) in _pending_tokens.items() if exp <= now]
    for t in expired:
        del _pending_tokens[t]
