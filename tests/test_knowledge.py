import tempfile
import unittest
from pathlib import Path
from scripts.upload_knowledge import upload_markdown_documents


class FakeBlobClient:
    def __init__(self, container: "FakeContainer", name: str) -> None:
        self.container = container
        self.name = name

    def upload_blob(self, payload: bytes, **kwargs) -> None:
        self.container.blobs[self.name] = payload
        self.container.upload_options[self.name] = kwargs


class FakeContainer:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.upload_options: dict[str, dict[str, object]] = {}

    def get_blob_client(self, name: str) -> FakeBlobClient:
        return FakeBlobClient(self, name)

class KnowledgeTests(unittest.TestCase):
    def test_uploads_markdown_and_preserves_relative_paths(self) -> None:
        container = FakeContainer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "guide.md").write_text("# Guide\n\nUseful text.", encoding="utf-8")
            nested = root / "policies"
            nested.mkdir()
            (nested / "access.md").write_text("# Access", encoding="utf-8")
            (root / "ignore.json").write_text("{}", encoding="utf-8")

            uploaded = upload_markdown_documents(container, root)

        self.assertEqual(uploaded, 2)
        self.assertEqual(
            list(container.blobs),
            ["guide.md", "policies/access.md"],
        )
        self.assertIn(b"Useful text.", container.blobs["guide.md"])
        content_settings = container.upload_options["guide.md"]["content_settings"]
        self.assertEqual(
            content_settings.content_type,
            "text/markdown; charset=utf-8",
        )
        self.assertIsNone(content_settings.content_encoding)

    def test_rejects_empty_markdown_source(self) -> None:
        container = FakeContainer()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ignore.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "No .md documents"):
                upload_markdown_documents(container, root)


if __name__ == "__main__":
    unittest.main()
