import base64
from datetime import datetime, timezone
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

from creditunder.domain.enums import ValidationOutcome
from creditunder.domain.models import (
    CaseResult,
    EmployerSnapshot,
    RequiredDocumentSet,
)
from creditunder.services.ai_client import AIClient
from creditunder.services.dms_client import DMSClient
from creditunder.validation_config import get_validation_config

log = structlog.get_logger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


class ReportGenerator:
    def __init__(self, ai_client: AIClient, dms_client: DMSClient):
        self._ai = ai_client
        self._dms = dms_client
        self._env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=True)

    async def generate(
        self,
        case_result: CaseResult,
        applicant_data: dict,
        branch_name: str,
        validator_id: str,
        required_documents: RequiredDocumentSet | None = None,
    ) -> tuple[str, bytes, str]:
        """
        Returns (html_content, pdf_bytes, narrative).
        """
        narrative = await self._ai.generate_narrative(
            {
                "application_id": case_result.application_id,
                "product_type": case_result.product_type,
                "recommendation": case_result.recommendation,
                "recommendation_rationale": case_result.recommendation_rationale,
                "applicant_data": applicant_data,
                "document_results": [
                    {
                        "document_id": dr.document_id,
                        "document_type": dr.document_type,
                        "document_name": dr.document_name,
                        "verification_passed": dr.verification_passed,
                        "verification_confidence": dr.verification_confidence,
                        "extracted_fields": {
                            k: {"value": ef.value, "confidence": ef.confidence}
                            for k, ef in dr.extracted_data.items()
                        },
                    }
                    for dr in case_result.document_results
                ],
                "validation_results": [
                    {
                        "rule_code": vr.rule_code,
                        "outcome": vr.outcome,
                        "description": vr.description,
                    }
                    for vr in case_result.validation_results
                ],
            }
        )

        # ----------------------------------------------------------------- #
        #  Derived projections used by the template                          #
        # ----------------------------------------------------------------- #

        employer = EmployerSnapshot.from_applicant_data(applicant_data)

        received_doc_types: list[str] = sorted({
            (dr.document_type.value if hasattr(dr.document_type, "value") else str(dr.document_type))
            for dr in case_result.document_results
        })
        if required_documents is not None:
            required_doc_types = sorted({
                (dt.value if hasattr(dt, "value") else str(dt))
                for dt in required_documents.required
            })
            missing_doc_types = sorted(set(required_doc_types) - set(received_doc_types))
        else:
            required_doc_types = []
            missing_doc_types = []

        # Manual-checks panel (BR-30 / BR-33): rule-driven manual review
        # items, deduplicated by rule_code so the section stays terse.
        seen_codes: set[str] = set()
        manual_check_items = []
        for vr in case_result.validation_results:
            if not (
                vr.manual_review_required
                or vr.outcome == ValidationOutcome.MANUAL_REVIEW_REQUIRED
            ):
                continue
            if vr.rule_code in seen_codes:
                continue
            seen_codes.add(vr.rule_code)
            manual_check_items.append(
                {
                    "rule_code": vr.rule_code,
                    "description": vr.description,
                    "field_name": vr.field_name,
                }
            )

        # Technical exceptions (BR-36 / BR-37): integration / extraction
        # failures that the reviewer needs to see, but that must not be
        # confused with business outcomes.
        technical_exceptions = []
        for dr in case_result.document_results:
            if not dr.verification_passed and not dr.extracted_data:
                technical_exceptions.append(
                    {
                        "kind": "VERIFICATION_FAILURE",
                        "description": (
                            "AI verification returned no extracted fields for this "
                            "document. Investigate before relying on the recommendation."
                        ),
                        "reference": f"{dr.document_type} — {dr.document_name}",
                    }
                )

        # Fetch each source document from DMS and embed as a base64 data URI
        # so the PDF is self-contained. A failure here is non-fatal for the
        # report (the rest of the document is still useful) but is surfaced
        # as a technical exception so the reviewer knows the embed is missing.
        doc_data_uris: dict[str, str] = {}
        doc_content_types: dict[str, str] = {}
        for dr in case_result.document_results:
            try:
                dms_doc = await self._dms.fetch_document(dr.document_id)
            except Exception as exc:
                log.warning(
                    "report.doc_embed_failed",
                    document_id=dr.document_id,
                    error=str(exc),
                )
                technical_exceptions.append(
                    {
                        "kind": "DOCUMENT_EMBED_FAILURE",
                        "description": (
                            f"Could not fetch source document from DMS for inline "
                            f"preview: {type(exc).__name__}: {exc}"
                        ),
                        "reference": f"{dr.document_type} — {dr.document_id}",
                    }
                )
                continue
            encoded = base64.b64encode(dms_doc.content).decode()
            doc_data_uris[dr.document_id] = f"data:{dms_doc.content_type};base64,{encoded}"
            doc_content_types[dr.document_id] = dms_doc.content_type

        config_version = get_validation_config().version

        template = self._env.get_template("report.html")
        html = template.render(
            application_id=case_result.application_id,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            product_type=case_result.product_type.value
            if hasattr(case_result.product_type, "value")
            else case_result.product_type,
            branch_name=branch_name,
            validator_id=validator_id,
            recommendation=case_result.recommendation.value
            if hasattr(case_result.recommendation, "value")
            else case_result.recommendation,
            recommendation_rationale=case_result.recommendation_rationale,
            applicant_data=applicant_data,
            employer_snapshot=employer.model_dump(mode="json") if employer else None,
            required_documents=required_doc_types,
            received_documents=received_doc_types,
            missing_documents=missing_doc_types,
            document_results=case_result.document_results,
            doc_data_uris=doc_data_uris,
            doc_content_types=doc_content_types,
            validation_results=case_result.validation_results,
            manual_check_items=manual_check_items,
            technical_exceptions=technical_exceptions,
            config_version=config_version,
            narrative=narrative,
        )

        pdf_bytes = self._html_to_pdf(html)
        log.info("report.generated", application_id=case_result.application_id)
        return html, pdf_bytes, narrative

    def _html_to_pdf(self, html: str) -> bytes:
        # WeasyPrint is a hard dependency: silently returning HTML bytes
        # would let an .html payload reach DMS as a "report.pdf".
        from weasyprint import HTML

        return HTML(string=html).write_pdf()
