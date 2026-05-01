import asyncio
import json
import traceback
from datetime import datetime, timezone
from uuid import UUID, uuid4

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

_ACTOR = "application_processor"

# Per-stage timeout budgets (seconds) — must sum well within 30s SLA
_TIMEOUT_DMS_FETCH = 10.0
_TIMEOUT_AI_VERIFY = 15.0
_TIMEOUT_AI_EXTRACT = 20.0
_TIMEOUT_AI_NARRATIVE = 20.0
_TIMEOUT_DMS_UPLOAD = 10.0
_TIMEOUT_EDW_EXPORT = 30.0

# Retry config
_MAX_ATTEMPTS = 3
_RETRY_BASE_DELAY = 1.0  # doubles each attempt: 1s, 2s, 4s


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
    #  Message entry point                                                  #
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

        log.info("event.received", application_id=event.application_id, event_id=str(event.event_id))

        async with AsyncSessionLocal() as session:
            try:
                inbound = InboundApplicationEvent(
                    event_id=event.event_id,
                    application_id=event.application_id,
                    product_type=event.product_type,
                    raw_payload=raw,
                    status="RECEIVED",
                )
                session.add(inbound)
                await session.flush()
            except IntegrityError:
                await session.rollback()
                log.info("event.duplicate", event_id=str(event.event_id))
                return

            case_row = await self._get_or_create_case(session, event)
            await session.commit()

        await self._process_case(event, case_row.id)

    # ------------------------------------------------------------------ #
    #  Case creation                                                        #
    # ------------------------------------------------------------------ #

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

        # Fetch and verify/extract each document
        document_results: list[DocumentResult] = []
        for doc_id in event.document_ids:
            doc_result = await self._process_document(
                event=event, case_id=case_id, document_id=doc_id
            )
            if doc_result:
                document_results.append(doc_result)

        # Completeness check — required document types must be represented
        from creditunder.domain.enums import DocumentType
        required = set(handler.required_documents)
        available = {dr.document_type for dr in document_results}
        missing = required - available
        if missing:
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
            return

        # Run product validation rules
        validation_results, recommendation, rationale = handler.validate(
            application_id=event.application_id,
            applicant_data=event.applicant_data,
            document_results=document_results,
        )

        manual_review_required = any(
            vr.manual_review_required for vr in validation_results
        )
        handler_completed_at = datetime.now(timezone.utc)

        case_result = CaseResult(
            application_id=event.application_id,
            product_type=event.product_type,
            document_results=document_results,
            validation_results=validation_results,
            recommendation=recommendation,
            recommendation_rationale=rationale,
            manual_review_required=manual_review_required,
            completed_at=handler_completed_at,
        )

        async with AsyncSessionLocal() as session:
            # Persist validation results (append-only)
            for vr in validation_results:
                outcome_val = vr.outcome.value if hasattr(vr.outcome, "value") else vr.outcome
                is_manual = (
                    vr.outcome == "MANUAL_REVIEW_REQUIRED"
                    or str(vr.outcome) == "MANUAL_REVIEW_REQUIRED"
                    or vr.manual_review_required
                )
                session.add(
                    ValidationResultRow(
                        case_id=case_id,
                        rule_code=vr.rule_code,
                        outcome=outcome_val,
                        description=vr.description,
                        field_name=vr.field_name,
                        extracted_value=(
                            str(vr.extracted_value) if vr.extracted_value is not None else None
                        ),
                        expected_value=(
                            str(vr.expected_value) if vr.expected_value is not None else None
                        ),
                        confidence=vr.confidence,
                        manual_review_required=is_manual,
                        input_data={
                            "field_name": vr.field_name,
                            "extracted_value": str(vr.extracted_value)
                            if vr.extracted_value is not None
                            else None,
                        },
                        details={
                            "description": vr.description,
                            "expected_value": str(vr.expected_value)
                            if vr.expected_value is not None
                            else None,
                        },
                    )
                )

            # Persist aggregated case_result row (handler completion time)
            session.add(
                CaseResultRow(
                    case_id=case_id,
                    recommendation=recommendation.value
                    if hasattr(recommendation, "value")
                    else recommendation,
                    manual_review_required=manual_review_required,
                    completed_at=handler_completed_at,
                )
            )

            # Update case with recommendation and mark validated documents
            await session.execute(
                update(ApplicationCase)
                .where(ApplicationCase.id == case_id)
                .values(
                    recommendation=recommendation.value
                    if hasattr(recommendation, "value")
                    else recommendation,
                    recommendation_rationale=rationale,
                    manual_review_required=manual_review_required,
                )
            )
            # Advance each successfully extracted document to VALIDATION_COMPLETED
            for dr in document_results:
                if dr.verification_passed and dr.extracted_data:
                    await session.execute(
                        update(CaseDocument)
                        .where(
                            CaseDocument.case_id == case_id,
                            CaseDocument.dms_document_id == dr.document_id,
                        )
                        .values(status=DocumentStatus.VALIDATION_COMPLETED)
                    )
            await self._audit(
                session, case_id, event.application_id, "VALIDATION_COMPLETED",
                {"recommendation": str(recommendation)}, actor=_ACTOR,
            )
            await session.commit()

        await self._generate_and_deliver_report(event, case_id, case_result)

    # ------------------------------------------------------------------ #
    #  Document processing                                                  #
    # ------------------------------------------------------------------ #

    async def _process_document(
        self, event: ApplicationEvent, case_id: UUID, document_id: str
    ) -> DocumentResult | None:
        async with AsyncSessionLocal() as session:
            doc_row = CaseDocument(
                case_id=case_id, dms_document_id=document_id, status=DocumentStatus.PENDING
            )
            session.add(doc_row)
            await session.flush()
            doc_row_id = doc_row.id
            await session.commit()

        # Stage: DOCUMENT_FETCH
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
                session.add(
                    DMSArtifact(
                        case_id=case_id,
                        dms_document_id=document_id,
                        artifact_type="SOURCE_DOCUMENT",
                        direction="INBOUND",
                        status="FAILED",
                        error_details={"error": str(exc)},
                    )
                )
                await session.commit()
            return None

        document_content = dms_doc.content.decode("utf-8", errors="replace")
        now = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                    status=DocumentStatus.FETCHED,
                    document_name=dms_doc.document_name,
                    document_type=dms_doc.document_type,
                    fetched_at=now,
                )
            )
            session.add(
                DMSArtifact(
                    case_id=case_id,
                    dms_document_id=document_id,
                    artifact_type="SOURCE_DOCUMENT",
                    direction="INBOUND",
                    status="SUCCESS",
                )
            )
            await session.commit()

        from creditunder.domain.enums import DocumentType
        try:
            expected_type = DocumentType(dms_doc.document_type)
        except ValueError:
            log.warning(
                "document.unknown_type",
                document_id=document_id,
                doc_type=dms_doc.document_type,
            )
            return None

        # Stage: VERIFICATION
        try:
            verification = await self._run_with_job(
                case_id=case_id,
                job_type="VERIFICATION",
                coro_fn=lambda: self._ai.verify_document_type(
                    document_content=document_content,
                    document_name=dms_doc.document_name,
                    expected_type=expected_type,
                ),
                timeout=_TIMEOUT_AI_VERIFY,
            )
        except Exception as exc:
            log.error("document.verification_error", document_id=document_id, error=str(exc))
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

        verified_at = datetime.now(timezone.utc)
        is_verified = verification.is_correct_type
        doc_status = (
            DocumentStatus.VERIFIED if is_verified else DocumentStatus.TYPE_MISMATCH
        )

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                    verification_passed=is_verified,
                    verification_confidence=verification.confidence,
                    status=doc_status,
                    verified_at=verified_at,
                )
            )
            await session.commit()

        if not is_verified:
            log.warning(
                "document.type_mismatch",
                document_id=document_id,
                detected=verification.detected_type,
            )
            return DocumentResult(
                document_id=document_id,
                document_type=expected_type,
                document_name=dms_doc.document_name,
                verification_passed=False,
                verification_confidence=verification.confidence,
                extracted_data={},
            )

        # Stage: EXTRACTION
        try:
            raw_extraction = await self._run_with_job(
                case_id=case_id,
                job_type="EXTRACTION",
                coro_fn=lambda: self._ai.extract_document_data(
                    document_content=document_content,
                    document_name=dms_doc.document_name,
                    document_type=expected_type,
                ),
                timeout=_TIMEOUT_AI_EXTRACT,
            )
            extraction_valid = True
            extraction_error = None
        except Exception as exc:
            log.error("document.extraction_failed", document_id=document_id, error=str(exc))
            raw_extraction = {}
            extraction_valid = False
            extraction_error = str(exc)

        async with AsyncSessionLocal() as session:
            existing_versions = await session.execute(
                select(StageOutputVersion).where(
                    StageOutputVersion.case_document_id == doc_row_id
                )
            )
            version = len(existing_versions.scalars().all()) + 1
            session.add(
                StageOutputVersion(
                    case_document_id=doc_row_id,
                    version=version,
                    raw_extraction=raw_extraction,
                    is_valid=extraction_valid,
                    validation_error=extraction_error,
                )
            )
            await session.execute(
                update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                    status=DocumentStatus.EXTRACTED
                    if extraction_valid
                    else DocumentStatus.EXTRACTION_FAILED
                )
            )
            await session.commit()

        if not extraction_valid:
            return None

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
            log.error("report.generation_failed", application_id=event.application_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(status="FAILED")
                )
                await session.commit()
            return

        html_generated_at = datetime.now(timezone.utc)
        pdf_generated_at = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    html_content=html,
                    narrative=narrative,
                    html_generated_at=html_generated_at,
                    pdf_generated_at=pdf_generated_at,
                    status="PDF_READY",
                )
            )
            await session.commit()

        # Stage: DMS upload (PDF report)
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
            log.error("report.upload_failed", application_id=event.application_id, error=str(exc))
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(
                        status="UPLOAD_FAILED"
                    )
                )
                session.add(
                    DMSArtifact(
                        case_id=case_id,
                        dms_document_id="",
                        artifact_type="PDF_REPORT",
                        direction="OUTBOUND",
                        status="FAILED",
                        error_details={"error": str(exc)},
                    )
                )
                await session.commit()
            return

        pdf_uploaded_at = datetime.now(timezone.utc)

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    pdf_dms_document_id=pdf_doc_id,
                    pdf_uploaded_at=pdf_uploaded_at,
                    status="COMPLETED",
                )
            )
            session.add(
                DMSArtifact(
                    case_id=case_id,
                    dms_document_id=pdf_doc_id,
                    artifact_type="PDF_REPORT",
                    direction="OUTBOUND",
                    status="SUCCESS",
                )
            )
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
            log.error("edw.export_failed", application_id=event.application_id, error=str(exc))
            # EDW failure does NOT mark the case FAILED — business processing completed.
            # The edw_staging row stays for independent retry.
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
                {
                    "recommendation": str(case_result.recommendation),
                    "edw_confirmation_id": confirmation_id,
                },
                actor=_ACTOR,
            )
            await session.commit()

        log.info(
            "case.completed",
            application_id=event.application_id,
            recommendation=str(case_result.recommendation),
        )

    # ------------------------------------------------------------------ #
    #  Retry wrapper with processing_job tracking                           #
    # ------------------------------------------------------------------ #

    async def _run_with_job(
        self,
        case_id: UUID,
        job_type: str,
        coro_fn,
        timeout: float,
        max_attempts: int = _MAX_ATTEMPTS,
        base_delay: float = _RETRY_BASE_DELAY,
    ):
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
                job_status = "FAILED" if is_last else "RETRYING"
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
                            status=job_status,
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
        session.add(
            AuditEvent(
                case_id=case_id,
                application_id=application_id,
                event_type=event_type,
                actor=actor,
                detail=detail,
            )
        )

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
            session.add(
                DeadLetterEvent(
                    event_id=UUID(str(event_id)) if event_id else None,
                    case_id=UUID(str(case_id)) if case_id else None,
                    application_id=application_id,
                    reason_code=reason_code,
                    error_detail=error,
                    raw_payload=raw_payload,
                    stack_trace=stack_trace,
                )
            )
            await session.commit()
