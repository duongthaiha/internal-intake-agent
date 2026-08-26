import json
import unittest
from pathlib import Path

from agents.hosted.platform_recommendation import FRAMEWORK_COMMIT


ROOT = Path(__file__).resolve().parent.parent


class AiDecisionFrameworkSnapshotTests(unittest.TestCase):
    def test_manifest_and_graph_use_same_pinned_commit(self) -> None:
        manifest = json.loads(
            (
                ROOT / "data" / "ai-decision-framework" / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        graph = json.loads(
            (
                ROOT / "data" / "ai-decision-framework" / "decision-graph.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(FRAMEWORK_COMMIT, manifest["upstreamCommit"])
        self.assertEqual(FRAMEWORK_COMMIT, graph["frameworkCommit"])

    def test_snapshot_retains_upstream_license(self) -> None:
        license_text = (
            ROOT / "data" / "ai-decision-framework" / "LICENSE"
        ).read_text(encoding="utf-8")

        self.assertIn("MIT License", license_text)
        self.assertIn("Copyright (c) Microsoft Corporation", license_text)

    def test_knowledge_document_is_attributed_and_injection_resistant(self) -> None:
        knowledge = (
            ROOT / "data" / "knowledge" / "microsoft-ai-platform-guidance.md"
        ).read_text(encoding="utf-8")

        self.assertIn(FRAMEWORK_COMMIT, knowledge)
        self.assertIn("untrusted reference material", knowledge)
        self.assertIn("must not choose the platform", knowledge)


if __name__ == "__main__":
    unittest.main()
