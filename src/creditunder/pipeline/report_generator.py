import importlib.resources
from datetime import datetime, timezone
from pathlib import Path

import structlog
from jinja2 import Environment, FileSystemLoader

from creditunder.domain.models import CaseResult
from creditunder.services.ai_client import AIClient
from creditunder.services.dms_client import DMSClient

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
            document_results=case_result.document_results,
            validation_results=case_result.validation_results,
            narrative=narrative,
        )

        pdf_bytes = self._html_to_pdf(html)
        log.info("report.generated", application_id=case_result.application_id)
        return html, pdf_bytes, narrative

    def _html_to_pdf(self, html: str) -> bytes:
        try:
            from weasyprint import HTML

            return HTML(string=html).write_pdf()
        except ImportError:
            log.warning("weasyprint.not_installed", detail="Returning HTML bytes as fallback")
            return html.encode("utf-8")
