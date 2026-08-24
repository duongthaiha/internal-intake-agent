import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from scripts.provision_foundry_iq import (
    FoundryIqConfig,
    SearchRestClient,
    build_knowledge_base,
    build_knowledge_source,
    provision_foundry_iq,
)


class FoundryIqTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = FoundryIqConfig(
            search_endpoint="https://example.search.windows.net",
            storage_account_id=(
                "/subscriptions/sub/resourceGroups/rg/providers/"
                "Microsoft.Storage/storageAccounts/knowledge"
            ),
            container_name="knowledge",
            knowledge_source_name="intake-source",
            knowledge_base_name="intake-kb",
            ingestion_interval="PT1H",
            openai_endpoint="https://example.openai.azure.com/",
            embedding_deployment_name="foundry-iq-embedding",
            embedding_model_name="text-embedding-3-large",
            chat_deployment_name="foundry-iq-chat",
            chat_model_name="gpt-5.4-mini",
        )

    def test_blob_source_uses_managed_identity_and_hourly_ingestion(self) -> None:
        payload = build_knowledge_source(self.config)
        blob = payload["azureBlobParameters"]
        ingestion = blob["ingestionParameters"]

        self.assertEqual(
            blob["connectionString"],
            f"ResourceId={self.config.storage_account_id};",
        )
        self.assertNotIn("apiKey", str(payload))
        self.assertEqual(ingestion["ingestionSchedule"], {"interval": "PT1H"})
        self.assertEqual(ingestion["contentExtractionMode"], "minimal")
        self.assertTrue(ingestion["disableImageVerbalization"])

    def test_knowledge_base_uses_answer_synthesis_and_safe_instructions(self) -> None:
        payload = build_knowledge_base(self.config)

        self.assertEqual(payload["outputMode"], "answerSynthesis")
        self.assertEqual(
            payload["knowledgeSources"],
            [{"name": self.config.knowledge_source_name}],
        )
        self.assertIn("untrusted", payload["answerInstructions"])
        self.assertIn("citations", payload["answerInstructions"])
        self.assertEqual(
            payload["models"][0]["azureOpenAIParameters"]["deploymentId"],
            self.config.chat_deployment_name,
        )

    def test_provisioning_is_repeatable(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.resources: dict[tuple[str, str], dict[str, object]] = {}

            def put(
                self,
                collection: str,
                name: str,
                payload: dict[str, object],
            ) -> dict[str, object]:
                self.resources[(collection, name)] = payload
                return payload

            def get(self, collection: str, name: str) -> dict[str, object]:
                return self.resources[(collection, name)]

        client = FakeClient()
        provision_foundry_iq(client, self.config)
        first_resources = dict(client.resources)
        provision_foundry_iq(client, self.config)

        self.assertEqual(client.resources, first_resources)
        self.assertEqual(len(client.resources), 2)

    def test_missing_configuration_is_actionable(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "Missing required environment variable: AZURE_SEARCH_ENDPOINT",
            ):
                FoundryIqConfig.from_environment()

    def test_rest_client_uses_preview_odata_routes_and_bearer_auth(self) -> None:
        requests: list[httpx.Request] = []

        def handle_request(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={"name": "intake-kb", "references": [{"id": "doc-1"}]},
            )

        class FakeCredential:
            def get_token(self, *scopes: str) -> SimpleNamespace:
                return SimpleNamespace(token="test-token")

        client = SearchRestClient(self.config.search_endpoint, FakeCredential())
        client._client.close()
        client._client = httpx.Client(
            transport=httpx.MockTransport(handle_request)
        )
        try:
            client.put("knowledgebases", "intake-kb", {"name": "intake-kb"})
            client.retrieve("intake-kb", "What is the policy?")
        finally:
            client.close()

        self.assertEqual(
            requests[0].url.path,
            "/knowledgebases('intake-kb')",
        )
        self.assertEqual(requests[0].headers["prefer"], "return=representation")
        self.assertEqual(
            requests[1].url.path,
            "/knowledgebases('intake-kb')/retrieve",
        )
        self.assertEqual(
            requests[1].headers["authorization"],
            "Bearer test-token",
        )


if __name__ == "__main__":
    unittest.main()
