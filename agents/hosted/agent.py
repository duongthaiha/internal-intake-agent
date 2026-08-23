"""Build and run the hosted intake agent."""

import argparse
import asyncio
import logging
import os
from contextlib import AsyncExitStack
from dataclasses import dataclass

from agent_framework import (
    Agent,
    AgentModeProvider,
    InMemoryHistoryProvider,
    create_harness_agent,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework_azure_ai_search import AzureAISearchContextProvider
from agent_framework_azure_cosmos import CosmosHistoryProvider
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

from agents.hosted.history import ObservableCosmosHistoryProvider
from agents.hosted.logging_config import LOG_LEVEL_NAMES, configure_logging
from agents.hosted.rag import build_rag_provider
from agents.shared.instructions import load_intake_instructions


logger = logging.getLogger(__name__)


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_history_provider_name() -> str:
    provider_name = os.getenv("HISTORY_PROVIDER", "memory").lower()
    if provider_name not in {"memory", "cosmos"}:
        raise RuntimeError("HISTORY_PROVIDER must be either 'memory' or 'cosmos'.")
    return provider_name


def get_boolean_setting(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = raw_value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value.")


@dataclass
class AgentComponents:
    agent: Agent
    credential: DefaultAzureCredential
    history_provider: InMemoryHistoryProvider | CosmosHistoryProvider
    history_provider_name: str
    rag_provider: object | None
    rag_provider_name: str


def build_agent() -> AgentComponents:
    project_endpoint = get_required_setting("FOUNDRY_PROJECT_ENDPOINT")
    model = os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME") or get_required_setting(
        "FOUNDRY_MODEL"
    )
    history_provider_name = get_history_provider_name()
    credential = DefaultAzureCredential()
    rag_provider_name, rag_provider = build_rag_provider(credential)
    logger.info(
        "Building agent with model=%s, history_provider=%s, rag_provider=%s",
        model,
        history_provider_name,
        rag_provider_name,
    )

    if history_provider_name == "cosmos":
        history_provider = ObservableCosmosHistoryProvider(
            endpoint=get_required_setting("AZURE_COSMOS_ENDPOINT"),
            database_name=get_required_setting("AZURE_COSMOS_DATABASE_NAME"),
            container_name=get_required_setting("AZURE_COSMOS_CONTAINER_NAME"),
            credential=os.getenv("AZURE_COSMOS_KEY") or credential,
            load_messages=get_boolean_setting("COSMOS_LOAD_MESSAGES", True),
        )
    else:
        history_provider = InMemoryHistoryProvider()

    agent = create_harness_agent(
        client=FoundryChatClient(
            project_endpoint=project_endpoint,
            model=model,
            credential=credential,
        ),
        name="maf-poc-agent",
        description="An Intake agent that helps with internal intake processes.",
        harness_instructions=(
            "Work methodically. For multi-step tasks, plan the work, track "
            "progress with todos, and verify the result before responding. "
            "Use tools deliberately and report uncertainty clearly."
        ),
        agent_instructions=load_intake_instructions(),
        history_provider=history_provider,
        context_providers=[rag_provider] if rag_provider else None,
        disable_file_memory=True,
        mode_provider=AgentModeProvider(default_mode="execute"),
        default_options={"store": False},
    )

    return AgentComponents(
        agent=agent,
        credential=credential,
        history_provider=history_provider,
        history_provider_name=history_provider_name,
        rag_provider=rag_provider,
        rag_provider_name=rag_provider_name,
    )


async def run_agent(prompt: str | None, session_id: str | None) -> None:
    components = build_agent()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(components.credential)
        if isinstance(components.history_provider, CosmosHistoryProvider):
            await stack.enter_async_context(components.history_provider)
        if isinstance(components.rag_provider, AzureAISearchContextProvider):
            await stack.enter_async_context(components.rag_provider)
        agent = await stack.enter_async_context(components.agent)

        session = agent.create_session(session_id=session_id)
        print(f"Session: {session.session_id}")
        print(f"History provider: {components.history_provider_name}")
        print(f"RAG provider: {components.rag_provider_name}")

        if prompt:
            response = await agent.run(prompt, session=session)
            print(response.text)
            return

        print("Agent ready. Enter a message, or type 'exit' to quit.")
        while True:
            user_input = input("\nYou: ").strip()
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue

            response = await agent.run(user_input, session=session)
            print(f"Agent: {response.text}")


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run a Microsoft Agent Framework agent with Microsoft Foundry."
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Optional one-shot prompt. Omit it to start interactive mode.",
    )
    parser.add_argument(
        "--session-id",
        help="Set the conversation session ID. Cosmos sessions can be resumed later.",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVEL_NAMES,
        help="Override LOG_LEVEL for this process.",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    asyncio.run(run_agent(args.prompt, args.session_id))


if __name__ == "__main__":
    main()
