import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agents.hosted.knowledge import (
    load_blob_documents,
    upload_local_documents,
)


class FakeDownload:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def readall(self) -> bytes:
        return self.payload


class FakeBlobClient:
    def __init__(self, container: "FakeContainer", name: str) -> None:
        self.container = container
        self.name = name

    def upload_blob(self, payload: bytes, **kwargs) -> None:
        self.container.blobs[self.name] = payload


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    def get_blob_client(self, name: str) -> FakeBlobClient:
        return FakeBlobClient(self, name)

    def list_blobs(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in self.blobs]

    def download_blob(self, name: str) -> FakeDownload:
        return FakeDownload(self.blobs[name])


class KnowledgeTests(unittest.TestCase):
    def test_upload_and_load_supported_documents(self) -> None:
        container = FakeContainer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide.md").write_text("# Guide\n\nUseful text.", encoding="utf-8")
            (root / "ignore.json").write_text("{}", encoding="utf-8")

            uploaded = upload_local_documents(container, root)

        documents = load_blob_documents(container)
        self.assertEqual(uploaded, 1)
        self.assertEqual(list(container.blobs), ["guide.md"])
        self.assertEqual(documents[0]["sourceName"], "guide.md")
        self.assertEqual(documents[0]["sourceLink"], "guide.md")
        self.assertIn("Useful text.", documents[0]["content"])

    def test_load_blob_documents_rejects_empty_supported_content(self) -> None:
        container = FakeContainer()
        container.blobs["ignore.json"] = b"{}"

        with self.assertRaisesRegex(RuntimeError, "No .md or .txt"):
            load_blob_documents(container)


if __name__ == "__main__":
    unittest.main()

