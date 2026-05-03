import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar
from uuid import UUID

import structlog
from aiokafka import AIOKafkaConsumer
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from creditunder.config import settings
from creditunder.db.models import (
    ApplicationCase,
    AuditEvent,
    CaseDocument,
    CaseReport,
    CaseResultRow,
    DeadLetterEvent,
    DMSArtifact,
    EdwStaging,
    InboundApplicationEvent,
    ProcessingJob,
    StageOutputVersion,
    ValidationResultRow,
)
from creditunder.db.session import AsyncSessionLocal
from creditunder.domain.enums import (
    CaseReportStatus,
    CaseStatus,
    DocumentStatus,
    DocumentType,
    InboundEventStatus,
    JobStatus,
    JobType,
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

log = structlog.get_logger(__name__)

_ACTOR = "application_processor"

# Per-stage timeout budgets (seconds) — must sum well within 30s SLA.
_TIMEOUT_DMS_FETCH = 10.0
_TIMEOUT_AI_VERIFY_AND_EXTRACT = 25.0
_TIMEOUT_AI_NARRATIVE = 20.0
_TIMEOUT_DMS_UPLOAD = 10.0

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
        )
        self._dms = DMSClient(settings.dms_base_url)
        self._report_gen = ReportGenerator(self._ai, self._dms)

    # ------------------------------------------------------------------ #
    #  Kafka consumer loop                                                  #
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        await self._recover_stuck_cases()

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
                # Top-level guard: never let one bad message stop the consumer.
                # Inner code already routes failures to dead_letter_event
                # and surfaces them on the relevant case row.
                try:
                    await self._handle_message(message.value)
                except Exception as exc:  # noqa: BLE001
                    log.error(
                        "processor.unhandled_exception",
                        error=str(exc),
                        stack=traceback.format_exc(),
                    )
                await consumer.commit()
        finally:
            await consumer.stop()

    # ------------------------------------------------------------------ #
    #  Startup recovery                                                     #
    # ------------------------------------------------------------------ #

    async def _recover_stuck_cases(self) -> None:
        """On every restart, mark any CREATED or IN_PROGRESS cases as FAILED.

        These states are non-terminal only while the processor is actively
        working on the case. If a case is still in one of these states at
        startup time it means the previous processor run was killed (OOM,
        SIGKILL, pod eviction, etc.) before it could finalise the case.
        The case cannot be safely resumed — we don't know which stage it
        reached — so we fail it immediately so ops can see it and resubmit.
        """
        stuck_statuses = [CaseStatus.CREATED.value, CaseStatus.IN_PROGRESS.value]
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ApplicationCase).where(
                    ApplicationCase.status.in_(stuck_statuses)
                )
            )
            stuck = result.scalars().all()
            if not stuck:
                log.info("processor.recovery.no_stuck_cases")
                return

            log.warning(
                "processor.recovery.found_stuck_cases",
                count=len(stuck),
                application_ids=[c.application_id for c in stuck],
            )
            for case in stuck:
                await session.execute(
                    update(ApplicationCase).where(ApplicationCase.id == case.id).values(
                        status=CaseStatus.FAILED,
                        error_detail=(
                            f"Case was in {case.status} state when the processor restarted. "
                            "The processing run did not complete. Resubmit the application."
                        ),
                    )
                )
                await self._audit(
                    session,
                    case.id,
                    case.application_id,
                    "CASE_FAILED",
                    {"error": "Processor restart — non-terminal state recovered to FAILED"},
                    actor=_ACTOR,
                )
            await session.commit()
            log.info("processor.recovery.done", recovered=len(stuck))

        # Sync edw_staging for any recovered cases that had a pending row.
        for case in stuck:
            try:
                await self._upsert_edw_staging_status(case.id)
            except SQLAlchemyError as exc:
                log.warning(
                    "edw_staging.recovery_update_failed",
                    application_id=case.application_id,
                    error=f"{type(exc).__name__}: {exc}",
                )

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
                    status=InboundEventStatus.RECEIVED,
                ))
                await session.flush()
            except IntegrityError:
                await session.rollback()
                log.info("event.duplicate", event_id=str(event.event_id))
                return

            case_row = await self._get_or_create_case(session, event)
            # Inbound event moves to PROCESSING once the case is in flight.
            await session.execute(
                update(InboundApplicationEvent)
                .where(InboundApplicationEvent.event_id == event.event_id)
                .values(status=InboundEventStatus.PROCESSING)
            )
            await session.commit()

        case_id = case_row.id
        try:
            await self._process_case(event, case_id)
            # Successful business processing — mark the inbound event COMPLETED.
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(InboundApplicationEvent)
                    .where(InboundApplicationEvent.event_id == event.event_id)
                    .values(status=InboundEventStatus.COMPLETED)
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001
            # Catastrophic, unhandled failure inside the pipeline. Capture it
            # on the case row so it is never silently swallowed, send to
            # dead_letter_event for replay, and surface via inbound status.
            error_msg = f"{type(exc).__name__}: {exc}"
            stack = traceback.format_exc()
            log.error(
                "case.unhandled_exception",
                application_id=event.application_id,
                error=error_msg,
                stack=stack,
            )
            await self._fail_case(case_id, event.application_id, error_msg)
            await self._dead_letter(
                event_id=str(event.event_id),
                application_id=event.application_id,
                case_id=case_id,
                reason_code="UNHANDLED_PIPELINE_EXCEPTION",
                error=error_msg,
                raw_payload=event.model_dump(mode="json"),
                stack_trace=stack,
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(InboundApplicationEvent)
                    .where(InboundApplicationEvent.event_id == event.event_id)
                    .values(status=InboundEventStatus.FAILED)
                )
                await session.commit()

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
            status=CaseStatus.CREATED,
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
            await self._fail_case(
                case_id,
                event.application_id,
                f"Unsupported product type: {event.product_type}. {exc}",
            )
            await self._dead_letter(
                event_id=str(event.event_id),
                application_id=event.application_id,
                case_id=case_id,
                reason_code="UNSUPPORTED_PRODUCT_TYPE",
                error=str(exc),
                raw_payload=event.model_dump(mode="json"),
            )
            return

        # Transition CREATED → IN_PROGRESS now that the handler is engaged.
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.IN_PROGRESS
                )
            )
            await session.commit()

        # Write a pending row to edw_staging immediately so the workbench
        # board shows the case as IN_PROGRESS rather than invisible. The full
        # denormalised payload is written (upserted) at completion.
        try:
            await self._upsert_edw_staging_pending(event, case_id)
        except SQLAlchemyError as exc:
            log.warning(
                "edw_staging.pending_write_failed",
                application_id=event.application_id,
                error=f"{type(exc).__name__}: {exc}",
            )

        # Fetch + verify + extract every document concurrently.
        results = await asyncio.gather(*[
            self._process_document(event, case_id, doc_id) for doc_id in event.document_ids
        ])
        document_results: list[DocumentResult] = [r for r in results if r is not None]

        # Completeness check — required document types must be represented.
        # The handler resolves the required set from applicant_data so that
        # employer-class-driven document matrices (BR-13/BR-14) are honoured.
        required_set = handler.required_documents(event.applicant_data)
        missing = required_set.required - {dr.document_type for dr in document_results}
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

        report_ok = await self._generate_and_deliver_report(
            event, case_id, case_result, required_set
        )

        if report_ok:
            now = datetime.now(timezone.utc)
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                        completed_at=now,
                    )
                )
                await self._audit(
                    session,
                    case_id,
                    event.application_id,
                    "CASE_COMPLETED",
                    {"recommendation": _enum_val(recommendation)},
                    actor=_ACTOR,
                )
                await session.commit()

            # Write the denormalised workbench snapshot. All upstream writes
            # (validation rows, case status, report upload, completed_at) are
            # committed above, so the snapshot is consistent. We catch
            # SQLAlchemy errors (replication/connection issues) and let
            # everything else propagate so programming errors surface in dev.
            try:
                await self._write_edw_staging(event.application_id, case_id, now)
            except SQLAlchemyError as exc:
                log.error(
                    "edw_staging.write_failed",
                    application_id=event.application_id,
                    error=f"{type(exc).__name__}: {exc}",
                )

            log.info(
                "case.completed",
                application_id=event.application_id,
                recommendation=_enum_val(recommendation),
            )
        else:
            log.warning(
                "case.completed_with_delivery_errors",
                application_id=event.application_id,
                report_ok=report_ok,
            )

    async def _handle_missing_documents(
        self, event: ApplicationEvent, case_id: UUID, missing: set
    ) -> None:
        missing_names = ", ".join(str(t) for t in missing)
        log.warning(
            "case.missing_required_documents",
            application_id=event.application_id,
            missing=missing_names,
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.MANUAL_INTERVENTION_REQUIRED,
                    error_detail=f"Missing required document types: {missing_names}",
                )
            )
            await self._audit(
                session, case_id, event.application_id, "MISSING_REQUIRED_DOCUMENTS",
                {"missing": missing_names}, actor=_ACTOR,
            )
            await session.commit()
        await self._dead_letter(
            event_id=str(event.event_id),
            application_id=event.application_id,
            case_id=case_id,
            reason_code="MISSING_REQUIRED_DOCUMENTS",
            error=f"Required document types not found: {missing_names}",
            raw_payload=event.model_dump(mode="json"),
        )
        try:
            await self._upsert_edw_staging_status(case_id)
        except SQLAlchemyError as exc:
            log.warning(
                "edw_staging.status_update_failed",
                application_id=event.application_id,
                error=f"{type(exc).__name__}: {exc}",
            )

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
                    rule_version=vr.rule_version,
                    config_version=vr.config_version,
                ))

            session.add(CaseResultRow(
                case_id=case_id,
                recommendation=rec_str,
                manual_review_required=manual_review_required,
                completed_at=completed_at,
            ))

            # Mark the case COMPLETED — business processing is done. Delivery
            # state (report upload) is tracked separately on case_report.
            # application_case.completed_at is
            # only set later, once delivery succeeds.
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.COMPLETED,
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
                job_type=JobType.DOCUMENT_FETCH,
                coro_fn=lambda: self._dms.fetch_document(document_id),
                timeout=_TIMEOUT_DMS_FETCH,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error("document.fetch_failed", document_id=document_id, error=error_msg)
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.EXTRACTION_FAILED,
                        error_detail=f"DMS fetch failed: {error_msg}",
                    )
                )
                session.add(DMSArtifact(
                    case_id=case_id,
                    dms_document_id=document_id,
                    artifact_type="SOURCE_DOCUMENT",
                    direction="INBOUND",
                    status="FAILED",
                    error_details={"error": error_msg},
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
            await self._audit(
                session, case_id, event.application_id, "DOCUMENT_FETCHED",
                {
                    "dms_document_id": document_id,
                    "document_type": dms_doc.document_type,
                },
                actor=_ACTOR,
            )
            await session.commit()

        try:
            expected_type = DocumentType(dms_doc.document_type)
        except ValueError:
            error_msg = f"Unknown document type from DMS: {dms_doc.document_type}"
            log.warning(
                "document.unknown_type",
                document_id=document_id,
                doc_type=dms_doc.document_type,
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.EXTRACTION_FAILED,
                        error_detail=error_msg,
                    )
                )
                await session.commit()
            return None

        # Stage 2: AI verify + extract (single LLM call).
        try:
            ai_result = await self._run_with_job(
                case_id=case_id,
                job_type=JobType.VERIFY_AND_EXTRACT,
                coro_fn=lambda: self._ai.verify_and_extract(
                    document_content=dms_doc.content,
                    document_content_type=dms_doc.content_type,
                    document_name=dms_doc.document_name,
                    expected_type=expected_type,
                ),
                timeout=_TIMEOUT_AI_VERIFY_AND_EXTRACT,
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error(
                "document.verify_and_extract_error",
                document_id=document_id,
                error=error_msg,
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseDocument).where(CaseDocument.id == doc_row_id).values(
                        status=DocumentStatus.VERIFICATION_FAILED,
                        error_detail=f"AI verify+extract failed: {error_msg}",
                    )
                )
                await session.commit()
            # Re-raise: this is a technical failure (AI error, timeout, etc.),
            # not a business outcome. The exception propagates through
            # asyncio.gather → _process_case → _handle_message which calls
            # _fail_case, so the case lands on FAILED rather than COMPLETED/DECLINE.
            # TYPE_MISMATCH (AI ran fine but wrong doc type) is handled below —
            # that IS a business case and does produce a HARD_BREACH.
            raise

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
                await self._audit(
                    session, case_id, event.application_id, "EXTRACTION_COMPLETED",
                    {
                        "dms_document_id": document_id,
                        "document_type": expected_type.value,
                        "field_count": len(ai_result.extracted_fields),
                        "verification_confidence": ai_result.verification_confidence,
                    },
                    actor=_ACTOR,
                )
            else:
                new_status = DocumentStatus.TYPE_MISMATCH
                await self._audit(
                    session, case_id, event.application_id, "DOCUMENT_TYPE_MISMATCH",
                    {
                        "dms_document_id": document_id,
                        "expected_type": expected_type.value,
                        "detected_type": ai_result.detected_type,
                        "verification_confidence": ai_result.verification_confidence,
                    },
                    actor=_ACTOR,
                )

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
        self,
        event: ApplicationEvent,
        case_id: UUID,
        case_result: CaseResult,
        required_set,
    ) -> bool:
        """Returns True iff HTML+PDF were produced AND uploaded to DMS."""
        async with AsyncSessionLocal() as session:
            report_row = CaseReport(case_id=case_id, status=CaseReportStatus.PENDING)
            session.add(report_row)
            await session.flush()
            report_id = report_row.id
            await session.commit()

        # Fetch audit timeline so the report can include it.
        async with AsyncSessionLocal() as session:
            audit_rows = (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.case_id == case_id)
                    .order_by(AuditEvent.occurred_at.asc())
                )
            ).scalars().all()
        audit_timeline = [
            {
                "event_type": a.event_type,
                "actor": a.actor,
                "detail": a.detail,
                "occurred_at": a.occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC") if a.occurred_at else "—",
            }
            for a in audit_rows
        ]

        try:
            html, pdf_bytes, narrative = await self._run_with_job(
                case_id=case_id,
                job_type=JobType.REPORT_GENERATION,
                coro_fn=lambda: self._report_gen.generate(
                    case_result=case_result,
                    applicant_data=event.applicant_data,
                    branch_name=event.branch_name,
                    validator_id=event.validator_id,
                    required_documents=required_set,
                    audit_timeline=audit_timeline,
                ),
                timeout=_TIMEOUT_AI_NARRATIVE + 10.0,  # narrative + rendering budget
            )
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error(
                "report.generation_failed",
                application_id=event.application_id,
                error=error_msg,
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(
                        status=CaseReportStatus.FAILED,
                        error_detail=f"Report generation failed: {error_msg}",
                    )
                )
                await session.commit()
            return False

        ready_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    html_content=html,
                    narrative=narrative,
                    html_generated_at=ready_at,
                    pdf_generated_at=ready_at,
                    status=CaseReportStatus.PDF_READY,
                )
            )
            await session.commit()

        # Stage: DMS upload (PDF report).
        try:
            pdf_doc_id = await self._run_with_job(
                case_id=case_id,
                job_type=JobType.REPORT_UPLOAD,
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
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error(
                "report.upload_failed",
                application_id=event.application_id,
                error=error_msg,
            )
            async with AsyncSessionLocal() as session:
                # Status stays at PDF_READY but moves to FAILED with
                # error_detail set — the HTML and PDF are persisted, so
                # the upload can be retried independently from the staging
                # state without re-running the pipeline. The failure is
                # already recorded on processing_job.last_error; we don't
                # write a sentinel DMSArtifact row because dms_document_id
                # is NOT NULL and there is no document id to record.
                await session.execute(
                    update(CaseReport).where(CaseReport.id == report_id).values(
                        status=CaseReportStatus.FAILED,
                        error_detail=f"DMS upload failed: {error_msg}",
                    )
                )
                await session.commit()
            return False

        uploaded_at = datetime.now(timezone.utc)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(CaseReport).where(CaseReport.id == report_id).values(
                    pdf_dms_document_id=pdf_doc_id,
                    pdf_uploaded_at=uploaded_at,
                    status=CaseReportStatus.UPLOADED,
                    error_detail=None,
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
        return True

    # ------------------------------------------------------------------ #
    #  EDW staging                                                          #
    # ------------------------------------------------------------------ #

    async def _upsert_edw_staging_pending(
        self, event: ApplicationEvent, case_id: UUID
    ) -> None:
        """Insert a minimal IN_PROGRESS row so the case appears on the board immediately.

        Uses ON CONFLICT DO NOTHING — if the row already exists (e.g. resubmission
        of a previously completed case) we leave the existing full payload intact.
        """
        applicant_data = event.applicant_data or {}
        applicant_name = applicant_data.get("name") if isinstance(applicant_data, dict) else None
        now = datetime.now(timezone.utc)

        pending_payload = {
            "case": {
                "id": str(case_id),
                "application_id": event.application_id,
                "applicant_name": applicant_name,
                "product_type": event.product_type,
                "branch_name": event.branch_name,
                "validator_id": event.validator_id,
                "supervisor_id": event.supervisor_id,
                "status": CaseStatus.IN_PROGRESS.value,
                "recommendation": None,
                "recommendation_rationale": None,
                "manual_review_required": False,
                "error_detail": None,
                "applicant_data": applicant_data,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "completed_at": None,
            },
            "employer_snapshot": (
                applicant_data.get("employer_snapshot")
                if isinstance(applicant_data, dict) else None
            ),
            "documents": [],
            "validations": {
                "hard_breach": [], "soft_mismatch": [],
                "low_confidence": [], "manual_review": [],
            },
            "manual_checks": [],
            "technical_exceptions": [],
            "report": {
                "status": None,
                "pdf_available": False,
                "pdf_uploaded_at": None,
                "error_detail": None,
            },
            "audit_timeline": [],
        }

        async with AsyncSessionLocal() as session:
            stmt = pg_insert(EdwStaging).values(
                case_id=case_id,
                application_id=event.application_id,
                validator_id=event.validator_id,
                supervisor_id=event.supervisor_id,
                product_type=event.product_type,
                branch_name=event.branch_name,
                applicant_name=applicant_name,
                status=CaseStatus.IN_PROGRESS.value,
                recommendation=None,
                manual_review_required=False,
                pdf_dms_document_id=None,
                error_detail=None,
                created_at=now,
                updated_at=now,
                completed_at=None,
                payload=pending_payload,
                export_status="PENDING",
                staged_at=now,
            ).on_conflict_do_nothing(constraint="uq_edw_staging_case_id")
            await session.execute(stmt)
            await session.commit()
        log.info("edw_staging.pending_written", application_id=event.application_id)

    async def _upsert_edw_staging_status(self, case_id: UUID) -> None:
        """Update the promoted status columns on an existing edw_staging row.

        Called after FAILED or MANUAL_INTERVENTION_REQUIRED transitions so the
        board reflects the terminal state even when the full payload write is
        skipped (terminal failures have no complete pipeline data to snapshot).
        No-op if no row exists yet (e.g. pending write itself failed).
        """
        async with AsyncSessionLocal() as session:
            case_row = await session.scalar(
                select(ApplicationCase).where(ApplicationCase.id == case_id)
            )
            if case_row is None:
                return

            now = datetime.now(timezone.utc)
            existing = await session.scalar(
                select(EdwStaging).where(EdwStaging.case_id == case_id)
            )
            if existing is None:
                return

            # Patch the status/error columns in both the promoted columns and payload.
            payload = dict(existing.payload)
            case_block = dict(payload.get("case", {}))
            case_block["status"] = case_row.status
            case_block["error_detail"] = case_row.error_detail
            case_block["updated_at"] = now.isoformat()
            payload["case"] = case_block

            # Surface error as a technical exception so the workbench detail
            # view shows it consistently even before a full snapshot is written.
            if case_row.error_detail:
                tex = payload.get("technical_exceptions", [])
                already = any(
                    t.get("kind") == "PIPELINE_FAILURE" for t in tex
                )
                if not already:
                    tex = [{"kind": "PIPELINE_FAILURE",
                            "description": case_row.error_detail,
                            "reference": f"case status: {case_row.status}"}] + tex
                    payload["technical_exceptions"] = tex

            await session.execute(
                update(EdwStaging).where(EdwStaging.case_id == case_id).values(
                    status=case_row.status,
                    error_detail=case_row.error_detail,
                    updated_at=now,
                    payload=payload,
                )
            )
            await session.commit()
        log.info("edw_staging.status_updated", case_id=str(case_id), status=case_row.status)

    async def _write_edw_staging(
        self,
        application_id: str,
        case_id: UUID,
        completed_at: datetime,
    ) -> None:
        """Build and persist the full denormalised workbench snapshot.

        Queries all pipeline rows for the case (all committed by the time
        this runs) and writes a single EdwStaging row. The workbench reads
        only from this table — it never touches the pipeline tables directly.
        """
        async with AsyncSessionLocal() as session:
            case_row = await session.scalar(
                select(ApplicationCase).where(ApplicationCase.id == case_id)
            )
            doc_rows = (
                await session.execute(
                    select(CaseDocument)
                    .where(CaseDocument.case_id == case_id)
                    .options(selectinload(CaseDocument.extraction_versions))
                )
            ).scalars().all()
            val_rows = (
                await session.execute(
                    select(ValidationResultRow).where(ValidationResultRow.case_id == case_id)
                )
            ).scalars().all()
            audit_rows = (
                await session.execute(
                    select(AuditEvent)
                    .where(AuditEvent.case_id == case_id)
                    .order_by(AuditEvent.occurred_at.asc())
                )
            ).scalars().all()
            report_row = await session.scalar(
                select(CaseReport).where(CaseReport.case_id == case_id)
            )

            applicant_data = case_row.applicant_data or {}
            if not isinstance(applicant_data, dict):
                applicant_data = {}
            applicant_name_val = applicant_data.get("name")
            employer_snapshot = applicant_data.get("employer_snapshot")

            # --- documents ------------------------------------------------
            documents = []
            for d in doc_rows:
                if d.extraction_versions:
                    latest = max(d.extraction_versions, key=lambda v: v.version)
                    raw = latest.raw_extraction or {}
                    extracted_fields = [
                        {
                            "label": ef.get("normalized_label") or key,
                            "value": ef.get("value"),
                            "confidence": ef.get("confidence"),
                            "page_reference": ef.get("page_reference"),
                        }
                        for key, ef in raw.items()
                        if isinstance(ef, dict)
                    ]
                else:
                    extracted_fields = []
                documents.append({
                    "id": str(d.id),
                    "dms_document_id": d.dms_document_id,
                    "document_name": d.document_name,
                    "document_type": d.document_type,
                    "status": d.status,
                    "verification_passed": d.verification_passed,
                    "verification_confidence": d.verification_confidence,
                    "fetched_at": d.fetched_at.isoformat() if d.fetched_at else None,
                    "verified_at": d.verified_at.isoformat() if d.verified_at else None,
                    "error_detail": d.error_detail,
                    "extracted_fields": extracted_fields,
                })

            # --- validations (pre-grouped) --------------------------------
            valids: dict[str, list] = {
                "passed": [], "hard_breach": [], "soft_mismatch": [],
                "low_confidence": [], "manual_review": [],
            }
            for v in val_rows:
                entry = {
                    "id": str(v.id),
                    "rule_code": v.rule_code,
                    "outcome": v.outcome,
                    "description": v.description,
                    "field_name": v.field_name,
                    "extracted_value": v.extracted_value,
                    "expected_value": v.expected_value,
                    "confidence": v.confidence,
                    "manual_review_required": v.manual_review_required,
                    "rule_version": v.rule_version,
                    "config_version": v.config_version,
                    "evaluated_at": v.evaluated_at.isoformat() if v.evaluated_at else None,
                }
                match v.outcome:
                    case "PASS":
                        valids["passed"].append(entry)
                    case "HARD_BREACH":
                        valids["hard_breach"].append(entry)
                    case "SOFT_MISMATCH":
                        valids["soft_mismatch"].append(entry)
                    case "LOW_CONFIDENCE":
                        valids["low_confidence"].append(entry)
                    case "MANUAL_REVIEW_REQUIRED":
                        valids["manual_review"].append(entry)

            # --- manual checks --------------------------------------------
            manual_checks = []
            seen_codes: set[str] = set()
            for v in val_rows:
                if not (v.manual_review_required or v.outcome == "MANUAL_REVIEW_REQUIRED"):
                    continue
                if v.rule_code in seen_codes:
                    continue
                seen_codes.add(v.rule_code)
                manual_checks.append({
                    "rule_code": v.rule_code,
                    "description": v.description,
                    "field_name": v.field_name,
                    "reference": None,
                    "kind": "RULE",
                })
            for d in doc_rows:
                manual_checks.append({
                    "rule_code": "STAMP_AND_SIGNATURE_REVIEW",
                    "description": (
                        "Visual confirmation of stamp and signature against the "
                        "reference record. The system does not render this verdict."
                    ),
                    "field_name": None,
                    "reference": (
                        f"{d.document_type or '—'} — {d.document_name or d.dms_document_id}"
                    ),
                    "kind": "STAMP_AND_SIGNATURE",
                })

            # --- technical exceptions -------------------------------------
            tech_exceptions = []
            if case_row.error_detail:
                tech_exceptions.append({
                    "kind": "PIPELINE_FAILURE",
                    "description": case_row.error_detail,
                    "reference": f"case status: {case_row.status}",
                })
            for d in doc_rows:
                if d.error_detail:
                    tech_exceptions.append({
                        "kind": "DOCUMENT_FAILURE",
                        "description": d.error_detail,
                        "reference": f"{d.document_type or '—'} — {d.dms_document_id}",
                    })
            if report_row and report_row.error_detail:
                tech_exceptions.append({
                    "kind": "REPORT_FAILURE",
                    "description": report_row.error_detail,
                    "reference": f"report status: {report_row.status}",
                })

            # --- audit timeline ------------------------------------------
            audit_timeline = [
                {
                    "id": str(a.id),
                    "event_type": a.event_type,
                    "actor": a.actor,
                    "detail": a.detail,
                    "occurred_at": a.occurred_at.isoformat() if a.occurred_at else None,
                }
                for a in audit_rows
            ]

            # --- full payload --------------------------------------------
            payload = {
                "case": {
                    "id": str(case_row.id),
                    "application_id": case_row.application_id,
                    "applicant_name": applicant_name_val,
                    "product_type": case_row.product_type,
                    "branch_name": case_row.branch_name,
                    "validator_id": case_row.validator_id,
                    "supervisor_id": case_row.supervisor_id,
                    "status": case_row.status,
                    "recommendation": case_row.recommendation,
                    "recommendation_rationale": case_row.recommendation_rationale,
                    "manual_review_required": case_row.manual_review_required,
                    "error_detail": case_row.error_detail,
                    "applicant_data": applicant_data,
                    "created_at": (
                        case_row.created_at.isoformat() if case_row.created_at else None
                    ),
                    "updated_at": completed_at.isoformat(),
                    "completed_at": completed_at.isoformat(),
                },
                "employer_snapshot": employer_snapshot,
                "documents": documents,
                "validations": valids,
                "manual_checks": manual_checks,
                "technical_exceptions": tech_exceptions,
                "report": {
                    "status": report_row.status if report_row else None,
                    "pdf_available": bool(report_row and report_row.pdf_dms_document_id),
                    "pdf_uploaded_at": (
                        report_row.pdf_uploaded_at.isoformat()
                        if report_row and report_row.pdf_uploaded_at
                        else None
                    ),
                    "error_detail": report_row.error_detail if report_row else None,
                },
                "audit_timeline": audit_timeline,
            }

            pdf_dms_doc_id = report_row.pdf_dms_document_id if report_row else None
            now = datetime.now(timezone.utc)

            # Upsert: the pending row written at IN_PROGRESS is updated in-place
            # with the full denormalised snapshot. ON CONFLICT updates every column
            # so the workbench immediately sees the completed state.
            stmt = pg_insert(EdwStaging).values(
                case_id=case_id,
                application_id=case_row.application_id,
                validator_id=case_row.validator_id,
                supervisor_id=case_row.supervisor_id,
                product_type=case_row.product_type,
                branch_name=case_row.branch_name,
                applicant_name=applicant_name_val,
                status=case_row.status,
                recommendation=case_row.recommendation,
                manual_review_required=case_row.manual_review_required,
                pdf_dms_document_id=pdf_dms_doc_id,
                error_detail=case_row.error_detail,
                created_at=case_row.created_at,
                updated_at=completed_at,
                completed_at=completed_at,
                payload=payload,
                export_status="STAGED",
                staged_at=now,
            ).on_conflict_do_update(
                constraint="uq_edw_staging_case_id",
                set_={
                    "status": case_row.status,
                    "recommendation": case_row.recommendation,
                    "manual_review_required": case_row.manual_review_required,
                    "pdf_dms_document_id": pdf_dms_doc_id,
                    "error_detail": case_row.error_detail,
                    "updated_at": completed_at,
                    "completed_at": completed_at,
                    "payload": payload,
                    "export_status": "STAGED",
                    "staged_at": now,
                },
            )
            await session.execute(stmt)
            await session.commit()

        log.info("edw_staging.written", application_id=application_id)

    # ------------------------------------------------------------------ #
    #  Failure helpers                                                      #
    # ------------------------------------------------------------------ #

    async def _fail_case(self, case_id: UUID, application_id: str, error: str) -> None:
        """Mark a case FAILED and capture the error. Never silent."""
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ApplicationCase).where(ApplicationCase.id == case_id).values(
                    status=CaseStatus.FAILED,
                    error_detail=error,
                )
            )
            await self._audit(
                session, case_id, application_id, "CASE_FAILED",
                {"error": error}, actor=_ACTOR,
            )
            await session.commit()

        try:
            await self._upsert_edw_staging_status(case_id)
        except SQLAlchemyError as exc:
            log.warning(
                "edw_staging.status_update_failed",
                application_id=application_id,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------ #
    #  Retry wrapper with processing_job tracking                           #
    # ------------------------------------------------------------------ #

    async def _run_with_job(
        self,
        case_id: UUID,
        job_type,
        coro_fn: Callable[[], Awaitable[T]],
        timeout: float,
        max_attempts: int = _MAX_ATTEMPTS,
        base_delay: float = _RETRY_BASE_DELAY,
    ) -> T:
        job_type_str = _enum_val(job_type)
        async with AsyncSessionLocal() as session:
            job = ProcessingJob(
                case_id=case_id,
                job_type=job_type_str,
                status=JobStatus.IN_PROGRESS,
                max_attempts=max_attempts,
                attempt_count=1,
                last_attempted_at=datetime.now(timezone.utc),
            )
            session.add(job)
            await session.flush()
            job_id = job.id
            await session.commit()

        if max_attempts < 1:
            raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                result = await asyncio.wait_for(coro_fn(), timeout=timeout)
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                            status=JobStatus.COMPLETED,
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
                    job_type=job_type_str,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(exc),
                )
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(ProcessingJob).where(ProcessingJob.id == job_id).values(
                            status=JobStatus.FAILED if is_last else JobStatus.RETRYING,
                            attempt_count=attempt,
                            last_error=str(exc),
                            last_attempted_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
                if not is_last:
                    await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

        # last_exc is guaranteed non-None: max_attempts >= 1 so the loop body
        # ran at least once, and the only way to exit without `return`-ing is
        # via the except branch, which always assigns last_exc.
        raise last_exc  # type: ignore[misc]

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
