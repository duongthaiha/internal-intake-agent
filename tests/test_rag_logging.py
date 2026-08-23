import unittest
from collections import Counter

from agent_framework import Message

from agents.hosted.rag import (
    KnowledgeChunk,
    _log_knowledge_chunks,
    _log_message_chunks,
)


class RagLoggingTests(unittest.TestCase):
    def test_in_memory_debug_log_includes_source_and_chunk(self) -> None:
        result = KnowledgeChunk(
            chunk_id="guide.md#1",
            source_name="guide.md",
            source_link="guide.md",
            content="Retrieved chunk text.",
            term_counts=Counter(),
        )

        with self.assertLogs("agents.hosted.rag", level="DEBUG") as logs:
            _log_knowledge_chunks([result])

        self.assertIn("source=guide.md", logs.output[0])
        self.assertIn("chunk_id=guide.md#1", logs.output[0])
        self.assertIn("chunk=Retrieved chunk text.", logs.output[0])

    def test_azure_search_debug_log_parses_source_prefix(self) -> None:
        result = Message(
            role="user",
            contents=["[Source: guide-md-1] Retrieved chunk text."],
        )

        with self.assertLogs("agents.hosted.rag", level="DEBUG") as logs:
            _log_message_chunks("Azure AI Search semantic", [result])

        self.assertIn("source=guide-md-1", logs.output[0])
        self.assertIn("chunk=Retrieved chunk text.", logs.output[0])


if __name__ == "__main__":
    unittest.main()
