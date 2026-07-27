import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, ClassVar

from langsmith import Client
from opentelemetry import context, metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def _is_enabled() -> bool:
    return os.getenv("LANGSMITH_TRACING", "false").lower() == "true"


def _is_otel_enabled() -> bool:
    return os.getenv("SACM_OTEL_ENABLED", "false").lower() == "true"


def _cost_per_token(provider: str, token_type: str) -> float | None:
    if provider == "openai" and token_type == "input":
        variable_name = "SACM_OPENAI_EMBEDDING_INPUT_COST_PER_MILLION_USD"
    else:
        provider_name = provider.upper().replace("-", "_")
        variable_name = f"SACM_{provider_name}_{token_type.upper()}_COST_PER_MILLION_USD"
    raw_value = os.getenv(variable_name, "")
    if not raw_value:
        return None

    try:
        price_per_million = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{variable_name} must be a number.") from exc
    if price_per_million < 0:
        raise ValueError(f"{variable_name} cannot be negative.")
    return price_per_million / 1_000_000


def _embedding_cost_per_token() -> float | None:
    return _cost_per_token("openai", "input")


def estimate_usage_cost(
    provider: str, input_tokens: int, output_tokens: int = 0
) -> float | None:
    input_price = _cost_per_token(provider, "input")
    output_price = _cost_per_token(provider, "output")
    if input_price is None and output_price is None:
        return None
    return input_tokens * (input_price or 0.0) + output_tokens * (output_price or 0.0)


@dataclass
class OpenTelemetryService:
    """Opt-in OTLP instrumentation using only non-sensitive operational data."""

    enabled: bool
    _configuration_lock: ClassVar[Lock] = Lock()
    _configured: ClassVar[bool] = False

    def __post_init__(self) -> None:
        if self.enabled:
            self._configure()
        self.tracer = trace.get_tracer("sacm-agent-runtime")
        meter = metrics.get_meter("sacm-agent-runtime")
        self.token_counter = meter.create_counter(
            "sacm.gen_ai.tokens",
            unit="{token}",
            description="Tokens reported by generative AI providers.",
        )
        self.cost_counter = meter.create_counter(
            "sacm.gen_ai.estimated_cost",
            unit="USD",
            description="Estimated generative AI cost from configured provider prices.",
        )
        self.tool_duration = meter.create_histogram(
            "sacm.tool.duration",
            unit="ms",
            description="Duration of SACM tool executions.",
        )
        self.tool_counter = meter.create_counter(
            "sacm.tool.executions",
            unit="{execution}",
            description="SACM tool execution count.",
        )

    @classmethod
    def _configure(cls) -> None:
        with cls._configuration_lock:
            if cls._configured:
                return

            resource = Resource.create(
                {SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "sacm-agent-runtime")}
            )
            tracer_provider = TracerProvider(resource=resource)
            tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            meter_provider = MeterProvider(
                resource=resource,
                metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
            )
            trace.set_tracer_provider(tracer_provider)
            metrics.set_meter_provider(meter_provider)
            cls._configured = True

    def start_span(self, name: str, attributes: dict[str, Any]) -> tuple[Any, object]:
        span = self.tracer.start_span(name, attributes=attributes)
        return span, context.attach(trace.set_span_in_context(span))

    def record_model_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        operation: str,
        output_tokens: int = 0,
    ) -> float | None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative.")
        if not self.enabled:
            return estimate_usage_cost(provider, input_tokens, output_tokens)

        attributes: dict[str, str | bool] = {
            "gen_ai.system": provider,
            "gen_ai.request.model": model,
            "sacm.gen_ai.operation": operation,
        }
        estimated_cost = estimate_usage_cost(provider, input_tokens, output_tokens)
        attributes["sacm.gen_ai.cost_estimation_available"] = estimated_cost is not None

        current_span = trace.get_current_span()
        current_span.set_attribute("gen_ai.system", provider)
        current_span.set_attribute("gen_ai.request.model", model)
        current_span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        current_span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        current_span.set_attribute(
            "sacm.gen_ai.cost_estimation_available", estimated_cost is not None
        )
        if estimated_cost is not None:
            current_span.set_attribute("sacm.gen_ai.estimated_cost_usd", estimated_cost)

        self.token_counter.add(input_tokens, {**attributes, "sacm.gen_ai.token_type": "input"})
        if output_tokens:
            self.token_counter.add(
                output_tokens, {**attributes, "sacm.gen_ai.token_type": "output"}
            )
        if estimated_cost is not None:
            self.cost_counter.add(estimated_cost, attributes)
        return estimated_cost

    def record_tool_execution(
        self, tool: str, duration_ms: int, returncode: int
    ) -> None:
        if duration_ms < 0:
            raise ValueError("duration_ms cannot be negative.")
        if not self.enabled:
            return
        attributes = {"sacm.tool.name": tool, "sacm.tool.returncode": returncode}
        current_span = trace.get_current_span()
        current_span.set_attribute("sacm.tool.name", tool)
        current_span.set_attribute("sacm.tool.duration_ms", duration_ms)
        current_span.set_attribute("sacm.tool.returncode", returncode)
        self.tool_duration.record(duration_ms, attributes)
        self.tool_counter.add(1, attributes)


