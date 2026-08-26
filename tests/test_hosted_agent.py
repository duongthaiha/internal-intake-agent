import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from opentelemetry import baggage, propagate
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode

from agents.hosted.agent import INTAKE_TOOLBOX_TOOLS, build_agent, build_toolbox
from agents.hosted.platform_recommendation import recommend_ai_platform
from agents.hosted.identity_diagnostics import (
    IdentityDiagnosticsMiddleware,
    hash_header_value,
)
from agents.hosted.request_telemetry import (
    HostedAgentIdentitySpanProcessor,
    HostedRequestTelemetryMiddleware,
)


class HostedToolboxTests(unittest.TestCase):
    @patch("agents.hosted.agent.FoundryToolbox")
    def test_build_toolbox_configures_allowed_tools_and_approval(
        self, toolbox_type: MagicMock
    ) -> None:
        credential = MagicMock()
        toolbox = MagicMock()
        toolbox_type.return_value = toolbox

        with patch.dict(
            os.environ,
            {
                "TOOLBOX_ENDPOINT": (
                    "https://example.test/project/toolboxes/intake/mcp"
                    "?api-version=v1"
                )
            },
        ):
            result = build_toolbox(
                credential,
                "https://example.test/project",
            )

        self.assertIs(result, toolbox)
        toolbox_type.assert_called_once_with(
            credential,
            url=(
                "https://example.test/project/toolboxes/intake/mcp"
                "?api-version=v1"
            ),
        )
        self.assertEqual(toolbox.allowed_tools, INTAKE_TOOLBOX_TOOLS)
        self.assertEqual(toolbox.approval_mode, "always_require")

    @patch("agents.hosted.agent.os.getenv", return_value=None)
    def test_build_toolbox_is_optional_without_endpoint(
        self, _getenv: MagicMock
    ) -> None:
        self.assertIsNone(
            build_toolbox(MagicMock(), "https://example.test/project")
        )

    def test_build_toolbox_rejects_empty_endpoint(self) -> None:
        with patch.dict(os.environ, {"TOOLBOX_ENDPOINT": "   "}):
            with self.assertRaisesRegex(
                RuntimeError,
                "TOOLBOX_ENDPOINT must not be empty",
            ):
                build_toolbox(MagicMock(), "https://example.test/project")

    def test_build_toolbox_rejects_endpoint_outside_project(self) -> None:
        with patch.dict(
            os.environ,
            {
                "TOOLBOX_ENDPOINT": (
                    "https://attacker.example/toolboxes/intake/mcp"
                    "?api-version=v1"
                )
            },
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "within FOUNDRY_PROJECT_ENDPOINT",
            ):
                build_toolbox(
                    MagicMock(),
                    "https://example.test/project",
                )

    @patch("agents.hosted.agent.create_harness_agent")
    @patch("agents.hosted.agent.InMemoryHistoryProvider")
    @patch("agents.hosted.agent.FoundryChatClient")
    @patch("agents.hosted.agent.build_skills_provider")
    @patch("agents.hosted.agent.FoundryToolbox")
    @patch("agents.hosted.agent.build_rag_provider", return_value=("none", None))
    @patch("agents.hosted.agent.DefaultAzureCredential")
    def test_build_agent_attaches_toolbox(
        self,
        credential_type: MagicMock,
        _build_rag_provider: MagicMock,
        toolbox_type: MagicMock,
        build_skills_provider: MagicMock,
        _chat_client_type: MagicMock,
        _history_provider_type: MagicMock,
        create_harness_agent: MagicMock,
    ) -> None:
        credential = MagicMock()
        credential_type.return_value = credential
        toolbox = MagicMock()
        toolbox_type.return_value = toolbox
        skills_provider = MagicMock()
        build_skills_provider.return_value = skills_provider
        agent = MagicMock()
        create_harness_agent.return_value = agent

        with patch.dict(
            os.environ,
            {
                "FOUNDRY_PROJECT_ENDPOINT": "https://example.test/project",
                "FOUNDRY_MODEL": "test-model",
                "FOUNDRY_AGENT_NAME": "hosted-agent",
                "FOUNDRY_AGENT_VERSION": "42",
                "HISTORY_PROVIDER": "memory",
                "TOOLBOX_ENDPOINT": (
                    "https://example.test/project/toolboxes/intake/mcp"
                    "?api-version=v1"
                ),
            },
            clear=True,
        ):
            components = build_agent()

        self.assertIs(components.agent, agent)
        self.assertIs(components.toolbox, toolbox)
        self.assertEqual(
            create_harness_agent.call_args.kwargs["tools"],
            [recommend_ai_platform, toolbox],
        )
        self.assertIs(
            create_harness_agent.call_args.kwargs["skills_provider"],
            skills_provider,
        )
        self.assertEqual(
            "hosted-agent:42",
            create_harness_agent.call_args.kwargs["id"],
        )
        self.assertEqual(
            "hosted-agent",
            create_harness_agent.call_args.kwargs["name"],
        )
        self.assertIsNone(
            create_harness_agent.call_args.kwargs["middleware"]
        )

    @patch("agents.hosted.agent.create_harness_agent")
    @patch("agents.hosted.agent.InMemoryHistoryProvider")
    @patch("agents.hosted.agent.FoundryChatClient")
    @patch("agents.hosted.agent.build_skills_provider")
    @patch("agents.hosted.agent.build_rag_provider", return_value=("none", None))
    @patch("agents.hosted.agent.DefaultAzureCredential")
    def test_build_agent_enables_identity_diagnostics_explicitly(
        self,
        _credential_type: MagicMock,
        _build_rag_provider: MagicMock,
        _build_skills_provider: MagicMock,
        _chat_client_type: MagicMock,
        _history_provider_type: MagicMock,
        create_harness_agent: MagicMock,
    ) -> None:
        with patch.dict(
            os.environ,
            {
                "FOUNDRY_PROJECT_ENDPOINT": "https://example.test/project",
                "FOUNDRY_MODEL": "test-model",
                "HISTORY_PROVIDER": "memory",
                "IDENTITY_DIAGNOSTICS_ENABLED": "true",
            },
            clear=True,
        ):
            build_agent()

        middleware = create_harness_agent.call_args.kwargs["middleware"]
        self.assertEqual(1, len(middleware))
        self.assertIsInstance(middleware[0], IdentityDiagnosticsMiddleware)


class IdentityDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    @patch("agents.hosted.identity_diagnostics.get_request_context")
    async def test_logs_hashed_user_context_without_raw_identifiers(
        self,
        get_request_context: MagicMock,
    ) -> None:
        raw_user_id = "11111111-2222-3333-4444-555555555555"
        raw_call_id = "opaque-call-id"
        get_request_context.return_value = SimpleNamespace(
            user_id=raw_user_id,
            call_id=raw_call_id,
            session_id="session-id",
        )
        call_next = AsyncMock()

        with self.assertLogs(
            "agents.hosted.identity_diagnostics",
            level="INFO",
        ) as captured:
            await IdentityDiagnosticsMiddleware().process(
                MagicMock(),
                call_next,
            )

        output = "\n".join(captured.output)
        self.assertIn(
            "x-agent-user-id=sha256:"
            f"{hash_header_value('x-agent-user-id', raw_user_id)}",
            output,
        )
        self.assertIn(
            "x-agent-foundry-call-id=sha256:"
            f"{hash_header_value('x-agent-foundry-call-id', raw_call_id)}",
            output,
        )
        self.assertIn("session_id=present", output)
        self.assertNotIn(raw_user_id, output)
        self.assertNotIn(raw_call_id, output)
        call_next.assert_awaited_once_with()


class HostedRequestTelemetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer(__name__)

    def tearDown(self) -> None:
        self.provider.shutdown()

    async def test_creates_correlated_server_span(self) -> None:
        downstream_traceparents: list[str] = []

        async def app(scope, _receive, send) -> None:
            self.assertEqual(
                "maf-poc-agent:16",
                baggage.get_baggage("gen_ai.agent.id"),
            )
            headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in scope["headers"]
            }
            downstream_traceparents.append(headers["traceparent"])
            downstream_context = propagate.extract(headers)
            self.assertEqual(
                "maf-poc-agent:16",
                baggage.get_baggage(
                    "gen_ai.agent.id",
                    downstream_context,
                ),
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        middleware = HostedRequestTelemetryMiddleware(
            app,
            tracer=self.tracer,
            agent_name="maf-poc-agent",
            agent_version="16",
        )
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/responses",
            "scheme": "https",
            "headers": [
                (b"host", b"agent.example"),
                (
                    b"traceparent",
                    b"00-11111111111111111111111111111111-2222222222222222-01",
                ),
                (
                    b"baggage",
                    b"gen_ai.agent.id=d46761d1-2ad3-440a-8709-cc7f0ba2043e,"
                    b"gen_ai.agent.name=maf-poc-agent,"
                    b"gen_ai.agent.version=16",
                ),
            ],
        }

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b""}

        async def send(_message: dict[str, object]) -> None:
            return None

        await middleware(scope, receive, send)

        spans = self.exporter.get_finished_spans()
        self.assertEqual(1, len(spans))
        span = spans[0]
        self.assertEqual(SpanKind.SERVER, span.kind)
        self.assertEqual(
            int("11111111111111111111111111111111", 16),
            span.context.trace_id,
        )
        self.assertEqual(
            int("2222222222222222", 16),
            span.parent.span_id,
        )
        self.assertEqual("maf-poc-agent", span.attributes["gen_ai.agent.name"])
        self.assertEqual("16", span.attributes["gen_ai.agent.version"])
        self.assertEqual(
            "invoke_agent",
            span.attributes["gen_ai.operation.name"],
        )
        self.assertEqual(200, span.attributes["http.response.status_code"])
        self.assertEqual(
            "https://agent.example/responses",
            span.attributes["url.full"],
        )
        self.assertIn(
            f"-{span.context.span_id:016x}-",
            downstream_traceparents[0],
        )

    async def test_uses_hosted_environment_identity(self) -> None:
        async def app(_scope, _receive, send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )

        with patch.dict(
            os.environ,
            {
                "FOUNDRY_AGENT_NAME": "hosted-agent",
                "FOUNDRY_AGENT_VERSION": "42",
            },
            clear=True,
        ):
            middleware = HostedRequestTelemetryMiddleware(
                app,
                tracer=self.tracer,
            )

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b""}

        async def send(_message: dict[str, object]) -> None:
            return None

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/responses",
                "headers": [],
            },
            receive,
            send,
        )

        span = self.exporter.get_finished_spans()[0]
        self.assertEqual("hosted-agent", span.attributes["gen_ai.agent.name"])
        self.assertEqual("42", span.attributes["gen_ai.agent.version"])
        self.assertEqual(
            "hosted-agent:42",
            span.attributes["gen_ai.agent.id"],
        )

    async def test_does_not_trace_readiness_probes(self) -> None:
        async def app(_scope, _receive, send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )

        middleware = HostedRequestTelemetryMiddleware(
            app,
            tracer=self.tracer,
        )

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b""}

        async def send(_message: dict[str, object]) -> None:
            return None

        await middleware(
            {
                "type": "http",
                "method": "GET",
                "path": "/readiness",
                "headers": [],
            },
            receive,
            send,
        )

        self.assertEqual((), self.exporter.get_finished_spans())

    async def test_marks_server_errors(self) -> None:
        async def app(_scope, _receive, send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [],
                }
            )

        middleware = HostedRequestTelemetryMiddleware(
            app,
            tracer=self.tracer,
        )

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b""}

        async def send(_message: dict[str, object]) -> None:
            return None

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "path": "/responses",
                "headers": [],
            },
            receive,
            send,
        )

        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(StatusCode.ERROR, span.status.status_code)
        self.assertEqual(503, span.attributes["http.response.status_code"])

    async def test_marks_unhandled_exceptions_as_500(self) -> None:
        async def app(_scope, _receive, _send) -> None:
            raise RuntimeError("boom")

        middleware = HostedRequestTelemetryMiddleware(
            app,
            tracer=self.tracer,
        )

        async def receive() -> dict[str, object]:
            return {"type": "http.request", "body": b""}

        async def send(_message: dict[str, object]) -> None:
            return None

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await middleware(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/responses",
                    "headers": [],
                },
                receive,
                send,
            )

        span = self.exporter.get_finished_spans()[0]
        self.assertEqual(StatusCode.ERROR, span.status.status_code)
        self.assertEqual(500, span.attributes["http.response.status_code"])


