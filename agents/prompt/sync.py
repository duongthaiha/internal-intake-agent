"""Create or update the repository-managed Foundry prompt agent."""

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agents.shared.instructions import load_intake_instructions


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")


@dataclass(frozen=True)
class PromptAgentConfig:
    name: str
    description: str
    project_endpoint: str
    model: str
    instructions_path: Path
    mcp_server_label: str
    mcp_server_url: str
    mcp_connection_id: str
    mcp_allowed_tools: tuple[str, ...]
    mcp_require_approval: str


def _required_string(data: dict[str, Any], name: str, source: Path) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{source} must define a non-empty string '{name}'.")
    return value.strip()


def _required_environment_variable(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _required_string_list(
    data: dict[str, Any],
    name: str,
    source: Path,
) -> tuple[str, ...]:
    value = data.get(name)
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item.strip() for item in value)
    ):
        raise RuntimeError(
            f"{source} must define a non-empty string list '{name}'."
        )
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        raise RuntimeError(f"{source} must not define duplicate values in '{name}'.")
    return normalized


def load_prompt_agent_config(path: Path) -> PromptAgentConfig:
    try:
        raw_config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"Prompt agent config does not exist: {path}") from exc
    if not isinstance(raw_config, dict):
        raise RuntimeError(f"Prompt agent config must be a YAML object: {path}")

    name_environment_variable = _required_string(
        raw_config, "nameEnvironmentVariable", path
    )
    default_name = _required_string(raw_config, "defaultName", path)
    endpoint_environment_variable = _required_string(
        raw_config, "projectEndpointEnvironmentVariable", path
    )
    model_environment_variable = _required_string(
        raw_config, "modelEnvironmentVariable", path
    )
    mcp_server_url_environment_variable = _required_string(
        raw_config, "mcpServerUrlEnvironmentVariable", path
    )
    mcp_connection_id_environment_variable = _required_string(
        raw_config, "mcpConnectionIdEnvironmentVariable", path
    )
    mcp_require_approval = _required_string(
        raw_config, "mcpRequireApproval", path
    )
    if mcp_require_approval not in {"always", "never"}:
        raise RuntimeError(
            f"{path} must define 'mcpRequireApproval' as 'always' or 'never'."
        )
    instructions_relative_path = Path(
        _required_string(raw_config, "instructionsPath", path)
    )
    instructions_path = (
        instructions_relative_path
        if instructions_relative_path.is_absolute()
        else REPOSITORY_ROOT / instructions_relative_path
    )

    return PromptAgentConfig(
        name=os.getenv(name_environment_variable) or default_name,
        description=_required_string(raw_config, "description", path),
        project_endpoint=_required_environment_variable(
            endpoint_environment_variable
        ),
        model=_required_environment_variable(model_environment_variable),
        instructions_path=instructions_path,
        mcp_server_label=_required_string(raw_config, "mcpServerLabel", path),
        mcp_server_url=_required_environment_variable(
            mcp_server_url_environment_variable
        ),
        mcp_connection_id=_required_environment_variable(
            mcp_connection_id_environment_variable
        ),
        mcp_allowed_tools=_required_string_list(
            raw_config, "mcpAllowedTools", path
        ),
        mcp_require_approval=mcp_require_approval,
    )


def build_prompt_agent_definition(
    config: PromptAgentConfig,
) -> PromptAgentDefinition:
    return PromptAgentDefinition(
        model=config.model,
        instructions=load_intake_instructions(config.instructions_path),
        tools=[
            MCPTool(
                server_label=config.mcp_server_label,
                server_url=config.mcp_server_url,
                project_connection_id=config.mcp_connection_id,
                allowed_tools=list(config.mcp_allowed_tools),
                require_approval=config.mcp_require_approval,
            )
        ],
    )


def _tool_value(tool: object) -> object:
    as_dict = getattr(tool, "as_dict", None)
    return as_dict() if callable(as_dict) else tool


def definitions_match(
    current: object,
    desired: PromptAgentDefinition,
) -> bool:
    return (
        isinstance(current, PromptAgentDefinition)
        and current.model == desired.model
        and current.instructions == desired.instructions
        and [_tool_value(tool) for tool in current.tools or []]
        == [_tool_value(tool) for tool in desired.tools or []]
    )


def sync_prompt_agent(
    config: PromptAgentConfig,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    definition = build_prompt_agent_definition(config)
    if dry_run:
        return (
            f"Would synchronize prompt agent '{config.name}' with model "
            f"'{config.model}' and MCP server '{config.mcp_server_label}' "
            f"({len(config.mcp_allowed_tools)} allowed tools, approval "
            f"{config.mcp_require_approval})."
        )

    credential = DefaultAzureCredential()
    try:
        with AIProjectClient(
            endpoint=config.project_endpoint,
            credential=credential,
            allow_preview=True,
        ) as project_client:
            try:
                existing = project_client.agents.get(config.name)
            except ResourceNotFoundError:
                existing = None

            latest = existing.versions.latest if existing is not None else None
            if (
                latest is not None
                and not force
                and definitions_match(latest.definition, definition)
                and latest.description == config.description
            ):
                return (
                    f"Prompt agent '{config.name}' is already current at version "
                    f"{latest.version}."
                )

            version = project_client.agents.create_version(
                config.name,
                definition=definition,
                description=config.description,
            )
    finally:
        credential.close()

    return f"Synchronized prompt agent '{config.name}' as version {version.version}."


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Create or update the repository-managed Foundry prompt agent."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Prompt-agent YAML config (default: {DEFAULT_CONFIG_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate local configuration without calling Foundry.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new version even when the latest definition is unchanged.",
    )
    args = parser.parse_args()

    config = load_prompt_agent_config(args.config.resolve())
    print(sync_prompt_agent(config, dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
