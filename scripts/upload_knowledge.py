import argparse
import os
from pathlib import Path
from typing import Protocol

from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from dotenv import load_dotenv


class BlobContainer(Protocol):
    def get_blob_client(self, blob: str): ...


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def find_markdown_documents(root: Path) -> list[Path]:
    root = root.resolve()
    if not root.is_dir():
        raise RuntimeError(f"Knowledge documents directory does not exist: {root}")

    documents = [
        path for path in sorted(root.rglob("*.md")) if path.is_file()
    ]
    if not documents:
        raise RuntimeError(f"No .md documents found in {root}")
    return documents


def upload_markdown_documents(container: BlobContainer, root: Path) -> int:
    root = root.resolve()
    documents = find_markdown_documents(root)
    for path in documents:
        blob_name = path.relative_to(root).as_posix()
        container.get_blob_client(blob_name).upload_blob(
            path.read_bytes(),
            overwrite=True,
            content_settings=ContentSettings(
                content_type="text/markdown; charset=utf-8",
            ),
        )
    return len(documents)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Upload repository Markdown files to the private Foundry IQ source "
            "container. Foundry IQ performs ingestion separately."
        )
    )
    parser.add_argument(
        "--documents",
        type=Path,
        default=Path("data/knowledge"),
    )
    args = parser.parse_args()

    credential = DefaultAzureCredential()
    try:
        with BlobServiceClient(
            account_url=get_required_setting(
                "FOUNDRY_IQ_STORAGE_BLOB_ENDPOINT"
            ),
            credential=credential,
        ) as service:
            container = service.get_container_client(
                get_required_setting("FOUNDRY_IQ_STORAGE_CONTAINER_NAME")
            )
            uploaded = upload_markdown_documents(container, args.documents)
    finally:
        credential.close()

    print(f"Uploaded {uploaded} Markdown document(s) for Foundry IQ ingestion.")


if __name__ == "__main__":
    main()
