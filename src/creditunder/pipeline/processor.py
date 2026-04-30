import json
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creditunder.config import settings
from creditunder.db.models import (
    ApplicationCase,
    AuditEvent,
    CaseDocument,
    CaseReport,
    DeadLetterEvent,
    EDWStaging,
    InboundApplicationEvent,
    StageOutputVersion,
    ValidationResultRow,
)
from creditunder.db.session import AsyncSessionLocal
from creditunder.domain.enums import CaseStatus, DocumentStatus, EDWStatus
from creditunder.domain.models import (
    ApplicationEvent,
    CaseResult,
    DocumentResult,
    ExtractedField,
)
from creditunder.handlers.registry import get_handler
from creditunder.pipeline.report_generator import ReportGenerator
from creditunder.services.ai_client import AIClient
from creditunder.services.dms_client import DMSClient
from creditunder.services.edw_client import EDWClient

log = structlog.get_logger(__name__)


class ApplicationProcessor:
    def __init__(self):
        self._ai = AIClient(
            base_url=settings.ai_base_url,
            api_key=settings.ai_api_key,
            model=settings.ai_model,
            confidence_threshold=settings.ai_confidence_threshold,
        )
        self._dms = DMSClient(settings.dms_base_url)
        self._edw = EDWClient(settings.edw_base_url)
        self._report_gen = ReportGenerator(self._ai, self._dms)

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            settings.kafka_topic,
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=settings.kafka_consumer_group,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda m: json.loads(m.decode("utf-8")),
        )
        await consumer.start()
        log.info("processor.started", topic=settings.kafka_topic)
        try:
            async for message in consumer:
                await self._handle_message(message.value)
                await consumer.commit()
        finally:
            await consumer.stop()

    async def _handle_message(self, raw: dict) -> None:
        try:
            event = ApplicationEvent.model_validate(raw)
        except Exception as exc:
            await self._dead_letter(
                event_id=raw.get("event_id"),
                application_id=raw.get("application_id"),
                reason_code="INVALID_EVENT_SCHEMA",
                error=str(exc),
                raw_payload=raw,
            )
            return

        log.info("event.received", application_id=event.application_id, event_id=str(event.event_id))

        async with AsyncSessionLocal() as session:
            # Deduplicate on event_id
            try:
                inbound = InboundApplicationEvent(
                    event_id=event.event_id,
                    application_id=event.application_id,
                    raw_payload=raw,
                )
                session.add(inbound)
                await session.flush()
            except IntegrityError:
                await session.rollback()
                log.info("event.duplicate", event_id=str(event.event_id))
                return

            # Create or locate case (idempotent on application_id)
            case_row = await self._get_or_create_case(session, event)
            await session.commit()

        await self._process_case(event, case_row.id)

    async def _get_or_create_case(
        self, session: AsyncSession, event: ApplicationEvent
    ) -> ApplicationCase:
        result = await session.execute(
            select(ApplicationCase).where(ApplicationCase.application_id == event.application_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing

        case = ApplicationCase(
            application_id=event.application_id,
            event_id=event.event_id,
            product_type=event.product_type,
            branch_name=event.branch_name,
            validator_id=event.validator_id,
            supervisor_id=event.supervisor_id,
            applicant_data=event.applicant_data,
            status=CaseStatus.IN_PROGRESS,
        )
        session.add(case)
        await session.flush()
        await self._audit(session, case.id, event.application_id, "CASE_CREATED")
        return case

    async def _process_case(self, event: ApplicationEvent, case_id) -> None:
        try:
            handler = get_handler(event.product_type)
        except ValueError as exc:
            await self._dead_letter(
                event_id=str(event.event_id),
                application_id=event.application_id,
                reason_code="UNSUPPORTED_PRODUCT_TYPE",
                error=str(exc),
                raw_payload=event.model_dump(mode="json"),
            )
            return

        document_results: list[DocumentResult] = []

        for doc_id in event.document_ids:
            doc_result = await self._process_document(
                event=event,
                case_id=case_id,
                document_id=doc_id,
                handler=handler,
            )
            if doc_result:
                document_results.append(doc_result)

        # Validate and compute recommendation
        validation_results, recommendation, rationale = handler.validate(
            application_id=event.application_id,
            applicant_data=event.applicant_data,
            document_results=document_results,
        )

        case_result = CaseResult(
            application_id=event.application_id,
            product_type=event.product_type,
            document_results=document_results,
            validation_results=validation_results,
            recommendation=recommendation,
            recommendation_rationale=rationale,
        )

        async with AsyncSessionLocal() as session:
            # Persist validation results
            for vr in validation_results:
                session.add(
                    ValidationResultRow(
                        case_id=case_id,
                        rule_code=vr.rule_code,
                        outcome=vr.outcome.value if hasattr(vr.outcome, "value") else vr.outcome,
                        description=vr.description,
                        field_name=vr.field_name,
                        extracted_value=str(vr.extracted_value) if vr.extracted_value is not None else None,
                        expected_value=str(vr.expected_value) if vr.expected_value is not None else None,
                    )
                )
            await session.execute(
                update(ApplicationCase)
                .where(ApplicationCase.id == case_id)
                .values(
                    recommendation=recommendation.value if hasattr(recommendation, "value") else recommendation,
                    recommendation_rationale=rationale,
                )
            )
            await self._audit(session, case_id, event.application_id, "VALIDATION_COMPLETED",
                              {"recommendation": str(recommendation)})
            await session.commit()

        # Generate report
        await self._generate_and_deliver_report(event, case_id, case_result)

    async def _process_document(
        self, event: ApplicationEvent, case_id, document_id: str, handler
    ) -> DocumentResult | None:
        async with AsyncSessionLocal() as session:
            doc_row = CaseDocument(case_id=case_id, dms_document_id=document_id, status=DocumentStatus.PENDING)
            session.add(doc_row)
            await session.flush()
            doc_row_id = doc_row.id
            await session.commit()

        try:
            # Fetch from DMS
            dms_doc = await self._dms.fetch_document(document_id)
            document_content = dms_doc.content.decode("utf-8", errors="replace")

            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.FETCHED,
                        document_name=dms_doc.document_name,
                        document_type=dms_doc.document_type,
                    )
                )
                await session.commit()

            # Determine expected document type from DMS metadata
            from creditunder.domain.enums import DocumentType
            try:
                expected_type = DocumentType(dms_doc.document_type)
            except ValueError:
                log.warning("document.unknown_type", document_id=document_id, doc_type=dms_doc.document_type)
                return None

            # Verify document type
            verification = await self._ai.verify_document_type(
                document_content=document_content,
                document_name=dms_doc.document_name,
                expected_type=expected_type,
            )

            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        verification_passed=verification.is_correct_type,
                        verification_confidence=verification.confidence,
                        status=DocumentStatus.VERIFIED
                        if verification.is_correct_type
                        else DocumentStatus.VERIFICATION_FAILED,
                    )
                )
                await session.commit()

            if not verification.is_correct_type:
                log.warning("document.verification_failed", document_id=document_id,
                            detected=verification.detected_type)
                return DocumentResult(
                    document_id=document_id,
                    document_type=expected_type,
                    document_name=dms_doc.document_name,
                    verification_passed=False,
                    verification_confidence=verification.confidence,
                    extracted_data={},
                )

            # Extract data
            raw_extraction = await self._ai.extract_document_data(
                document_content=document_content,
                document_name=dms_doc.document_name,
                document_type=expected_type,
            )

            # Persist extraction version
            async with AsyncSessionLocal() as session:
                existing_versions = await session.execute(
                    select(StageOutputVersion).where(StageOutputVersion.case_document_id == doc_row_id)
                )
                version = len(existing_versions.scalars().all()) + 1
                session.add(StageOutputVersion(
                    case_document_id=doc_row_id,
                    version=version,
                    raw_extraction=raw_extraction,
                ))
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.EXTRACTED
                    )
                )
                await session.commit()

            # Convert raw extraction to ExtractedField objects
            extracted_data: dict[str, ExtractedField] = {}
            for field_name, field_data in raw_extraction.items():
                if isinstance(field_data, dict) and "value" in field_data:
                    extracted_data[field_name] = ExtractedField(
                        value=field_data["value"],
                        confidence=float(field_data.get("confidence", 0.5)),
                        source_document_name=dms_doc.document_name,
                        page_reference=int(field_data.get("page_reference", 1)),
                        normalized_label=field_data.get("normalized_label", field_name),
                    )

            return DocumentResult(
                document_id=document_id,
                document_type=expected_type,
                document_name=dms_doc.document_name,
                verification_passed=True,
                verification_confidence=verification.confidence,
                extracted_data=extracted_data,
            )

        except Exception as exc:
            log.error("document.processing_error", document_id=document_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.EXTRACTION_FAILED
                    )
                )
                await session.commit()
            return None

    async def _generate_and_deliver_report(
        self, event: ApplicationEvent, case_id, case_result: CaseResult
    ) -> None:
        async with AsyncSessionLocal() as session:
            report_row = CaseReport(case_id=case_id, status="GENERATING")
            session.add(report_row)
            await session.flush()
            report_id = report_row.id
            await session.commit()

        try:
            html, pdf_bytes, narrative = await self._report_gen.generate(
                case_result=case_result,
                applicant_data=event.applicant_data,
                branch_name=event.branch_name,
                validator_id=event.validator_id,
            )

            pdf_doc_id = await self._dms.upload_document(
                content=pdf_bytes,
                document_name=f"report_{event.application_id}.pdf",
                document_type="CREDIT_REPORT",
                content_type="application/pdf",
                related_application_id=event.application_id,
            )

            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(
                        html_content=html,
                        narrative=narrative,
                        pdf_dms_document_id=pdf_doc_id,
                        status="COMPLETED",
                    )
                )
                await self._audit(session, case_id, event.application_id, "REPORT_UPLOADED",
                                  {"pdf_dms_document_id": pdf_doc_id})
                await session.commit()

        except Exception as exc:
            log.error("report.generation_error", application_id=event.application_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(status="FAILED")
                )
                await session.commit()
            return

        await self._export_to_edw(event, case_id, case_result)

    async def _export_to_edw(
        self, event: ApplicationEvent, case_id, case_result: CaseResult
    ) -> None:
        payload = {
            "application_id": event.application_id,
            "event_id": str(event.event_id),
            "product_type": str(event.product_type),
            "branch_name": event.branch_name,
            "validator_id": event.validator_id,
            "supervisor_id": event.supervisor_id,
            "applicant_data": event.applicant_data,
            "recommendation": str(case_result.recommendation),
            "recommendation_rationale": case_result.recommendation_rationale,
            "validation_results": [
                {
                    "rule_code": vr.rule_code,
                    "outcome": str(vr.outcome),
                    "description": vr.description,
                    "field_name": vr.field_name,
                }
                for vr in case_result.validation_results
            ],
            "exported_at": datetime.now(timezone.utc).isoformat(),
        }

        async with AsyncSessionLocal() as session:
            staging = EDWStaging(case_id=case_id, payload=payload, status=EDWStatus.STAGED)
            session.add(staging)
            await session.flush()
            staging_id = staging.id
            await session.commit()

        try:
            confirmation_id = await self._edw.export(payload)
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(EDWStaging).where(EDWStaging.id == staging_id).values(
                        status=EDWStatus.EXPORTED,
                        edw_confirmation_id=confirmation_id,
                    )
                )
                await session.execute(
                    update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                        status=CaseStatus.COMPLETED
                    )
                )
                await self._audit(session, case_id, event.application_id, "EDW_EXPORTED",
                                  {"confirmation_id": confirmation_id})
                await session.commit()

            log.info("case.completed", application_id=event.application_id, recommendation=str(case_result.recommendation))

        except Exception as exc:
            log.error("edw.export_error", application_id=event.application_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(EDWStaging).where(EDWStaging.id == staging_id).values(
                        status=EDWStatus.EXPORT_FAILED
                    )
                )
                await session.execute(
                    update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                        status=CaseStatus.FAILED
                    )
                )
                await session.commit()

    @staticmethod
    async def _audit(
        session: AsyncSession, case_id, application_id: str, event_type: str, detail: dict | None = None
    ) -> None:
        session.add(AuditEvent(
            case_id=case_id,
            application_id=application_id,
            event_type=event_type,
            detail=detail,
        ))

    async def _dead_letter(
        self,
        event_id,
        application_id,
        reason_code: str,
        error: str,
        raw_payload: dict | None,
    ) -> None:
        log.error("dead_letter", reason_code=reason_code, application_id=application_id, error=error)
        async with AsyncSessionLocal() as session:
            from uuid import UUID
            session.add(DeadLetterEvent(
                event_id=UUID(str(event_id)) if event_id else None,
                application_id=application_id,
                reason_code=reason_code,
                error_detail=error,
                raw_payload=raw_payload,
            ))
            await session.commit()
