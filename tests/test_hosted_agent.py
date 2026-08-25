import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agents.hosted.agent import INTAKE_TOOLBOX_TOOLS, build_agent, build_toolbox
from agents.hosted.identity_diagnostics import (
    IdentityDiagnosticsMiddleware,
    hash_header_value,
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
    @patch("agents.hosted.agent.FoundryToolbox")
    @patch("agents.hosted.agent.build_rag_provider", return_value=("none", None))
    @patch("agents.hosted.agent.DefaultAzureCredential")
    def test_build_agent_attaches_toolbox(
        self,
        credential_type: MagicMock,
        _build_rag_provider: MagicMock,
        toolbox_type: MagicMock,
        _chat_client_type: MagicMock,
        _history_provider_type: MagicMock,
        create_harness_agent: MagicMock,
    ) -> None:
        credential = MagicMock()
        credential_type.return_value = credential
        toolbox = MagicMock()
        toolbox_type.return_value = toolbox
        agent = MagicMock()
        create_harness_agent.return_value = agent

        with patch.dict(
            os.environ,
            {
                "FOUNDRY_PROJECT_ENDPOINT": "https://example.test/project",
                "FOUNDRY_MODEL": "test-model",
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
        self.assertIs(create_harness_agent.call_args.kwargs["tools"], toolbox)
        self.assertIsNone(
            create_harness_agent.call_args.kwargs["middleware"]
        )

    @patch("agents.hosted.agent.create_harness_agent")
    @patch("agents.hosted.agent.InMemoryHistoryProvider")
    @patch("agents.hosted.agent.FoundryChatClient")
    @patch("agents.hosted.agent.build_rag_provider", return_value=("none", None))
    @patch("agents.hosted.agent.DefaultAzureCredential")
    def test_build_agent_enables_identity_diagnostics_explicitly(
        self,
        _credential_type: MagicMock,
        _build_rag_provider: MagicMock,
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


if __name__ == "__main__":
    unittest.main()
