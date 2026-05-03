"""OpenTelemetry telemetry client for the credit underwriting platform.

This module exposes a single `Telemetry` class that wraps the OpenTelemetry SDK
for traces, metrics, and logs. All telemetry is emitted via OTLP to an
OpenTelemetry-compatible backend (Grafana, Jaeger, Elastic, etc.).

The class is intentionally side-effect free until `setup()` is called: importing
the module does not register any global providers. Once `setup()` runs, the
SDK is initialised and helper methods become live.

This module currently provides scaffolding only — it is not yet wired into the
pipeline. To instrument a stage, call `telemetry.span(...)`,
`telemetry.increment(...)`, or `telemetry.histogram(...)` from the call site.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

# ---------------------------------------------------------------------- #
#  Standard metric and span names                                          #
# ---------------------------------------------------------------------- #
#
# Centralised so every call site emits identical strings. Backend
# dashboards can rely on these names being stable.
#

# Pipeline-level spans
SPAN_PROCESS_EVENT = "pipeline.process_event"
SPAN_PROCESS_CASE = "pipeline.process_case"
SPAN_PROCESS_DOCUMENT = "pipeline.process_document"
SPAN_GENERATE_REPORT = "pipeline.generate_report"

# External-call spans
SPAN_DMS_FETCH = "dms.fetch_document"
SPAN_DMS_UPLOAD = "dms.upload_document"
SPAN_AI_VERIFY_AND_EXTRACT = "ai.verify_and_extract"
SPAN_AI_GENERATE_NARRATIVE = "ai.generate_narrative"

# Counters
COUNTER_EVENTS_CONSUMED = "creditunder.events_consumed"
COUNTER_EVENTS_DUPLICATE = "creditunder.events_duplicate"
COUNTER_CASES_CREATED = "creditunder.cases_created"
COUNTER_CASES_COMPLETED = "creditunder.cases_completed"
COUNTER_CASES_FAILED = "creditunder.cases_failed"
COUNTER_DOCUMENTS_VERIFIED = "creditunder.documents_verified"
COUNTER_VALIDATION_OUTCOMES = "creditunder.validation_outcomes"
COUNTER_DEAD_LETTER = "creditunder.dead_letter_events"

# Histograms (durations in milliseconds)
HISTOGRAM_PIPELINE_MS = "creditunder.pipeline.duration_ms"
HISTOGRAM_AI_CALL_MS = "creditunder.ai.call_duration_ms"
HISTOGRAM_DMS_CALL_MS = "creditunder.dms.call_duration_ms"


class Telemetry:
    """Single entry point for all OpenTelemetry instrumentation.

    Usage:
        telemetry = Telemetry()
        telemetry.setup(service_name="creditunder", otlp_endpoint="http://otel:4317")

        with telemetry.span(SPAN_PROCESS_CASE, {"application_id": app_id}):
            ...
            telemetry.increment(COUNTER_CASES_COMPLETED, {"recommendation": "APPROVE"})
            telemetry.histogram(HISTOGRAM_PIPELINE_MS, duration_ms, {"product_type": "PERSONAL_FINANCE"})
    """

    def __init__(self) -> None:
        self._initialised: bool = False
        self._tracer: trace.Tracer | None = None
        self._meter: metrics.Meter | None = None
        self._counters: dict[str, metrics.Counter] = {}
        self._histograms: dict[str, metrics.Histogram] = {}

    # ------------------------------------------------------------------ #
    #  Setup                                                               #
    # ------------------------------------------------------------------ #

    def setup(
        self,
        service_name: str,
        otlp_endpoint: str,
        environment: str = "production",
        insecure: bool = True,
    ) -> None:
        """Initialise OTel SDK providers and exporters. Idempotent."""
        if self._initialised:
            return

        resource = Resource.create(
            {
                "service.name": service_name,
                "deployment.environment": environment,
            }
        )

        # Tracing
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure))
        )
        trace.set_tracer_provider(tracer_provider)
        self._tracer = trace.get_tracer(service_name)

        # Metrics
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=otlp_endpoint, insecure=insecure)
                )
            ],
        )
        metrics.set_meter_provider(meter_provider)
        self._meter = metrics.get_meter(service_name)

        # Logs (OTel Logs signal — exports via OTLP).
        # Application code keeps using structlog; a LoggingHandler is attached
        # to the stdlib root logger so structlog records are mirrored to OTLP
        # with trace context automatically merged in.
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(
            BatchLogRecordProcessor(OTLPLogExporter(endpoint=otlp_endpoint, insecure=insecure))
        )
        set_logger_provider(logger_provider)
        self._log_handler = LoggingHandler(logger_provider=logger_provider)

        self._initialised = True

    @property
    def log_handler(self) -> LoggingHandler | None:
        """Stdlib logging handler bridging to OTLP logs. Attach to root logger."""
        return getattr(self, "_log_handler", None)

    # ------------------------------------------------------------------ #
    #  Tracing                                                             #
    # ------------------------------------------------------------------ #

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[Span | None]:
        """Context manager that opens a span and records exceptions on it.

        If telemetry is not yet initialised, yields None and is a no-op.
        """
        if self._tracer is None:
            yield None
            return

        with self._tracer.start_as_current_span(name, attributes=attributes or {}) as sp:
            try:
                yield sp
            except Exception as exc:
                sp.record_exception(exc)
                sp.set_status(Status(StatusCode.ERROR, str(exc)))
                raise

    def set_span_attribute(self, key: str, value: Any) -> None:
        """Attach an attribute to the current span (no-op if no active span)."""
        if self._tracer is None:
            return
        sp = trace.get_current_span()
        if sp is not None and sp.is_recording():
            sp.set_attribute(key, value)

    # ------------------------------------------------------------------ #
    #  Metrics                                                             #
    # ------------------------------------------------------------------ #

    def increment(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        value: int = 1,
    ) -> None:
        """Increment a counter. Counter is lazily created on first use."""
        if self._meter is None:
            return
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        counter.add(value, attributes or {})

    def histogram(
        self,
        name: str,
        value: float,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Record a value into a histogram (e.g. duration in ms)."""
        if self._meter is None:
            return
        hist = self._histograms.get(name)
        if hist is None:
            hist = self._meter.create_histogram(name)
            self._histograms[name] = hist
        hist.record(value, attributes or {})

    # ------------------------------------------------------------------ #
    #  Domain-specific convenience helpers                                  #
    # ------------------------------------------------------------------ #
    #
    # These wrap `increment`/`histogram` with the standardised attribute set
    # for each business event, so call sites stay short.
    #

    def record_event_consumed(self, product_type: str | None) -> None:
        self.increment(COUNTER_EVENTS_CONSUMED, {"product_type": product_type or "UNKNOWN"})

    def record_event_duplicate(self) -> None:
        self.increment(COUNTER_EVENTS_DUPLICATE)

    def record_case_created(self, product_type: str) -> None:
        self.increment(COUNTER_CASES_CREATED, {"product_type": product_type})

    def record_case_completed(
        self,
        product_type: str,
        recommendation: str,
        duration_ms: float,
        manual_review_required: bool,
    ) -> None:
        attrs = {
            "product_type": product_type,
            "recommendation": recommendation,
            "manual_review_required": str(manual_review_required).lower(),
        }
        self.increment(COUNTER_CASES_COMPLETED, attrs)
        self.histogram(HISTOGRAM_PIPELINE_MS, duration_ms, attrs)

    def record_case_failed(self, product_type: str, reason_code: str) -> None:
        self.increment(
            COUNTER_CASES_FAILED, {"product_type": product_type, "reason_code": reason_code}
        )

    def record_document_verified(
        self, document_type: str, verification_passed: bool, confidence: float
    ) -> None:
        self.increment(
            COUNTER_DOCUMENTS_VERIFIED,
            {
                "document_type": document_type,
                "verification_passed": str(verification_passed).lower(),
                "confidence_bucket": _confidence_bucket(confidence),
            },
        )

    def record_validation_outcome(self, rule_code: str, outcome: str) -> None:
        self.increment(
            COUNTER_VALIDATION_OUTCOMES, {"rule_code": rule_code, "outcome": outcome}
        )

    def record_ai_call(
        self, call_type: str, duration_ms: float, success: bool
    ) -> None:
        self.histogram(
            HISTOGRAM_AI_CALL_MS,
            duration_ms,
            {"call_type": call_type, "success": str(success).lower()},
        )

    def record_dms_call(
        self, direction: str, duration_ms: float, success: bool
    ) -> None:
        self.histogram(
            HISTOGRAM_DMS_CALL_MS,
            duration_ms,
            {"direction": direction, "success": str(success).lower()},
        )

    def record_dead_letter(self, reason_code: str) -> None:
        self.increment(COUNTER_DEAD_LETTER, {"reason_code": reason_code})


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.9:
        return "very_high"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.6:
        return "medium"
    if confidence >= 0.4:
        return "low"
    return "very_low"


# Module-level singleton. Call `telemetry.setup(...)` once at startup, then
# import `telemetry` from anywhere in the codebase.
telemetry = Telemetry()
