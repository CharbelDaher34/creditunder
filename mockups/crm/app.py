"""
CRM mockup API — receives credit application data via HTTP and publishes to Kafka.

Endpoints:
    POST /publish          — publish a fully-formed event to Kafka
    POST /publish/sample   — publish one of the predefined sample events
    GET  /samples          — list available sample templates
    GET  /health           — liveness check
"""

import asyncio
import json
import os
import uuid

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from aiokafka import AIOKafkaProducer


KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:19092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "credit-applications")

producer: AIOKafkaProducer | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global producer
    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
    )
    await producer.start()
    print(f"[CRM] Kafka producer connected to {KAFKA_BOOTSTRAP_SERVERS}")
    yield
    await producer.stop()
    print("[CRM] Kafka producer stopped")


app = FastAPI(title="CRM Mockup API", lifespan=lifespan)


# ---------- Models ----------

class PublishRequest(BaseModel):
    product_type: str
    branch_name: str
    validator_id: str
    supervisor_id: str
    document_ids: list[str]
    applicant_data: dict[str, Any]


class PublishSampleRequest(BaseModel):
    sample_index: int = 0


# ---------- Sample data (inline to avoid import issues in Docker) ----------

# Employer snapshot — CRM resolves the employer against the governed rules
# source at submission time and embeds the snapshot here so the processor
# never needs to fetch it.
_ARAMCO_EMPLOYER_SNAPSHOT = {
    "employer_id": "EMP-ARAMCO",
    "employer_name_normalized": "SAUDI ARAMCO",
    "employer_class": "A",
    "active_restrictions": [],
    "max_limit_note": None,
    "rule_version": "2026.04",
    "rule_source_date": "2026-04-15",
}


SAMPLE_TEMPLATES = [
    {
        "label": "Happy Path (salary match)",
        "product_type": "PERSONAL_FINANCE",
        "branch_name": "Riyadh Main Branch",
        "validator_id": "CRM-USR-4821",
        "supervisor_id": "CRM-USR-1093",
        "document_ids": ["DMS-00192", "DMS-00193"],
        "applicant_data": {
            "name": "Mohammed Al-Harbi",
            "id_number": "1082345678",
            "date_of_birth": "1985-04-12",
            "employer": "Saudi Aramco",
            "declared_salary": 18500.00,
            "simah_score": 720,
            "t24_account_id": "T24-ACC-998821",
            "requested_amount": 150000.00,
            "requested_tenure_months": 60,
            "employer_snapshot": _ARAMCO_EMPLOYER_SNAPSHOT,
        },
    },
    {
        "label": "Salary Mismatch (HOLD)",
        "product_type": "PERSONAL_FINANCE",
        "branch_name": "Jeddah Branch",
        "validator_id": "CRM-USR-5533",
        "supervisor_id": "CRM-USR-1093",
        "document_ids": ["DMS-00192", "DMS-00193"],
        "applicant_data": {
            "name": "Mohammed Al-Harbi",
            "id_number": "1082345678",
            "date_of_birth": "1985-04-12",
            "employer": "Saudi Aramco",
            "declared_salary": 25000.00,
            "simah_score": 680,
            "t24_account_id": "T24-ACC-112233",
            "requested_amount": 200000.00,
            "requested_tenure_months": 84,
            "employer_snapshot": _ARAMCO_EMPLOYER_SNAPSHOT,
        },
    },
]


# ---------- Endpoints ----------

@app.post("/publish")
async def publish_event(req: PublishRequest):
    """Accept application data, wrap it in a Kafka event, and publish."""
    event_id = str(uuid.uuid4())
    application_id = f"APP-{uuid.uuid4().hex[:8].upper()}"

    event = {
        "event_id": event_id,
        "application_id": application_id,
        "product_type": req.product_type,
        "branch_name": req.branch_name,
        "validator_id": req.validator_id,
        "supervisor_id": req.supervisor_id,
        "document_ids": req.document_ids,
        "applicant_data": req.applicant_data,
    }

    await producer.send_and_wait(KAFKA_TOPIC, value=event, key=application_id)
    print(
        f"[CRM] Published event  application_id={application_id}  event_id={event_id}"
    )

    return {
        "message": "published",
        "application_id": application_id,
        "event_id": event_id,
    }


@app.post("/publish/sample")
async def publish_sample(req: PublishSampleRequest):
    """Publish one of the predefined sample templates."""
    if req.sample_index < 0 or req.sample_index >= len(SAMPLE_TEMPLATES):
        raise HTTPException(status_code=400, detail=f"sample_index must be 0–{len(SAMPLE_TEMPLATES)-1}")

    template = SAMPLE_TEMPLATES[req.sample_index]
    publish_req = PublishRequest(
        product_type=template["product_type"],
        branch_name=template["branch_name"],
        validator_id=template["validator_id"],
        supervisor_id=template["supervisor_id"],
        document_ids=template["document_ids"],
        applicant_data=template["applicant_data"],
    )
    return await publish_event(publish_req)


@app.get("/samples")
async def list_samples():
    """Return the available sample templates so the UI can display them."""
    return [
        {"index": i, "label": t["label"], "product_type": t["product_type"]}
        for i, t in enumerate(SAMPLE_TEMPLATES)
    ]


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("CRM_PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port)
