import json
import re
from typing import Any

import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from creditunder.domain.enums import DocumentType
from creditunder.documents.base import BaseExtractionSchema
from creditunder.documents import EXTRACTION_SCHEMA_REGISTRY

log = structlog.get_logger(__name__)


class VerificationResult(BaseModel):
    is_correct_type: bool
    confidence: float
    detected_type: str
    notes: str = ""


class AIClient:
    def __init__(self, base_url: str, api_key: str, model: str, confidence_threshold: float = 0.7):
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model = model
        self._confidence_threshold = confidence_threshold

    async def verify_document_type(
        self,
        document_content: str,
        document_name: str,
        expected_type: DocumentType,
    ) -> VerificationResult:
        system_prompt = (
            "You are a document verification specialist. "
            "Given document content, determine if it is the expected document type. "
            "Respond with JSON only matching the schema provided."
        )
        user_prompt = (
            f"Document name: {document_name}\n"
            f"Expected document type: {expected_type.value}\n\n"
            f"Document content:\n{document_content}\n\n"
            "Verify whether this document matches the expected type. "
            "Return JSON with fields: is_correct_type (bool), confidence (0.0-1.0), "
            "detected_type (string), notes (string)."
        )

        log.info("ai.verify_document_type", document_name=document_name, expected_type=expected_type)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content
        return VerificationResult.model_validate_json(raw)

    async def extract_document_data(
        self,
        document_content: str,
        document_name: str,
        document_type: DocumentType,
    ) -> dict[str, Any]:
        schema_cls = EXTRACTION_SCHEMA_REGISTRY.get(document_type)
        if schema_cls is None:
            raise ValueError(f"No extraction schema registered for {document_type}")

        json_schema = schema_cls.model_json_schema()
        extraction_prompt = schema_cls.extraction_prompt()

        system_prompt = (
            f"{extraction_prompt}\n\n"
            "For each field, also provide a confidence score (0.0-1.0) and the page number (1-indexed). "
            "Return a JSON object where each key maps to an object with: "
            "value, confidence (float 0-1), page_reference (int), normalized_label (string). "
            f"The fields to extract are defined by this schema:\n{json.dumps(json_schema, indent=2)}"
        )
        user_prompt = f"Document name: {document_name}\n\nDocument content:\n{document_content}"

        log.info("ai.extract_document_data", document_name=document_name, document_type=document_type)
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        return json.loads(response.choices[0].message.content)

    async def generate_narrative(self, case_summary: dict[str, Any]) -> str:
        system_prompt = (
            "You are a credit analyst writing a formal report narrative for a bank validator. "
            "Write a concise, professional summary (3-5 paragraphs) covering: "
            "document verification status, key extracted data, validation findings, and the recommendation. "
            "Be factual and objective. Do not use bullet points — use flowing prose."
        )
        user_prompt = f"Case data:\n{json.dumps(case_summary, indent=2, default=str)}"

        log.info("ai.generate_narrative", application_id=case_summary.get("application_id"))
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
        )
        return response.choices[0].message.content
