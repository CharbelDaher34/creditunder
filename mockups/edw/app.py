"""
EDW (Enterprise Data Warehouse) mockup.
Accepts structured case exports and stores them in-memory.
Writes are idempotent on application_id.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException

app = FastAPI(title="EDW Mockup", version="1.0")

_exports: dict[str, dict] = {}  # keyed by application_id


@app.post("/export", status_code=200)
def export_case(payload: dict[str, Any]) -> dict:
    application_id = payload.get("application_id")
    if not application_id:
        raise HTTPException(status_code=400, detail="application_id is required")

    # Idempotent: if already exported, return same confirmation
    if application_id in _exports:
        return {
            "confirmation_id": _exports[application_id]["_confirmation_id"],
            "application_id": application_id,
            "status": "already_exported",
        }

    confirmation_id = f"EDW-{uuid.uuid4().hex[:12].upper()}"
    _exports[application_id] = {
        **payload,
        "_confirmation_id": confirmation_id,
        "_received_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "confirmation_id": confirmation_id,
        "application_id": application_id,
        "status": "accepted",
    }


@app.get("/exports")
def list_exports() -> list[dict]:
    return [
        {
            "application_id": v["application_id"],
            "confirmation_id": v["_confirmation_id"],
            "received_at": v["_received_at"],
            "recommendation": v.get("recommendation"),
            "product_type": v.get("product_type"),
        }
        for v in _exports.values()
    ]


@app.get("/exports/{application_id}")
def get_export(application_id: str) -> dict:
    record = _exports.get(application_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No export found for {application_id}")
    return record


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "exports_stored": len(_exports)}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("EDW_PORT", 8002)))
