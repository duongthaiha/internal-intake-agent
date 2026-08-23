"""Azure AI Search index initialization for hosted-agent knowledge."""

import logging
import re
from pathlib import Path

from azure.core.credentials import TokenCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    SearchableField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
)

from agents.hosted.rag import SUPPORTED_DOCUMENT_SUFFIXES, chunk_text


logger = logging.getLogger(__name__)


def load_documents(documents_path: Path) -> list[dict[str, str]]:
    documents_path = documents_path.resolve()
    if not documents_path.is_dir():
        raise RuntimeError(
            f"RAG documents directory does not exist: {documents_path}"
        )

    documents: list[dict[str, str]] = []
    for path in sorted(documents_path.rglob("*")):
        if (
            not path.is_file()
            or path.suffix.lower() not in SUPPORTED_DOCUMENT_SUFFIXES
        ):
            continue

        relative_path = path.relative_to(documents_path).as_posix()
        document_id = re.sub(r"[^a-z0-9_-]", "-", relative_path.lower())
        for index, content in enumerate(
            chunk_text(path.read_text(encoding="utf-8")), start=1
        ):
            documents.append(
                {
                    "id": f"{document_id}-{index}",
                    "content": content,
                    "sourceName": path.name,
                    "sourceLink": relative_path,
                }
            )

    if not documents:
        raise RuntimeError(f"No .md or .txt documents found in {documents_path}")
    return documents


def build_index(index_name: str) -> SearchIndex:
    return SearchIndex(
        name=index_name,
        fields=[
            SimpleField(
                name="id",
                type=SearchFieldDataType.String,
                key=True,
                filterable=True,
            ),
            SearchableField(name="content", type=SearchFieldDataType.String),
            SearchableField(name="sourceName", type=SearchFieldDataType.String),
            SimpleField(
                name="sourceLink",
                type=SearchFieldDataType.String,
                filterable=True,
            ),
        ],
    )


def initialize_search_index(
    *,
    endpoint: str,
    index_name: str,
    documents_path: Path,
    credential: TokenCredential,
) -> int:
    documents = load_documents(documents_path)
    logger.info(
        "Initializing Azure AI Search index %s with %d document chunk(s)",
        index_name,
        len(documents),
    )

    with SearchIndexClient(endpoint=endpoint, credential=credential) as index_client:
        index_client.create_or_update_index(build_index(index_name))

    with SearchClient(
        endpoint=endpoint,
        index_name=index_name,
        credential=credential,
    ) as search_client:
        result = search_client.merge_or_upload_documents(documents)

    failures = [item for item in result if not item.succeeded]
    if failures:
        failure_details = ", ".join(
            f"{item.key}: {item.error_message}" for item in failures
        )
        raise RuntimeError(f"Failed to index documents: {failure_details}")

    logger.info(
        "Initialized Azure AI Search index %s with %d document chunk(s)",
        index_name,
        len(documents),
    )
    return len(documents)