class HostedAgentIdentitySpanProcessorTests(unittest.TestCase):
    def test_overrides_platform_principal_with_agent_version_identity(
        self,
    ) -> None:
        context = baggage.set_baggage(
            "gen_ai.agent.name",
            "maf-poc-agent",
        )
        context = baggage.set_baggage(
            "gen_ai.agent.version",
            "20",
            context,
        )
        span = MagicMock()
        span.attributes = {}

        HostedAgentIdentitySpanProcessor().on_start(span, context)

        span.set_attribute.assert_any_call(
            "gen_ai.agent.id",
            "maf-poc-agent:20",
        )

    def test_overrides_identity_after_hosted_attributes_are_populated(
        self,
    ) -> None:
        span = MagicMock()
        attributes = {
            "gen_ai.agent.name": "maf-poc-agent",
            "gen_ai.agent.version": "21",
            "gen_ai.agent.id": "managed-identity-principal",
        }
        span.attributes = attributes
        span._attributes = attributes

        HostedAgentIdentitySpanProcessor()._on_ending(span)

        self.assertEqual(
            "maf-poc-agent:21",
            attributes["gen_ai.agent.id"],
        )

    def test_uses_attributes_set_by_earlier_span_processors(self) -> None:
        span = MagicMock()
        span.attributes = {
            "gen_ai.agent.name": "maf-poc-agent",
            "gen_ai.agent.version": "22",
            "gen_ai.agent.id": "managed-identity-principal",
        }

        HostedAgentIdentitySpanProcessor().on_start(span)

        span.set_attribute.assert_any_call(
            "gen_ai.agent.id",
            "maf-poc-agent:22",
        )

    def test_reapplies_identity_during_on_end(self) -> None:
        project_id = "/subscriptions/test/projects/intake"
        attributes = {
            "gen_ai.agent.name": "maf-poc-agent",
            "gen_ai.agent.version": "24",
            "gen_ai.agent.id": "managed-identity-principal",
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": "microsoft.agent_framework.harness",
            "microsoft.foundry.project.id": project_id,
        }
        span = MagicMock()
        span.attributes = attributes
        span._attributes = attributes

        HostedAgentIdentitySpanProcessor().on_end(span)

        self.assertEqual(
            "maf-poc-agent:24",
            attributes["gen_ai.agent.id"],
        )
        self.assertEqual("microsoft.foundry", attributes["gen_ai.provider.name"])
        self.assertEqual(True, attributes["microsoft.foundry"])
        self.assertEqual("agent", attributes["span_type"])
        self.assertEqual(
            project_id,
            attributes["gen_ai.azure_ai_project.id"],
        )
        self.assertEqual(
            "microsoft.agent_framework.harness",
            attributes["microsoft.agent_framework.provider.name"],
        )


if __name__ == "__main__":
    unittest.main()
