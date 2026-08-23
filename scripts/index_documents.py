import argparse
import os
from pathlib import Path

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from agents.hosted.search_index import initialize_search_index


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Create the Azure AI Search RAG index and upload local documents."
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path(os.getenv("RAG_DOCUMENTS_PATH", "data/knowledge")),
    )
    args = parser.parse_args()

    credential = DefaultAzureCredential()
    try:
        document_count = initialize_search_index(
            endpoint=get_required_setting("AZURE_SEARCH_ENDPOINT"),
            index_name=get_required_setting("AZURE_SEARCH_INDEX_NAME"),
            documents_path=args.documents,
            credential=credential,
        )
    finally:
        credential.close()

    print(
        f"Indexed {document_count} document chunks into "
        f"'{get_required_setting('AZURE_SEARCH_INDEX_NAME')}'."
    )


if __name__ == "__main__":
    main()
