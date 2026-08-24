import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from agents.hosted.hosted_agent import initialize_rag
from agents.hosted.rag import (
    FoundryIqContextProvider,
    FoundryIqRetrieval,
    build_rag_provider,
    parse_foundry_iq_retrieval,
)


class FakeAsyncCredential:
    def __init__(self) -> None:
        self.scopes: list[str] = []

    async def get_token(self, *scopes: str) -> SimpleNamespace:
        self.scopes.extend(scopes)
        return SimpleNamespace(token="test-token")


class FakeContext:
    def __init__(self, text: str) -> None:
        self.input_messages = [SimpleNamespace(role="user", text=text)]
        self.extensions: list[tuple[str, list[object]]] = []

    def extend_messages(self, source_id: str, messages: list[object]) -> None:
        self.extensions.append((source_id, messages))


def retrieval_payload() -> dict[str, object]:
    return {
        "response": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Only the Recruitment Team may issue the offer.",
                    }
                ],
            }
        ],
        "references": [
            {
                "type": "azureBlob",
                "id": "0",
                "docKey": "contoso-hr-staff-recruitment-sop.md",
            }
        ],
        "activity": [
            {
                "type": "azureBlob",
                "knowledgeSourceName": "intake-source",
                "count": 1,
            },
            {"type": "agenticReasoning"},
            {"type": "modelAnswerSynthesis"},
        ],
    }


class FoundryIqProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_closes_http_client(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=FakeAsyncCredential(),
            client=client,
        )

        await provider.close()

        client.aclose.assert_awaited_once_with()

    async def test_retrieve_uses_bearer_token_and_knowledge_base_route(
        self,
    ) -> None:
        requests: list[httpx.Request] = []

        async def handle_request(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=retrieval_payload())

        credential = FakeAsyncCredential()
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handle_request)
        )
        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net/",
            knowledge_base_name="intake kb",
            credential=credential,
            client=client,
        )
        try:
            result = await provider.retrieve("Who may issue an offer?")
        finally:
            await provider.close()

        self.assertEqual(
            requests[0].url.raw_path.decode(),
            (
                "/knowledgebases('intake%20kb')/retrieve"
                "?api-version=2026-05-01-preview"
            ),
        )
        self.assertEqual(
            requests[0].headers["authorization"],
            "Bearer test-token",
        )
        self.assertEqual(
            json.loads(requests[0].content)["intents"][0],
            {
                "type": "semantic",
                "search": "Who may issue an offer?",
            },
        )
        self.assertEqual(
            result.sources,
            ("contoso-hr-staff-recruitment-sop.md",),
        )
        self.assertIn("Recruitment Team", result.text)

    async def test_retrieve_surfaces_http_failure_without_response_body(
        self,
    ) -> None:
        async def handle_request(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                json={"error": {"message": "sensitive upstream detail"}},
            )

        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=FakeAsyncCredential(),
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(handle_request)
            ),
        )
        try:
            with self.assertRaisesRegex(
                RuntimeError,
                r"Foundry IQ retrieval failed: HTTP 502\.",
            ) as raised:
                await provider.retrieve("Question")
        finally:
            await provider.close()

        self.assertNotIn("sensitive upstream detail", str(raised.exception))

    async def test_before_run_injects_grounding_and_source(self) -> None:
        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=FakeAsyncCredential(),
            client=httpx.AsyncClient(),
        )
        provider.retrieve = AsyncMock(
            return_value=FoundryIqRetrieval(
                text="Grounded answer. ",
                sources=("recruitment.md",),
                activity_types=("azureBlob", "modelAnswerSynthesis"),
            )
        )
        context = FakeContext("What is the recruitment process?")
        try:
            with self.assertLogs("agents.hosted.rag", level="DEBUG") as logs:
                await provider.before_run(
                    agent=None,
                    session=None,
                    context=context,
                    state={},
                )
        finally:
            await provider.close()

        provider.retrieve.assert_awaited_once_with(
            "What is the recruitment process?"
        )
        injected = context.extensions[0][1][0].text
        self.assertIn("Grounded answer", injected)
        self.assertIn("recruitment.md", injected)
        self.assertIn("untrusted", injected)
        self.assertNotIn("Grounded answer", "\n".join(logs.output))

    async def test_before_run_handles_valid_empty_result(self) -> None:
        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=FakeAsyncCredential(),
            client=httpx.AsyncClient(),
        )
        provider.retrieve = AsyncMock(
            return_value=FoundryIqRetrieval(
                text="",
                sources=(),
                activity_types=("agenticReasoning",),
            )
        )
        context = FakeContext("Unknown question")
        try:
            await provider.before_run(
                agent=None,
                session=None,
                context=context,
                state={},
            )
        finally:
            await provider.close()

        self.assertIn("insufficient", context.extensions[0][1][0].text)

    async def test_before_run_requires_sources_for_grounding(self) -> None:
        provider = FoundryIqContextProvider(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=FakeAsyncCredential(),
            client=httpx.AsyncClient(),
        )
        provider.retrieve = AsyncMock(
            return_value=FoundryIqRetrieval(
                text="An uncited synthesized response.",
                sources=(),
                activity_types=("modelAnswerSynthesis",),
            )
        )
        context = FakeContext("Question")
        try:
            await provider.before_run(
                agent=None,
                session=None,
                context=context,
                state={},
            )
        finally:
            await provider.close()

        injected = context.extensions[0][1][0].text
        self.assertIn("insufficient", injected)
        self.assertNotIn("uncited synthesized response", injected)

    async def test_build_provider_selects_foundry_iq(self) -> None:
        environment = {
            "RAG_PROVIDER": "foundry_iq",
            "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
            "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME": "intake-kb",
        }
        with patch.dict(os.environ, environment, clear=True):
            provider_name, provider = build_rag_provider(
                FakeAsyncCredential()
            )

        self.assertEqual(provider_name, "foundry_iq")
        self.assertIsInstance(provider, FoundryIqContextProvider)
        await provider.close()


