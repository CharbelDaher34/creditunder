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

_ID_DOCUMENT_ROWS = [
    ("Document Type",  "National ID Card"),
    ("Issuer",         "Kingdom of Saudi Arabia"),
    ("Full Name",      "Mohammed Al-Harbi"),
    ("ID Number",      "1082345678"),
    ("Date of Birth",  "1985-04-12"),
    ("Expiry Date",    "2028-09-30"),
    ("Nationality",    "Saudi Arabian"),
    ("Gender",         "Male"),
    ("Place of Issue", "Riyadh"),
]

_SALARY_CERT_ROWS = [
    ("Document Type",     "Salary Certificate"),
    ("Employer",          "Saudi Aramco"),
    ("Issuing Department","Human Resources"),
    ("Date",              "2026-04-15"),
    ("Employee Name",     "Mohammed Al-Harbi"),
    ("Employee ID",       "ARW-49821"),
    ("Job Title",         "Senior Engineer"),
    ("Basic Salary",      "SAR 15,000.00"),
    ("Housing Allowance", "SAR 3,000.00"),
    ("Transport Allow.",  "SAR 500.00"),
    ("Total Salary",      "SAR 18,500.00"),
    ("Employment Since",  "March 2010"),
    ("Purpose",           "Bank financing"),
]


def _render_document_image(title: str, rows: list[tuple[str, str]]) -> bytes:
    """Render a two-column table as a PNG image and return the bytes."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    # Layout constants
    PAD = 40
    HEADER_H = 60
    ROW_H = 36
    COL1_W = 220
    COL2_W = 340
    WIDTH = PAD * 2 + COL1_W + COL2_W + 1  # +1 for divider line
    HEIGHT = PAD + HEADER_H + ROW_H * len(rows) + PAD

    BG       = (245, 247, 250)
    HEADER_BG= (30,  60, 114)
    ROW_ALT  = (255, 255, 255)
    ROW_EVEN = (235, 240, 248)
    BORDER   = (180, 190, 210)
    TEXT_HDR = (255, 255, 255)
    TEXT_KEY = (50,  70, 110)
    TEXT_VAL = (20,  20,  40)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Try to load a bundled font; fall back to default
    try:
        font_bold  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 15)
        font_reg   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 17)
    except OSError:
        font_bold = font_reg = font_title = ImageFont.load_default()

    # Header bar
    draw.rectangle([(0, PAD), (WIDTH, PAD + HEADER_H)], fill=HEADER_BG)
    draw.text((PAD, PAD + 14), title, font=font_title, fill=TEXT_HDR)

    # Rows
    for i, (key, val) in enumerate(rows):
        y = PAD + HEADER_H + i * ROW_H
        row_bg = ROW_EVEN if i % 2 == 0 else ROW_ALT
        draw.rectangle([(0, y), (WIDTH, y + ROW_H)], fill=row_bg)
        # Column divider
        draw.line([(PAD + COL1_W, y), (PAD + COL1_W, y + ROW_H)], fill=BORDER, width=1)
        # Row bottom border
        draw.line([(0, y + ROW_H - 1), (WIDTH, y + ROW_H - 1)], fill=BORDER, width=1)
        # Text
        draw.text((PAD + 6, y + 9), key, font=font_bold, fill=TEXT_KEY)
        draw.text((PAD + COL1_W + 10, y + 9), val, font=font_reg, fill=TEXT_VAL)

    # Outer border
    draw.rectangle([(0, PAD), (WIDTH - 1, HEIGHT - 1)], outline=BORDER, width=2)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


_SEED_VERSION = "v2"
_SEED_VERSION_FILE = DATA_DIR / ".seed_version"


def _seed() -> None:
    # Regenerate seeded documents when the seed version changes so that
    # fixes to the sample data take effect without manual volume cleanup.
    current = _SEED_VERSION_FILE.read_text().strip() if _SEED_VERSION_FILE.exists() else ""
    force = current != _SEED_VERSION

    for doc_id, name, doc_type, title, rows in [
        (
            "DMS-00192",
            "national_id_al_harbi.png",
            "ID_DOCUMENT",
            "NATIONAL ID CARD",
            _ID_DOCUMENT_ROWS,
        ),
        (
            "DMS-00193",
            "salary_certificate_al_harbi.png",
            "SALARY_CERTIFICATE",
            "SALARY CERTIFICATE",
            _SALARY_CERT_ROWS,
        ),
    ]:
        if force or not _meta_path(doc_id).exists():
            _save(doc_id, name, doc_type, _render_document_image(title, rows), "image/png")

    _SEED_VERSION_FILE.write_text(_SEED_VERSION)


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
