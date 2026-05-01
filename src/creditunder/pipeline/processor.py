import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from creditunder.config import settings
from creditunder.db.models import (
    ApplicationCase,
    AuditEvent,
    CaseDocument,
    CaseReport,
    CaseResultRow,
    DeadLetterEvent,
    DMSArtifact,
    EDWStaging,
    InboundApplicationEvent,
    ProcessingJob,
    StageOutputVersion,
    ValidationResultRow,
)
from creditunder.db.session import AsyncSessionLocal
from creditunder.domain.enums import (
    CaseStatus,
    DocumentStatus,
    DocumentType,
    EDWStatus,
    ValidationOutcome,
)
from creditunder.domain.models import (
    ApplicationEvent,
    CaseResult,
    DocumentResult,
)
from creditunder.handlers.registry import get_handler
from creditunder.pipeline.report_generator import ReportGenerator
from creditunder.services.ai_client import AIClient
from creditunder.services.dms_client import DMSClient
from creditunder.services.edw_client import EDWClient

log = structlog.get_logger(__name__)

_ACTOR = "application_processor"

# Per-stage timeout budgets (seconds) — must sum well within 30s SLA.
_TIMEOUT_DMS_FETCH = 10.0
_TIMEOUT_AI_VERIFY_AND_EXTRACT = 25.0
_TIMEOUT_AI_NARRATIVE = 20.0
_TIMEOUT_DMS_UPLOAD = 10.0
_TIMEOUT_EDW_EXPORT = 30.0

_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # exponential: 1s, 2s, 4s

T = TypeVar("T")


def _enum_val(x):
    return x.value if hasattr(x, "value") else x


def _str_or_none(x) -> str | None:
    return str(x) if x is not None else None


