"""Serve the hosted intake agent through the Foundry responses protocol."""

import logging
import os
from pathlib import Path
from uuid import uuid4

from agent_framework_foundry_hosting import ResponsesHostServer
from azure.cosmos import CosmosClient
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agents.hosted.agent import build_agent
from agents.hosted.logging_config import configure_logging
from agents.hosted.rag import verify_foundry_iq_access
from agents.hosted.request_telemetry import (
    HostedRequestTelemetryMiddleware,
    configure_hosted_observability,
)
from agents.hosted.search_index import initialize_search_index


logger = logging.getLogger(__name__)


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


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def initialize_rag() -> None:
    provider_name = os.getenv("RAG_PROVIDER", "memory").lower()
    if provider_name == "foundry_iq":
        if get_boolean_setting("RAG_AUTO_INDEX", False):
            raise RuntimeError(
                "RAG_AUTO_INDEX must be false when RAG_PROVIDER=foundry_iq."
            )
        if not get_boolean_setting("FOUNDRY_IQ_STARTUP_CHECK", False):
            logger.info("Foundry IQ startup connectivity check is disabled.")
            return

        credential = DefaultAzureCredential()
        try:
            verify_foundry_iq_access(
                endpoint=get_required_setting("AZURE_SEARCH_ENDPOINT"),
                knowledge_base_name=get_required_setting(
                    "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME"
                ),
                credential=credential,
            )
            logger.info("Foundry IQ startup connectivity check succeeded.")
        except Exception:
            logger.exception("Foundry IQ startup connectivity check failed.")
            raise
        finally:
            credential.close()
        return

    if not get_boolean_setting("RAG_AUTO_INDEX", False):
        logger.info("Azure AI Search startup indexing is disabled.")
        return
    if provider_name != "azure_search":
        raise RuntimeError(
            "RAG_AUTO_INDEX=true requires RAG_PROVIDER=azure_search."
        )

    credential = DefaultAzureCredential()
    try:
        initialize_search_index(
            endpoint=get_required_setting("AZURE_SEARCH_ENDPOINT"),
            index_name=get_required_setting("AZURE_SEARCH_INDEX_NAME"),
            documents_path=Path(
                os.getenv("RAG_DOCUMENTS_PATH", "data/knowledge")
            ),
            credential=credential,
        )
    except Exception:
        logger.exception("Azure AI Search startup indexing failed.")
        raise
    finally:
        credential.close()


def verify_cosmos_access() -> None:
    if not get_boolean_setting("COSMOS_STARTUP_CHECK", False):
        logger.info("Cosmos DB startup connectivity check is disabled.")
        return
    if os.getenv("HISTORY_PROVIDER", "memory").lower() != "cosmos":
        raise RuntimeError(
            "COSMOS_STARTUP_CHECK=true requires HISTORY_PROVIDER=cosmos."
        )

    credential = DefaultAzureCredential()
    client = CosmosClient(
        get_required_setting("AZURE_COSMOS_ENDPOINT"),
        credential=credential,
    )
    session_id = f"startup-check-{uuid4()}"
    item = {
        "id": session_id,
        "session_id": session_id,
        "type": "startup-connectivity-check",
    }
    try:
        container = client.get_database_client(
            get_required_setting("AZURE_COSMOS_DATABASE_NAME")
        ).get_container_client(
            get_required_setting("AZURE_COSMOS_CONTAINER_NAME")
        )
        container.upsert_item(item)
        container.read_item(item=session_id, partition_key=session_id)
        container.delete_item(item=session_id, partition_key=session_id)
        logger.info("Cosmos DB startup connectivity check succeeded.")
    except Exception:
        logger.exception("Cosmos DB startup connectivity check failed.")
        raise
    finally:
        client.close()
        credential.close()


def main() -> None:
    load_dotenv()
    configure_logging()
    initialize_rag()
    verify_cosmos_access()
    components = build_agent()
    server = ResponsesHostServer(
        components.agent,
        configure_observability=configure_hosted_observability,
    )
    server.add_middleware(HostedRequestTelemetryMiddleware)
    server.run()


if __name__ == "__main__":
    main()
