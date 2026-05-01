"""
DMS (Document Management System) mockup.
Persists documents to /data on disk. Pre-seeded with sample documents on startup.
"""

import base64
import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

app = FastAPI(title="DMS Mockup", version="1.0")

DATA_DIR = Path("/data")
META_DIR = DATA_DIR / "meta"
FILES_DIR = DATA_DIR / "files"

for _d in (DATA_DIR, META_DIR, FILES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---- persistence helpers ----

def _meta_path(document_id: str) -> Path:
    return META_DIR / f"{document_id}.json"


def _save(document_id: str, document_name: str, document_type: str,
          content: bytes, content_type: str,
          related_application_id: str | None = None) -> None:
    ext = mimetypes.guess_extension(content_type) or ""
    ext = {".jpe": ".jpg", ".jpeg": ".jpg"}.get(ext, ext)
    file_name = f"{document_id}{ext}"
    (FILES_DIR / file_name).write_bytes(content)
    _meta_path(document_id).write_text(json.dumps({
        "document_id": document_id,
        "document_name": document_name,
        "document_type": document_type,
        "content_type": content_type,
        "file": file_name,
        "related_application_id": related_application_id,
    }))


def _load_meta(document_id: str) -> dict | None:
    p = _meta_path(document_id)
    return json.loads(p.read_text()) if p.exists() else None


def _load_content(meta: dict) -> bytes:
    return (FILES_DIR / meta["file"]).read_bytes()


def _all_meta() -> list[dict]:
    return [json.loads(p.read_text()) for p in META_DIR.glob("*.json")]


# ---- sample documents seeded at startup ----

SAMPLE_ID_DOCUMENT = """\
KINGDOM OF SAUDI ARABIA
NATIONAL ID CARD

Full Name:    Mohammed Al-Harbi
ID Number:    1082345678
Date of Birth: 1985-04-12
Expiry Date:  2028-09-30
Nationality:  Saudi Arabian
Gender:       Male
Place of Issue: Riyadh

This document certifies that the above-named individual is a
registered Saudi national. Valid for all official purposes.
""".strip()

SAMPLE_SALARY_CERTIFICATE = """\
SAUDI ARAMCO
Human Resources Department
P.O. Box 5000, Dhahran 31311, Saudi Arabia

SALARY CERTIFICATE

Date: 2026-04-15

To Whom It May Concern,

This is to certify that Mr. Mohammed Al-Harbi, holding Employee ID ARW-49821,
is employed with Saudi Aramco as a Senior Engineer.

Monthly Salary Breakdown:
  Basic Salary:          SAR 15,000.00
  Housing Allowance:     SAR 3,000.00
  Transport Allowance:   SAR 500.00
  Total Monthly Salary:  SAR 18,500.00

Mr. Al-Harbi has been in continuous employment since March 2010 and is in
good standing with the company.

This certificate is issued upon the employee's request for bank purposes.

Authorised Signatory
Saudi Aramco HR Department
""".strip()


def _seed() -> None:
    for doc_id, name, doc_type, content, ct in [
        ("DMS-00192", "national_id_al_harbi.txt", "ID_DOCUMENT",
         SAMPLE_ID_DOCUMENT.encode(), "text/plain"),
        ("DMS-00193", "salary_certificate_al_harbi.txt", "SALARY_CERTIFICATE",
         SAMPLE_SALARY_CERTIFICATE.encode(), "text/plain"),
    ]:
        if not _meta_path(doc_id).exists():
            _save(doc_id, name, doc_type, content, ct)


_seed()


# ---- API ----

class UploadRequest(BaseModel):
    document_name: str
    document_type: str
    content_base64: str
    content_type: str = "application/pdf"
    related_application_id: str | None = None


@app.get("/documents/{document_id}")
def get_document(document_id: str) -> dict:
    meta = _load_meta(document_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    content = _load_content(meta)
    return {
        "document_id": meta["document_id"],
        "document_name": meta["document_name"],
        "document_type": meta["document_type"],
        "content_type": meta["content_type"],
        "content_base64": base64.b64encode(content).decode(),
        "related_application_id": meta["related_application_id"],
    }


@app.get("/documents/{document_id}/raw")
def get_document_raw(document_id: str):
    """Serve raw file bytes with correct Content-Type (images render in browser)."""
    meta = _load_meta(document_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    content = _load_content(meta)
    return Response(content=content, media_type=meta["content_type"])


@app.get("/documents/{document_id}/metadata")
def get_metadata(document_id: str) -> dict:
    meta = _load_meta(document_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    content = _load_content(meta)
    return {
        "document_id": meta["document_id"],
        "document_name": meta["document_name"],
        "document_type": meta["document_type"],
        "content_type": meta["content_type"],
        "size_bytes": len(content),
        "related_application_id": meta["related_application_id"],
    }


@app.post("/documents", status_code=201)
def upload_document(req: UploadRequest) -> dict:
    document_id = f"DMS-{uuid.uuid4().hex[:8].upper()}"
    content = base64.b64decode(req.content_base64)
    _save(document_id, req.document_name, req.document_type,
          content, req.content_type, req.related_application_id)
    return {"document_id": document_id, "document_name": req.document_name}


@app.get("/documents")
def list_documents() -> list[dict]:
    result = []
    for meta in _all_meta():
        content = _load_content(meta)
        result.append({
            "document_id": meta["document_id"],
            "document_name": meta["document_name"],
            "document_type": meta["document_type"],
            "size_bytes": len(content),
            "related_application_id": meta["related_application_id"],
        })
    return result


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "documents_stored": len(list(META_DIR.glob("*.json")))}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("DMS_PORT", 8001)))