class ApplicationProcessor:
    def __init__(self):
        self._ai = AIClient(
            base_url=settings.effective_ai_base_url,
            api_key=settings.effective_ai_api_key,
            model=settings.ai_model,
            confidence_threshold=settings.ai_confidence_threshold,
        )
        self._dms = DMSClient(settings.dms_base_url)
        self._edw = EDWClient(settings.edw_base_url)
        self._report_gen = ReportGenerator(self._ai, self._dms)

    # ------------------------------------------------------------------ #
    #  Kafka consumer loop                                                  #
    # ------------------------------------------------------------------ #

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

    # ------------------------------------------------------------------ #
    #  Message ingest                                                       #
    # ------------------------------------------------------------------ #

    async def _handle_message(self, raw: dict) -> None:
        try:
            event = ApplicationEvent.model_validate(raw)
        except Exception as exc:
            await self._dead_letter(
                event_id=raw.get("event_id"),
                application_id=raw.get("application_id"),
                case_id=None,
                reason_code="INVALID_EVENT_SCHEMA",
                error=str(exc),
                raw_payload=raw,
                stack_trace=traceback.format_exc(),
            )
            return

        log.info(
            "event.received",
            application_id=event.application_id,
            event_id=str(event.event_id),
        )

        async with AsyncSessionLocal() as session:
            try:
                session.add(InboundApplicationEvent(
                    event_id=event.event_id,
                    application_id=event.application_id,
                    product_type=event.product_type,
                    raw_payload=raw,
                    status="RECEIVED",
                ))
                await session.flush()
            except IntegrityError:
                await session.rollback()
                log.info("event.duplicate", event_id=str(event.event_id))
                return

            case_row = await self._get_or_create_case(session, event)
            await session.commit()

        await self._process_case(event, case_row.id)

    async def _get_or_create_case(
        self, session: AsyncSession, event: ApplicationEvent
    ) -> ApplicationCase:
        existing = await session.scalar(
            select(ApplicationCase).where(ApplicationCase.application_id == event.application_id)
        )
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

    # ------------------------------------------------------------------ #
    #  Core pipeline                                                        #
    # ------------------------------------------------------------------ #

    async def _process_case(self, event: ApplicationEvent, case_id: UUID) -> None:
        try:
            handler = get_handler(event.product_type)
        except ValueError as exc:
            await self._dead_letter(
                event_id=str(event.event_id),
                application_id=event.application_id,
                case_id=case_id,
                reason_code="UNSUPPORTED_PRODUCT_TYPE",
                error=str(exc),
                raw_payload=event.model_dump(mode="json"),
            )
            return

        # Fetch + verify + extract every document concurrently.
        results = await asyncio.gather(*[
            self._process_document(event, case_id, doc_id) for doc_id in event.document_ids
        ])
        document_results: list[DocumentResult] = [r for r in results if r is not None]

        # Completeness check — required document types must be represented.
        missing = set(handler.required_documents) - {dr.document_type for dr in document_results}
        if missing:
            await self._handle_missing_documents(event, case_id, missing)
            return

        validation_results, recommendation, rationale = handler.validate(
            application_id=event.application_id,
            applicant_data=event.applicant_data,
            document_results=document_results,
        )
        manual_review_required = any(vr.manual_review_required for vr in validation_results)
        completed_at = datetime.now(timezone.utc)

        case_result = CaseResult(
            application_id=event.application_id,
            product_type=event.product_type,
            document_results=document_results,
            validation_results=validation_results,
            recommendation=recommendation,
            recommendation_rationale=rationale,
            manual_review_required=manual_review_required,
            completed_at=completed_at,
        )

        await self._persist_validation(
            case_id=case_id,
            application_id=event.application_id,
            validation_results=validation_results,
            document_results=document_results,
            recommendation=recommendation,
            rationale=rationale,
            manual_review_required=manual_review_required,
            completed_at=completed_at,
        )

        await self._generate_and_deliver_report(event, case_id, case_result)

    async def _handle_missing_documents(
        self, event: ApplicationEvent, case_id: UUID, missing: set
    ) -> None:
        missing_names = ", ".join(str(t) for t in missing)
        log.warning(
            "case.missing_required_documents",
            application_id=event.application_id,
            missing=missing_names,
        )
        await self._dead_letter(
            event_id=str(event.event_id),
            application_id=event.application_id,
            case_id=case_id,
            reason_code="MISSING_REQUIRED_DOCUMENTS",
            error=f"Required document types not found: {missing_names}",
            raw_payload=event.model_dump(mode="json"),
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.MANUAL_INTERVENTION_REQUIRED
                )
            )
            await self._audit(
                session, case_id, event.application_id, "MISSING_REQUIRED_DOCUMENTS",
                {"missing": missing_names}, actor=_ACTOR,
            )
            await session.commit()

    async def _persist_validation(
        self,
        *,
        case_id: UUID,
        application_id: str,
        validation_results,
        document_results: list[DocumentResult],
        recommendation,
        rationale: str,
        manual_review_required: bool,
        completed_at: datetime,
    ) -> None:
        rec_str = _enum_val(recommendation)
        async with AsyncSessionLocal() as session:
            for vr in validation_results:
                ev_str = _str_or_none(vr.extracted_value)
                exp_str = _str_or_none(vr.expected_value)
                is_manual = (
                    vr.manual_review_required
                    or vr.outcome == ValidationOutcome.MANUAL_REVIEW_REQUIRED
                )
                session.add(ValidationResultRow(
                    case_id=case_id,
                    rule_code=vr.rule_code,
                    outcome=_enum_val(vr.outcome),
                    description=vr.description,
                    field_name=vr.field_name,
                    extracted_value=ev_str,
                    expected_value=exp_str,
                    confidence=vr.confidence,
                    manual_review_required=is_manual,
                    input_data={"field_name": vr.field_name, "extracted_value": ev_str},
                    details={"description": vr.description, "expected_value": exp_str},
                ))

            session.add(CaseResultRow(
                case_id=case_id,
                recommendation=rec_str,
                manual_review_required=manual_review_required,
                completed_at=completed_at,
            ))

            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    recommendation=rec_str,
                    recommendation_rationale=rationale,
                    manual_review_required=manual_review_required,
                )
            )

            verified_doc_ids = [
                dr.document_id for dr in document_results
                if dr.verification_passed and dr.extracted_data
            ]
            if verified_doc_ids:
                await session.execute(
                    update(CaseDocument)
                    .where(
                        CaseDocument.case_id == case_id,
                        CaseDocument.dms_document_id.in_(verified_doc_ids),
                    )
                    .values(status=DocumentStatus.VALIDATION_COMPLETED)
                )

            await self._audit(
                session, case_id, application_id, "VALIDATION_COMPLETED",
                {"recommendation": rec_str}, actor=_ACTOR,
            )
            await session.commit()

    # ------------------------------------------------------------------ #
    #  Document processing                                                  #
    # ------------------------------------------------------------------ #

    async def _process_document(
        self, event: ApplicationEvent, case_id: UUID, document_id: str
    ) -> DocumentResult | None:
        # Reserve a row for this document.
        async with AsyncSessionLocal() as session:
            doc_row = CaseDocument(
                case_id=case_id, dms_document_id=document_id, status=DocumentStatus.PENDING
            )
            session.add(doc_row)
            await session.flush()
            doc_row_id = doc_row.id
            await session.commit()

        # Stage 1: fetch from DMS.
        try:
            dms_doc = await self._run_with_job(
                case_id=case_id,
                job_type="DOCUMENT_FETCH",
                coro_fn=lambda: self._dms.fetch_document(document_id),
                timeout=_TIMEOUT_DMS_FETCH,
            )
        except Exception as exc:
            log.error("document.fetch_failed", document_id=document_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.EXTRACTION_FAILED
                    )
                )
                session.add(DMSArtifact(
                    case_id=case_id,
                    dms_document_id=document_id,
                    artifact_type="SOURCE_DOCUMENT",
                    direction="INBOUND",
                    status="FAILED",
                    error_details={"error": str(exc)},
                ))
                await session.commit()
            return None

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                    status=DocumentStatus.FETCHED,
                    document_name=dms_doc.document_name,
                    document_type=dms_doc.document_type,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            session.add(DMSArtifact(
                case_id=case_id,
                dms_document_id=document_id,
                artifact_type="SOURCE_DOCUMENT",
                direction="INBOUND",
                status="SUCCESS",
            ))
            await session.commit()

        try:
            expected_type = DocumentType(dms_doc.document_type)
        except ValueError:
            log.warning(
                "document.unknown_type",
                document_id=document_id,
                doc_type=dms_doc.document_type,
            )
            return None

        # Stage 2: AI verify + extract (single LLM call).
        try:
            ai_result = await self._run_with_job(
                case_id=case_id,
                job_type="VERIFY_AND_EXTRACT",
                coro_fn=lambda: self._ai.verify_and_extract(
                    document_content=dms_doc.content,
                    document_content_type=dms_doc.content_type,
                    document_name=dms_doc.document_name,
                    expected_type=expected_type,
                ),
                timeout=_TIMEOUT_AI_VERIFY_AND_EXTRACT,
            )
        except Exception as exc:
            log.error(
                "document.verify_and_extract_error",
                document_id=document_id,
                error=str(exc),
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.VERIFICATION_FAILED
                    )
                )
                await session.commit()
            return DocumentResult(
                document_id=document_id,
                document_type=expected_type,
                document_name=dms_doc.document_name,
                verification_passed=False,
                verification_confidence=0.0,
                extracted_data={},
            )

        is_verified = ai_result.is_correct_type
        verified_at = datetime.now(timezone.utc)

        # Single transaction: persist verification result and (when verified)
        # the extracted-fields version row + EXTRACTED status.
        async with AsyncSessionLocal() as session:
            if is_verified:
                last_version = await session.scalar(
                    select(func.max(StageOutputVersion.version)).where(
                        StageOutputVersion.case_document_id == doc_row_id
                    )
                )
                session.add(StageOutputVersion(
                    case_document_id=doc_row_id,
                    version=(last_version or 0) + 1,
                    raw_extraction={
                        k: v.model_dump() for k, v in ai_result.extracted_fields.items()
                    },
                    is_valid=True,
                ))
                new_status = DocumentStatus.EXTRACTED
            else:
                new_status = DocumentStatus.TYPE_MISMATCH

            await session.execute(
                update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                    verification_passed=is_verified,
                    verification_confidence=ai_result.verification_confidence,
                    status=new_status,
                    verified_at=verified_at,
                )
            )
            await session.commit()

        if not is_verified:
            log.warning(
                "document.type_mismatch",
                document_id=document_id,
                detected=ai_result.detected_type,
            )
            return DocumentResult(
                document_id=document_id,
                document_type=expected_type,
                document_name=dms_doc.document_name,
                verification_passed=False,
                verification_confidence=ai_result.verification_confidence,
                extracted_data={},
            )

        return DocumentResult(
            document_id=document_id,
            document_type=expected_type,
            document_name=dms_doc.document_name,
            verification_passed=True,
            verification_confidence=ai_result.verification_confidence,
            extracted_data=ai_result.extracted_fields,
        )

    # ------------------------------------------------------------------ #
    #  Report generation and DMS delivery                                   #
    # ------------------------------------------------------------------ #

    async def _generate_and_deliver_report(
        self, event: ApplicationEvent, case_id: UUID, case_result: CaseResult
    ) -> None:
        async with AsyncSessionLocal() as session:
            report_row = CaseReport(case_id=case_id, status="GENERATING")
            session.add(report_row)
            await session.flush()
            report_id = report_row.id
            await session.commit()

        try:
            html, pdf_bytes, narrative = await self._run_with_job(
                case_id=case_id,
                job_type="REPORT_GENERATION",
                coro_fn=lambda: self._report_gen.generate(
                    case_result=case_result,
                    applicant_data=event.applicant_data,
                    branch_name=event.branch_name,
                    validator_id=event.validator_id,
                ),
                timeout=_TIMEOUT_AI_NARRATIVE + 10.0,  # narrative + rendering budget
            )
        except Exception as exc:
            log.error(
                "report.generation_failed",
                application_id=event.application_id,
                error=str(exc),
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(status="FAILED")
                )
                await session.commit()
            return

        ready_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    html_content=html,
                    narrative=narrative,
                    html_generated_at=ready_at,
                    pdf_generated_at=ready_at,
                    status="PDF_READY",
                )
            )
            await session.commit()

        # Stage: DMS upload (PDF report).
        try:
            pdf_doc_id = await self._run_with_job(
                case_id=case_id,
                job_type="REPORT_UPLOAD",
                coro_fn=lambda: self._dms.upload_document(
                    content=pdf_bytes,
                    document_name=f"report_{event.application_id}.pdf",
                    document_type="CREDIT_REPORT",
                    content_type="application/pdf",
                    related_application_id=event.application_id,
                ),
                timeout=_TIMEOUT_DMS_UPLOAD,
            )
        except Exception as exc:
            log.error(
                "report.upload_failed",
                application_id=event.application_id,
                error=str(exc),
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(
                        status="UPLOAD_FAILED"
                    )
                )
                session.add(DMSArtifact(
                    case_id=case_id,
                    dms_document_id="",
                    artifact_type="PDF_REPORT",
                    direction="OUTBOUND",
                    status="FAILED",
                    error_details={"error": str(exc)},
                ))
                await session.commit()
            return

        uploaded_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    pdf_dms_document_id=pdf_doc_id,
                    pdf_uploaded_at=uploaded_at,
                    status="COMPLETED",
                )
            )
            session.add(DMSArtifact(
                case_id=case_id,
                dms_document_id=pdf_doc_id,
                artifact_type="PDF_REPORT",
                direction="OUTBOUND",
                status="SUCCESS",
            ))
            await self._audit(
                session, case_id, event.application_id, "REPORT_UPLOADED",
                {"pdf_dms_document_id": pdf_doc_id}, actor=_ACTOR,
            )
            await session.commit()

        await self._export_to_edw(event, case_id, case_result)

    # ------------------------------------------------------------------ #
    #  EDW export                                                           #
    # ------------------------------------------------------------------ #

    async def _export_to_edw(
        self, event: ApplicationEvent, case_id: UUID, case_result: CaseResult
    ) -> None:
        rec_str = str(case_result.recommendation)
        payload = {
            "application_id": event.application_id,
            "event_id": str(event.event_id),
            "product_type": str(event.product_type),
            "branch_name": event.branch_name,
            "validator_id": event.validator_id,
            "supervisor_id": event.supervisor_id,
            "applicant_data": event.applicant_data,
            "recommendation": rec_str,
            "recommendation_rationale": case_result.recommendation_rationale,
            "manual_review_required": case_result.manual_review_required,
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
            confirmation_id = await self._run_with_job(
                case_id=case_id,
                job_type="EDW_WRITE",
                coro_fn=lambda: self._edw.export(payload),
                timeout=_TIMEOUT_EDW_EXPORT,
            )
        except Exception as exc:
            log.error(
                "edw.export_failed",
                application_id=event.application_id,
                error=str(exc),
            )
            # EDW failure does NOT mark the case FAILED — business processing
            # completed. The edw_staging row stays for independent retry.
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(EDWStaging).where(EDWStaging.id == staging_id).values(
                        status=EDWStatus.EXPORT_FAILED,
                        export_error=str(exc),
                    )
                )
                await session.commit()
            return

        now = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(EDWStaging).where(EDWStaging.id == staging_id).values(
                    status=EDWStatus.EXPORTED,
                    edw_confirmation_id=confirmation_id,
                    exported_at=now,
                )
            )
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.COMPLETED,
                    completed_at=now,
                )
            )
            await self._audit(
                session, case_id, event.application_id, "CASE_COMPLETED",
                {"recommendation": rec_str, "edw_confirmation_id": confirmation_id},
                actor=_ACTOR,
            )
            await session.commit()

        log.info("case.completed", application_id=event.application_id, recommendation=rec_str)

    # ------------------------------------------------------------------ #
    #  Retry wrapper with processing_job tracking                           #
    # ------------------------------------------------------------------ #

    async def _run_with_job(
        self,
        case_id: UUID,
        job_type: str,
        coro_fn: Callable[[], Awaitable[T]],
        timeout: float,
        max_attempts: int = _MAX_ATTEMPTS,
        base_delay: float = _RETRY_BASE_DELAY,
    ) -> T:
        async with AsyncSessionLocal() as session:
            job = ProcessingJob(
                case_id=case_id,
                job_type=job_type,
                status="IN_PROGRESS",
                max_attempts=max_attempts,
                attempt_count=1,
                last_attempted_at=datetime.now(timezone.utc),
            )
            session.add(job)
            await session.flush()
            job_id = job.id
            await session.commit()

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await asyncio.wait_for(coro_fn(), timeout=timeout)
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                            status="COMPLETED",
                            attempt_count=attempt,
                            completed_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
                return result
            except Exception as exc:
                last_exc = exc
                is_last = attempt == max_attempts
                log.warning(
                    "job.attempt_failed",
                    job_type=job_type,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(exc),
                )
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                            status="FAILED" if is_last else "RETRYING",
                            attempt_count=attempt,
                            last_error=str(exc),
                            last_attempted_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
                if not is_last:
                    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

        raise last_exc

    # ------------------------------------------------------------------ #
    #  Helpers                                                              #
    # ------------------------------------------------------------------ #

    @staticmethod
    async def _audit(
        session: AsyncSession,
        case_id,
        application_id: str,
        event_type: str,
        detail: dict | None = None,
        actor: str = _ACTOR,
    ) -> None:
        session.add(AuditEvent(
            case_id=case_id,
            application_id=application_id,
            event_type=event_type,
            actor=actor,
            detail=detail,
        ))

    async def _dead_letter(
        self,
        event_id,
        application_id,
        case_id,
        reason_code: str,
        error: str,
        raw_payload: dict | None,
        stack_trace: str | None = None,
    ) -> None:
        log.error(
            "dead_letter",
            reason_code=reason_code,
            application_id=application_id,
            error=error,
        )
        async with AsyncSessionLocal() as session:
            session.add(DeadLetterEvent(
                event_id=UUID(str(event_id)) if event_id else None,
                case_id=UUID(str(case_id)) if case_id else None,
                application_id=application_id,
                reason_code=reason_code,
                error_detail=error,
                raw_payload=raw_payload,
                stack_trace=stack_trace,
            ))
            await session.commit()
