import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from azure.ai.projects.models import MCPTool, PromptAgentDefinition

from agents.prompt.sync import (
    build_prompt_agent_definition,
    definitions_match,
    load_prompt_agent_config,
    sync_prompt_agent,
)
from agents.shared.instructions import load_intake_instructions


class PromptAgentTests(unittest.TestCase):
    def test_shared_instructions_are_non_empty(self) -> None:
        instructions = load_intake_instructions()

        self.assertIn("internal innovation intake assistant", instructions)
        self.assertIn("requester's name and email", instructions)

    def test_shared_instructions_reject_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instructions.md"
            path.write_text("  \n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "empty"):
                load_intake_instructions(path)

    def test_config_resolves_environment_and_absolute_instruction_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instructions = root / "instructions.md"
            instructions.write_text("Be helpful.", encoding="utf-8")
            config = root / "config.yaml"
            config.write_text(
                "\n".join(
                    [
                        "nameEnvironmentVariable: PROMPT_AGENT_NAME",
                        "defaultName: default-agent",
                        "description: Test prompt agent",
                        "projectEndpointEnvironmentVariable: FOUNDRY_PROJECT_ENDPOINT",
                        "modelEnvironmentVariable: AZURE_AI_MODEL_DEPLOYMENT_NAME",
                        f"instructionsPath: '{instructions.as_posix()}'",
                        "mcpServerLabel: intake_mcp",
                        "mcpServerUrlEnvironmentVariable: AZURE_INTAKE_MCP_SERVER_URL",
                        "mcpConnectionIdEnvironmentVariable: FOUNDRY_INTAKE_MCP_CONNECTION_ID",
                        "mcpAllowedTools:",
                        "  - list_intake_requests",
                        "mcpRequireApproval: always",
                    ]
                ),
                encoding="utf-8",
            )
            environment = {
                "PROMPT_AGENT_NAME": "configured-agent",
                "FOUNDRY_PROJECT_ENDPOINT": (
                    "https://example.services.ai.azure.com/api/projects/test"
                ),
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "AZURE_INTAKE_MCP_SERVER_URL": "https://example.test/mcp",
                "FOUNDRY_INTAKE_MCP_CONNECTION_ID": (
                    "/subscriptions/test/connections/intake"
                ),
            }

            with patch.dict(os.environ, environment, clear=True):
                loaded = load_prompt_agent_config(config)

            self.assertEqual(loaded.name, "configured-agent")
            self.assertEqual(loaded.instructions_path, instructions)
            self.assertEqual(loaded.mcp_server_label, "intake_mcp")
            self.assertEqual(
                loaded.mcp_allowed_tools,
                ("list_intake_requests",),
            )
            self.assertEqual(loaded.mcp_require_approval, "always")

    def test_config_requires_mcp_connection_environment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instructions = root / "instructions.md"
            instructions.write_text("Be helpful.", encoding="utf-8")
            config = root / "config.yaml"
            config.write_text(
                "\n".join(
                    [
                        "nameEnvironmentVariable: PROMPT_AGENT_NAME",
                        "defaultName: default-agent",
                        "description: Test prompt agent",
                        "projectEndpointEnvironmentVariable: FOUNDRY_PROJECT_ENDPOINT",
                        "modelEnvironmentVariable: AZURE_AI_MODEL_DEPLOYMENT_NAME",
                        f"instructionsPath: '{instructions.as_posix()}'",
                        "mcpServerLabel: intake_mcp",
                        "mcpServerUrlEnvironmentVariable: AZURE_INTAKE_MCP_SERVER_URL",
                        "mcpConnectionIdEnvironmentVariable: FOUNDRY_INTAKE_MCP_CONNECTION_ID",
                        "mcpAllowedTools:",
                        "  - list_intake_requests",
                        "mcpRequireApproval: always",
                    ]
                ),
                encoding="utf-8",
            )
            environment = {
                "FOUNDRY_PROJECT_ENDPOINT": (
                    "https://example.services.ai.azure.com/api/projects/test"
                ),
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "AZURE_INTAKE_MCP_SERVER_URL": "https://example.test/mcp",
            }

            with (
                patch.dict(os.environ, environment, clear=True),
                self.assertRaisesRegex(
                    RuntimeError,
                    "FOUNDRY_INTAKE_MCP_CONNECTION_ID",
                ),
            ):
                load_prompt_agent_config(config)

    def test_build_definition_creates_approval_gated_mcp_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instructions = Path(directory) / "instructions.md"
            instructions.write_text("Be helpful.", encoding="utf-8")
            config = SimpleNamespace(
                model="model",
                instructions_path=instructions,
                mcp_server_label="intake_mcp",
                mcp_server_url="https://example.test/mcp",
                mcp_connection_id="/subscriptions/test/connections/intake",
                mcp_allowed_tools=(
                    "get_intake_request",
                    "list_intake_requests",
                ),
                mcp_require_approval="always",
            )

            definition = build_prompt_agent_definition(config)

        self.assertEqual(len(definition.tools), 1)
        tool = definition.tools[0]
        self.assertIsInstance(tool, MCPTool)
        self.assertEqual(tool.server_label, "intake_mcp")
        self.assertEqual(tool.server_url, "https://example.test/mcp")
        self.assertEqual(
            tool.project_connection_id,
            "/subscriptions/test/connections/intake",
        )
        self.assertEqual(
            tool.allowed_tools,
            ["get_intake_request", "list_intake_requests"],
        )
        self.assertEqual(tool.require_approval, "always")

    def test_definition_match_uses_model_instructions_and_tools(self) -> None:
        tool = MCPTool(
            server_label="intake_mcp",
            server_url="https://example.test/mcp",
            project_connection_id="/subscriptions/test/connections/intake",
            allowed_tools=["list_intake_requests"],
            require_approval="always",
        )
        current = PromptAgentDefinition(
            model="model",
            instructions="Be helpful.",
            tools=[tool],
        )
        desired = PromptAgentDefinition(
            model="model",
            instructions="Be helpful.",
            tools=[
                MCPTool(
                    server_label="intake_mcp",
                    server_url="https://example.test/mcp",
                    project_connection_id="/subscriptions/test/connections/intake",
                    allowed_tools=["list_intake_requests"],
                    require_approval="always",
                )
            ],
        )

        self.assertTrue(definitions_match(current, desired))
        desired.tools[0].require_approval = "never"
        self.assertFalse(definitions_match(current, desired))

    def test_dry_run_does_not_require_foundry_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            instructions = root / "instructions.md"
            instructions.write_text("Be helpful.", encoding="utf-8")
            config = type(
                "Config",
                (),
                {
                    "name": "prompt-agent",
                    "description": "Prompt agent",
                    "project_endpoint": (
                        "https://example.services.ai.azure.com/api/projects/test"
                    ),
                    "model": "model",
                    "instructions_path": instructions,
                    "mcp_server_label": "intake_mcp",
                    "mcp_server_url": "https://example.test/mcp",
                    "mcp_connection_id": (
                        "/subscriptions/test/connections/intake"
                    ),
                    "mcp_allowed_tools": ("list_intake_requests",),
                    "mcp_require_approval": "always",
                },
            )()

            result = sync_prompt_agent(config, dry_run=True, force=False)

        self.assertIn("Would synchronize prompt agent 'prompt-agent'", result)
        self.assertIn("approval always", result)

    @patch("agents.prompt.sync.AIProjectClient")
    @patch("agents.prompt.sync.DefaultAzureCredential")
    def test_existing_agent_without_latest_version_creates_version(
        self,
        credential_type,
        project_client_type,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            instructions = Path(directory) / "instructions.md"
            instructions.write_text("Be helpful.", encoding="utf-8")
            config = SimpleNamespace(
                name="prompt-agent",
                description="Prompt agent",
                project_endpoint=(
                    "https://example.services.ai.azure.com/api/projects/test"
                ),
                model="model",
                instructions_path=instructions,
                mcp_server_label="intake_mcp",
                mcp_server_url="https://example.test/mcp",
                mcp_connection_id="/subscriptions/test/connections/intake",
                mcp_allowed_tools=("list_intake_requests",),
                mcp_require_approval="always",
            )
            credential = credential_type.return_value
            client = project_client_type.return_value.__enter__.return_value
            client.agents.get.return_value = SimpleNamespace(
                versions=SimpleNamespace(latest=None)
            )
            client.agents.create_version.return_value = SimpleNamespace(version="1")

            result = sync_prompt_agent(config, dry_run=False, force=False)

        self.assertIn("version 1", result)
        client.agents.create_version.assert_called_once()
        credential.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
