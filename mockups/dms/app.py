"""
DMS (Document Management System) mockup.
Stores documents in-memory. Pre-seeded with sample documents on startup.
"""

import base64
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="DMS Mockup", version="1.0")


@dataclass
class StoredDocument:
    document_id: str
    document_name: str
    document_type: str
    content: bytes
    content_type: str
    related_application_id: str | None = None
    metadata: dict = field(default_factory=dict)


# In-memory store
_store: dict[str, StoredDocument] = {}


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


def _seed() -> tuple[str, str]:
    id_doc_id = "DMS-00192"
    sal_doc_id = "DMS-00193"

    _store[id_doc_id] = StoredDocument(
        document_id=id_doc_id,
        document_name="national_id_al_harbi.txt",
        document_type="ID_DOCUMENT",
        content=SAMPLE_ID_DOCUMENT.encode(),
        content_type="text/plain",
    )
    _store[sal_doc_id] = StoredDocument(
        document_id=sal_doc_id,
        document_name="salary_certificate_al_harbi.txt",
        document_type="SALARY_CERTIFICATE",
        content=SAMPLE_SALARY_CERTIFICATE.encode(),
        content_type="text/plain",
    )
    return id_doc_id, sal_doc_id


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
    doc = _store.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return {
        "document_id": doc.document_id,
        "document_name": doc.document_name,
        "document_type": doc.document_type,
        "content_type": doc.content_type,
        "content_base64": base64.b64encode(doc.content).decode(),
        "related_application_id": doc.related_application_id,
    }


@app.get("/documents/{document_id}/metadata")
def get_metadata(document_id: str) -> dict:
    doc = _store.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document {document_id} not found")
    return {
        "document_id": doc.document_id,
        "document_name": doc.document_name,
        "document_type": doc.document_type,
        "content_type": doc.content_type,
        "size_bytes": len(doc.content),
        "related_application_id": doc.related_application_id,
    }


@app.post("/documents", status_code=201)
def upload_document(req: UploadRequest) -> dict:
    document_id = f"DMS-{uuid.uuid4().hex[:8].upper()}"
    content = base64.b64decode(req.content_base64)
    _store[document_id] = StoredDocument(
        document_id=document_id,
        document_name=req.document_name,
        document_type=req.document_type,
        content=content,
        content_type=req.content_type,
        related_application_id=req.related_application_id,
    )
    return {"document_id": document_id, "document_name": req.document_name}


@app.get("/documents")
def list_documents() -> list[dict]:
    return [
        {
            "document_id": d.document_id,
            "document_name": d.document_name,
            "document_type": d.document_type,
            "size_bytes": len(d.content),
            "related_application_id": d.related_application_id,
        }
        for d in _store.values()
    ]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "documents_stored": len(_store)}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("DMS_PORT", 8001)))