@dataclass
class TaskTrace:
    client: Client | None
    run_id: uuid.UUID | None
    project_name: str
    otel: OpenTelemetryService
    span: Any | None = None
    span_context: object | None = None

    def record(
        self,
        name: str,
        run_type: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
    ) -> None:
        if self.span is not None:
            with self.otel.tracer.start_as_current_span(name) as span:
                span.set_attribute("sacm.run_type", run_type)
                for key, value in {**inputs, **outputs}.items():
                    if isinstance(value, (str, bool, int, float)):
                        span.set_attribute(f"sacm.{key}", value)

        if self.client is None or self.run_id is None:
            return

        event_id = uuid.uuid4()
        self.client.create_run(
            id=event_id,
            name=name,
            run_type=run_type,
            inputs=inputs,
            project_name=self.project_name,
            parent_run_id=self.run_id,
        )
        self.client.update_run(event_id, outputs=outputs, end_time=datetime.now(timezone.utc))

    def finish(self, outputs: dict[str, Any]) -> None:
        if self.span is not None:
            for key, value in outputs.items():
                if isinstance(value, (str, bool, int, float)):
                    self.span.set_attribute(f"sacm.{key}", value)
            self.span.end()
            if self.span_context is not None:
                context.detach(self.span_context)

        if self.client is None or self.run_id is None:
            return
        self.client.update_run(
            self.run_id,
            outputs=outputs,
            end_time=datetime.now(timezone.utc),
        )


class ObservabilityService:
    """Privacy-preserving, opt-in operational traces for LangSmith."""

    def __init__(self) -> None:
        self._enabled = _is_enabled()
        self._otel = OpenTelemetryService(_is_otel_enabled())
        self._project_name = os.getenv("LANGSMITH_PROJECT", "sacm-agent-runtime")
        if self._enabled and not os.getenv("LANGSMITH_API_KEY"):
            raise RuntimeError(
                "LANGSMITH_API_KEY must be set when LANGSMITH_TRACING=true."
            )
        self._client = Client() if self._enabled else None

    def start_task(self, task_id: str, max_steps: int) -> TaskTrace:
        span = None
        span_context = None
        if self._otel.enabled:
            span, span_context = self._otel.start_span(
                "sacm.run_task",
                {"sacm.task_id": task_id, "sacm.max_steps": max_steps},
            )
        if self._client is None:
            return TaskTrace(
                None,
                None,
                self._project_name,
                self._otel,
                span,
                span_context,
            )

        run_id = uuid.uuid4()
        self._client.create_run(
            id=run_id,
            name="sacm.run_task",
            run_type="chain",
            inputs={"task_id": task_id, "max_steps": max_steps},
            project_name=self._project_name,
            extra={"metadata": {"component": "orchestrator"}},
        )
        return TaskTrace(
            self._client,
            run_id,
            self._project_name,
            self._otel,
            span,
            span_context,
        )

    def record_embedding_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        operation: str,
        output_tokens: int = 0,
    ) -> float | None:
        return self._otel.record_model_usage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            operation=operation,
        )

    def record_model_usage(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        operation: str,
    ) -> float | None:
        return self.record_embedding_usage(
            provider,
            model,
            input_tokens,
            operation,
            output_tokens,
        )

    def record_tool_execution(
        self, tool: str, duration_ms: int, returncode: int
    ) -> None:
        self._otel.record_tool_execution(tool, duration_ms, returncode)