class FoundryIqParsingTests(unittest.TestCase):
    def test_parse_rejects_failed_activity(self) -> None:
        payload = retrieval_payload()
        payload["activity"] = [
            {
                "type": "azureBlob",
                "error": {"message": "vectorization failed"},
            }
        ]

        with self.assertRaisesRegex(
            RuntimeError,
            "failed knowledge-source activity",
        ):
            parse_foundry_iq_retrieval(payload)

    def test_parse_rejects_missing_response_array(self) -> None:
        payload = retrieval_payload()
        del payload["response"]

        with self.assertRaisesRegex(RuntimeError, "missing the response array"):
            parse_foundry_iq_retrieval(payload)

    def test_parse_rejects_invalid_reference(self) -> None:
        payload = retrieval_payload()
        payload["references"] = ["invalid"]

        with self.assertRaisesRegex(RuntimeError, "invalid reference"):
            parse_foundry_iq_retrieval(payload)


class FoundryIqStartupTests(unittest.TestCase):
    @patch("agents.hosted.hosted_agent.DefaultAzureCredential")
    @patch("agents.hosted.hosted_agent.verify_foundry_iq_access")
    def test_startup_check_verifies_configured_knowledge_base(
        self,
        verify_access,
        credential_type,
    ) -> None:
        environment = {
            "RAG_PROVIDER": "foundry_iq",
            "RAG_AUTO_INDEX": "false",
            "FOUNDRY_IQ_STARTUP_CHECK": "true",
            "AZURE_SEARCH_ENDPOINT": "https://example.search.windows.net",
            "FOUNDRY_IQ_KNOWLEDGE_BASE_NAME": "intake-kb",
        }
        credential = credential_type.return_value

        with patch.dict(os.environ, environment, clear=True):
            initialize_rag()

        verify_access.assert_called_once_with(
            endpoint="https://example.search.windows.net",
            knowledge_base_name="intake-kb",
            credential=credential,
        )
        credential.close.assert_called_once()

    def test_foundry_iq_rejects_startup_indexing(self) -> None:
        environment = {
            "RAG_PROVIDER": "foundry_iq",
            "RAG_AUTO_INDEX": "true",
        }

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "RAG_AUTO_INDEX must be false",
            ):
                initialize_rag()


if __name__ == "__main__":
    unittest.main()
