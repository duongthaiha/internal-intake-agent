import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import httpx

from scripts.provision_intake_search import (
    IntakeSearchConfig,
    build_data_source,
    build_index,
    build_indexer,
    build_skillset,
    provision_intake_search,
    SearchRestClient,
)


def config() -> IntakeSearchConfig:
    return IntakeSearchConfig(
        search_endpoint="https://search.example.test",
        cosmos_account_resource_id="/subscriptions/test/cosmos/intake",
        cosmos_database_name="intake",
        cosmos_container_name="intake-requests",
        index_name="intake-search",
        data_source_name="intake-search-cosmos",
        skillset_name="intake-search-skillset",
        indexer_name="intake-search-indexer",
        indexer_interval="PT15M",
        openai_endpoint="https://foundry.example.test/",
        embedding_deployment_name="embedding",
        embedding_model_name="text-embedding-3-large",
        embedding_dimensions=3072,
    )


class IntakeSearchProvisioningTests(unittest.TestCase):
    def test_search_client_uses_bearer_token(self) -> None:
        credential = SimpleNamespace(
            get_token=Mock(return_value=SimpleNamespace(token="test-token"))
        )
        client = SearchRestClient(
            "https://search.example.test",
            credential,
        )
        try:
            headers = client._headers()
        finally:
            client.close()

        self.assertEqual("Bearer test-token", headers["Authorization"])

    def test_search_client_accepts_empty_success_response(self) -> None:
        credential = SimpleNamespace(
            get_token=Mock(return_value=SimpleNamespace(token="test-token"))
        )
        client = SearchRestClient(
            "https://search.example.test",
            credential,
        )
        client._client = SimpleNamespace(
            put=Mock(
                return_value=httpx.Response(
                    204,
                    request=httpx.Request(
                        "PUT",
                        "https://search.example.test/indexes/test",
                    ),
                )
            ),
            close=Mock(),
        )
        try:
            response = client.put("indexes", "test", {})
        finally:
            client.close()

        self.assertEqual({}, response)

    def test_index_is_hybrid_ready_and_security_fields_are_filterable(
        self,
    ) -> None:
        payload = build_index(config())
        fields = {field["name"]: field for field in payload["fields"]}

        self.assertTrue(fields["tenantId"]["filterable"])
        self.assertTrue(fields["createdBy"]["filterable"])
        self.assertTrue(fields["status"]["filterable"])
        self.assertTrue(fields["searchText"]["searchable"])
        self.assertEqual(3072, fields["searchVector"]["dimensions"])
        self.assertFalse(fields["searchVector"]["retrievable"])
        self.assertEqual(
            "text-embedding-3-large",
            payload["vectorSearch"]["vectorizers"][0][
                "azureOpenAIParameters"
            ]["modelName"],
        )

    def test_data_source_uses_managed_identity_and_change_tracking(self) -> None:
        payload = build_data_source(config())

        self.assertEqual("cosmosdb", payload["type"])
        self.assertIn(
            "IdentityAuthType=AccessToken",
            payload["credentials"]["connectionString"],
        )
        self.assertEqual(
            "_ts",
            payload["dataChangeDetectionPolicy"][
                "highWaterMarkColumnName"
            ],
        )
        self.assertIn("IS_DEFINED(c.searchText)", payload["container"]["query"])
        self.assertIn(
            "c._ts > @HighWaterMark",
            payload["container"]["query"],
        )

    def test_skillset_and_indexer_map_the_embedding(self) -> None:
        skill = build_skillset(config())["skills"][0]
        indexer = build_indexer(config())

        self.assertEqual(
            "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill",
            skill["@odata.type"],
        )
        self.assertEqual(3072, skill["dimensions"])
        self.assertEqual("PT15M", indexer["schedule"]["interval"])
        self.assertEqual(
            "private",
            indexer["parameters"]["configuration"]["executionEnvironment"],
        )
        self.assertEqual(
            "searchVector",
            indexer["outputFieldMappings"][0]["targetFieldName"],
        )

    def test_provisioning_orders_resources_and_optionally_runs_indexer(
        self,
    ) -> None:
        client = SimpleNamespace(
            put=Mock(),
            get=Mock(
                side_effect=[
                    {"name": "intake-search"},
                    {"name": "intake-search-cosmos"},
                    {"name": "intake-search-skillset"},
                    {"name": "intake-search-indexer"},
                ]
            ),
            post=Mock(),
        )

        provision_intake_search(client, config(), run_indexer=True)

        self.assertEqual(
            ["indexes", "datasources", "skillsets", "indexers"],
            [call.args[0] for call in client.put.call_args_list],
        )
        client.post.assert_called_once_with(
            "indexers",
            "intake-search-indexer",
            "/run",
        )


if __name__ == "__main__":
    unittest.main()
