import json
from typing import Any, Optional

import structlog
from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent, TextContent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from creditunder.domain.enums import DocumentType
from creditunder.domain.models import ExtractedField
from creditunder.documents import EXTRACTION_SCHEMA_REGISTRY
from creditunder.documents.base import BaseExtractionSchema

log = structlog.get_logger(__name__)

_agent_cache: dict[DocumentType, Agent] = {}
_narrative_agent_cache: dict[int, Agent] = {}


def _get_agent(document_type: DocumentType, model: OpenAIChatModel) -> Agent:
    if document_type not in _agent_cache:
        schema_cls = EXTRACTION_SCHEMA_REGISTRY[document_type]
        verification_fields = schema_cls.verification_field_names()
        extraction_fields = [f for f in schema_cls.model_fields if f not in verification_fields]

        system_prompt = (
            "You are a document verification and data extraction specialist for a bank.\n\n"
            f"VERIFICATION CRITERIA:\n{schema_cls.verification_prompt()}\n\n"
            f"EXTRACTION INSTRUCTIONS:\n{schema_cls.extraction_prompt()}\n\n"
            "INSTRUCTIONS:\n"
            "1. Fill the verification fields: is_correct_type, verification_confidence, detected_type, verification_notes.\n"
            f"2. If the document is the correct type, extract every field: {', '.join(extraction_fields)}.\n"
            "3. If it is NOT the correct type, leave all extraction fields as null.\n"
            "4. For each field provide: value, confidence (0.0-1.0), page_reference (1-indexed), normalized_label.\n"
            "   source_document_name should be set to the document name provided in the user message."
        )

        _agent_cache[document_type] = Agent(
            model=model,
            output_type=schema_cls,
            system_prompt=system_prompt,
            model_settings={"temperature": 0.0},
        )
    return _agent_cache[document_type]


def _get_narrative_agent(model: OpenAIChatModel) -> Agent:
    cache_key = id(model)
    if cache_key not in _narrative_agent_cache:
        _narrative_agent_cache[cache_key] = Agent(
            model=model,
            system_prompt=(
                "You are a senior credit analyst at a bank. "
                "Write exactly ONE concise paragraph (3-5 sentences) that: "
                "1) opens with a direct statement of the recommendation (APPROVE / HOLD / DECLINE), "
                "2) cites the one or two specific findings that drove it (e.g. salary deviation, "
                "   expired ID, employer class restriction, verification confidence), "
                "3) notes any mandatory manual checks the reviewer must complete before signing off. "
                "Be factual and precise — no filler phrases, no restating the applicant name or product. "
                "Do not use bullet points or headings."
            ),
            model_settings={"temperature": 0.3},
        )
    return _narrative_agent_cache[cache_key]


class VerifyAndExtractResult(BaseModel):
    is_correct_type: bool
    verification_confidence: float
    detected_type: str
    verification_notes: Optional[str] = None
    extracted_fields: dict[str, ExtractedField]


class AIClient:
    def __init__(self, base_url: str, api_key: str, model: str):
        self._model = OpenAIChatModel(
            model,
            provider=OpenAIProvider(base_url=base_url, api_key=api_key),
        )

    async def verify_and_extract(
        self,
        document_content: bytes,
        document_content_type: str,
        document_name: str,
        expected_type: DocumentType,
    ) -> VerifyAndExtractResult:
        if expected_type not in EXTRACTION_SCHEMA_REGISTRY:
            raise ValueError(f"No extraction schema registered for {expected_type}")

        agent = _get_agent(expected_type, self._model)
        schema_cls = EXTRACTION_SCHEMA_REGISTRY[expected_type]
        verification_fields = schema_cls.verification_field_names()

        header = (
            f"Document name: {document_name}\n"
            f"Expected document type: {expected_type.value}\n\n"
        )

        if document_content_type.startswith("image/"):
            user_message = [
                TextContent(content=header + "Analyse the document image below."),
                BinaryContent(data=document_content, media_type=document_content_type),
            ]
        else:
            text = document_content.decode("utf-8", errors="replace")
            user_message = header + f"Document content:\n{text}"

        log.info("ai.verify_and_extract", document_name=document_name, expected_type=expected_type)

        result = await agent.run(user_message)
        data: BaseExtractionSchema = result.output

        is_correct = data.is_correct_type.value
        v_confidence = data.verification_confidence.value
        detected = data.detected_type.value
        notes_field = data.verification_notes
        notes = notes_field.value if notes_field is not None else None

        extracted: dict[str, ExtractedField] = {}
        if is_correct:
            for field_name in schema_cls.model_fields:
                if field_name in verification_fields:
                    continue
                field_obj = getattr(data, field_name, None)
                if field_obj is not None:
                    extracted[field_name] = field_obj

        return VerifyAndExtractResult(
            is_correct_type=is_correct,
            verification_confidence=v_confidence,
            detected_type=detected,
            verification_notes=notes,
            extracted_fields=extracted,
        )

    async def generate_narrative(self, case_summary: dict[str, Any]) -> str:
        narrative_agent = _get_narrative_agent(self._model)
        user_message = f"Case data:\n{json.dumps(case_summary, indent=2, default=str)}"
        log.info("ai.generate_narrative", application_id=case_summary.get("application_id"))
        result = await narrative_agent.run(user_message)
        return result.output
