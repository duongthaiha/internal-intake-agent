"""Request-level OpenTelemetry spans for the hosted agent server."""

import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlunsplit

from opentelemetry import baggage, context as otel_context, propagate, trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer


ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[[], Awaitable[dict[str, Any]]],
        Callable[[dict[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]


class HostedAgentIdentitySpanProcessor(SpanProcessor):
    """Normalize hosted telemetry to the Foundry agent name and version."""

    def __init__(self, agent_name: str | None = None) -> None:
        self.agent_name = (
            agent_name
            or os.getenv("FOUNDRY_AGENT_NAME")
            or os.getenv("AGENT_MAF_POC_AGENT_NAME")
            or "maf-poc-agent"
        )

    def on_start(
        self,
        span: Any,
        parent_context: Context | None = None,
    ) -> None:
        context = parent_context or otel_context.get_current()
        attributes = span.attributes
        agent_name = (
            attributes.get("gen_ai.agent.name")
            or
            baggage.get_baggage("gen_ai.agent.name", context)
            or self.agent_name
        )
        agent_version = (
            attributes.get("gen_ai.agent.version")
            or baggage.get_baggage(
                "gen_ai.agent.version",
                context,
            )
        )
        if not agent_name or not agent_version:
            return

        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("gen_ai.agent.version", agent_version)
        span.set_attribute(
            "gen_ai.agent.id",
            f"{agent_name}:{agent_version}",
        )

    def _on_ending(self, span: Any) -> None:
        attributes = span.attributes
        agent_name = attributes.get("gen_ai.agent.name") or self.agent_name
        agent_version = attributes.get("gen_ai.agent.version")
        if not agent_name or not agent_version:
            return

        attribute_store = getattr(span, "_attributes", None)
        if attribute_store is None:
            return
        target = getattr(attribute_store, "_dict", attribute_store)
        target["gen_ai.agent.id"] = f"{agent_name}:{agent_version}"
        if target.get("gen_ai.operation.name") == "invoke_agent":
            provider_name = target.get("gen_ai.provider.name")
            if provider_name and provider_name != "microsoft.foundry":
                target["microsoft.agent_framework.provider.name"] = (
                    provider_name
                )
            target["gen_ai.provider.name"] = "microsoft.foundry"
            target["microsoft.foundry"] = True
            target["span_type"] = "agent"
            project_id = target.get("microsoft.foundry.project.id")
            if project_id:
                target["gen_ai.azure_ai_project.id"] = project_id

    def on_end(self, span: Any) -> None:
        self._on_ending(span)


def configure_hosted_observability(
    *,
    connection_string: str | None = None,
    log_level: str | None = None,
    enable_sensitive_data: bool = False,
) -> None:
    from azure.ai.agentserver.core import _tracing as agentserver_tracing

    original_setup = agentserver_tracing._setup_distro_export

    def setup_with_identity_processor(
        *,
        resource: Any,
        span_processors: list[Any],
        metric_readers: list[Any],
        log_record_processors: list[Any],
        connection_string: str | None = None,
        enable_sensitive_data: bool = False,
    ) -> None:
        span_processors.append(HostedAgentIdentitySpanProcessor())
        original_setup(
            resource=resource,
            span_processors=span_processors,
            metric_readers=metric_readers,
            log_record_processors=log_record_processors,
            connection_string=connection_string,
            enable_sensitive_data=enable_sensitive_data,
        )

    agentserver_tracing._setup_distro_export = setup_with_identity_processor
    try:
        agentserver_tracing.configure_observability(
            connection_string=connection_string,
            log_level=log_level,
            enable_sensitive_data=enable_sensitive_data,
        )
    finally:
        agentserver_tracing._setup_distro_export = original_setup


def _header_carrier(scope: dict[str, Any]) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", [])
    }


def _scope_with_current_trace(
    scope: dict[str, Any],
    context: Context | None = None,
) -> dict[str, Any]:
    carrier: dict[str, str] = {}
    propagate.inject(carrier, context=context)
    propagation_headers = {
        key.lower().encode("latin-1"): value.encode("latin-1")
        for key, value in carrier.items()
        if key.lower() in {"baggage", "traceparent", "tracestate"}
    }
    headers = [
        (key, value)
        for key, value in scope.get("headers", [])
        if key.lower() not in propagation_headers
    ]
    headers.extend(propagation_headers.items())
    return {**scope, "headers": headers}


def _request_url(scope: dict[str, Any], headers: dict[str, str]) -> str:
    scheme = str(scope.get("scheme", "http"))
    host = headers.get("host")
    if not host:
        server = scope.get("server")
        if server:
            server_host, server_port = server
            host = f"{server_host}:{server_port}"
    path = str(scope.get("path", "/"))
    return urlunsplit((scheme, host or "", path, "", ""))


class HostedRequestTelemetryMiddleware:
    """Create Azure Monitor request spans around hosted-agent HTTP calls."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        tracer: Tracer | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
    ) -> None:
        self.app = app
        self.tracer = tracer or trace.get_tracer(__name__)
        self.agent_name = (
            agent_name
            or os.getenv("FOUNDRY_AGENT_NAME")
            or os.getenv("AGENT_MAF_POC_AGENT_NAME")
            or "maf-poc-agent"
        )
        self.agent_version = (
            agent_version
            or os.getenv("FOUNDRY_AGENT_VERSION")
            or os.getenv("AGENT_MAF_POC_AGENT_VERSION")
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = _header_carrier(scope)
        method = str(scope.get("method", "HTTP"))
        path = str(scope.get("path", "/"))
        if path in {"/readiness", "/health", "/healthz"}:
            await self.app(scope, receive, send)
            return

        parent_context = propagate.extract(headers)
        agent_name = self.agent_name or baggage.get_baggage(
            "gen_ai.agent.name",
            parent_context,
        )
        agent_version = self.agent_version or baggage.get_baggage(
            "gen_ai.agent.version",
            parent_context,
        )
        if agent_name:
            parent_context = baggage.set_baggage(
                "gen_ai.agent.name",
                agent_name,
                parent_context,
            )
        if agent_version:
            parent_context = baggage.set_baggage(
                "gen_ai.agent.version",
                agent_version,
                parent_context,
            )
        if agent_name and agent_version:
            parent_context = baggage.set_baggage(
                "gen_ai.agent.id",
                f"{agent_name}:{agent_version}",
                parent_context,
            )

        attributes: dict[str, str | int] = {
            "http.request.method": method,
            "http.method": method,
            "url.path": path,
            "url.full": _request_url(scope, headers),
            "http.route": path,
        }
        if path == "/responses":
            attributes["gen_ai.operation.name"] = "invoke_agent"
        if agent_name:
            attributes["gen_ai.agent.name"] = agent_name
            attributes["azure.ai.agentserver.agent_name"] = agent_name
        if agent_version:
            attributes["gen_ai.agent.version"] = agent_version
        if agent_name and agent_version:
            attributes["gen_ai.agent.id"] = (
                f"{agent_name}:{agent_version}"
            )

        with self.tracer.start_as_current_span(
            f"{method} {path}",
            context=parent_context,
            kind=SpanKind.SERVER,
            attributes=attributes,
        ) as span:
            downstream_context = trace.set_span_in_context(
                span,
                parent_context,
            )

            async def send_with_status(message: dict[str, Any]) -> None:
                if message.get("type") == "http.response.start":
                    status_code = int(message["status"])
                    span.set_attribute(
                        "http.response.status_code",
                        status_code,
                    )
                    span.set_attribute("http.status_code", status_code)
                    if status_code >= 500:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                f"HTTP {status_code}",
                            )
                        )
                await send(message)

            try:
                context_token = otel_context.attach(downstream_context)
                try:
                    await self.app(
                        _scope_with_current_trace(
                            scope,
                            downstream_context,
                        ),
                        receive,
                        send_with_status,
                    )
                finally:
                    otel_context.detach(context_token)
            except Exception:
                span.set_attribute("http.response.status_code", 500)
                span.set_attribute("http.status_code", 500)
                span.set_status(Status(StatusCode.ERROR, "HTTP 500"))
                raise
