"""Provision the Azure AI Search indexer pipeline for intake Cosmos records."""

import argparse
import os
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv


SEARCH_SCOPE = "https://search.azure.com/.default"
SEARCH_API_VERSION = "2025-09-01"


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def get_positive_integer(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < 1:
        raise RuntimeError(f"{name} must be greater than zero.")
    return value


@dataclass(frozen=True)
class IntakeSearchConfig:
    search_endpoint: str
    cosmos_account_resource_id: str
    cosmos_database_name: str
    cosmos_container_name: str
    index_name: str
    data_source_name: str
    skillset_name: str
    indexer_name: str
    indexer_interval: str
    openai_endpoint: str
    embedding_deployment_name: str
    embedding_model_name: str
    embedding_dimensions: int

    @classmethod
    def from_environment(cls) -> "IntakeSearchConfig":
        index_name = get_required_setting("INTAKE_SEARCH_INDEX_NAME")
        return cls(
            search_endpoint=get_required_setting("AZURE_SEARCH_ENDPOINT"),
            cosmos_account_resource_id=get_required_setting(
                "INTAKE_COSMOS_ACCOUNT_RESOURCE_ID"
            ),
            cosmos_database_name=get_required_setting(
                "INTAKE_COSMOS_DATABASE_NAME"
            ),
            cosmos_container_name=get_required_setting(
                "INTAKE_COSMOS_CONTAINER_NAME"
            ),
            index_name=index_name,
            data_source_name=os.getenv(
                "INTAKE_SEARCH_DATA_SOURCE_NAME",
                f"{index_name}-cosmos",
            ),
            skillset_name=os.getenv(
                "INTAKE_SEARCH_SKILLSET_NAME",
                f"{index_name}-skillset",
            ),
            indexer_name=os.getenv(
                "INTAKE_SEARCH_INDEXER_NAME",
                f"{index_name}-indexer",
            ),
            indexer_interval=os.getenv(
                "INTAKE_SEARCH_INDEXER_INTERVAL",
                "PT15M",
            ),
            openai_endpoint=get_required_setting(
                "FOUNDRY_IQ_OPENAI_ENDPOINT"
            ),
            embedding_deployment_name=get_required_setting(
                "FOUNDRY_IQ_EMBEDDING_DEPLOYMENT_NAME"
            ),
            embedding_model_name=get_required_setting(
                "FOUNDRY_IQ_EMBEDDING_MODEL_NAME"
            ),
            embedding_dimensions=get_positive_integer(
                "INTAKE_SEARCH_EMBEDDING_DIMENSIONS",
                3072,
            ),
        )


def build_index(config: IntakeSearchConfig) -> dict[str, object]:
    vectorizer_name = f"{config.index_name}-vectorizer"
    algorithm_name = f"{config.index_name}-hnsw"
    profile_name = f"{config.index_name}-profile"
    return {
        "name": config.index_name,
        "fields": [
            {
                "name": "id",
                "type": "Edm.String",
                "key": True,
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "tenantId",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "createdBy",
                "type": "Edm.String",
                "filterable": True,
                "retrievable": True,
            },
            {
                "name": "status",
                "type": "Edm.String",
                "filterable": True,
                "facetable": True,
                "retrievable": True,
            },
            {
                "name": "updatedAt",
                "type": "Edm.DateTimeOffset",
                "filterable": True,
                "sortable": True,
                "retrievable": True,
            },
            {
                "name": "searchTitle",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "searchText",
                "type": "Edm.String",
                "searchable": True,
                "retrievable": True,
            },
            {
                "name": "searchVector",
                "type": "Collection(Edm.Single)",
                "searchable": True,
                "retrievable": False,
                "stored": False,
                "dimensions": config.embedding_dimensions,
                "vectorSearchProfile": profile_name,
            },
        ],
        "vectorSearch": {
            "algorithms": [
                {
                    "name": algorithm_name,
                    "kind": "hnsw",
                }
            ],
            "profiles": [
                {
                    "name": profile_name,
                    "algorithm": algorithm_name,
                    "vectorizer": vectorizer_name,
                }
            ],
            "vectorizers": [
                {
                    "name": vectorizer_name,
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": config.openai_endpoint,
                        "deploymentId": config.embedding_deployment_name,
                        "modelName": config.embedding_model_name,
                    },
                }
            ],
        },
    }


def build_data_source(config: IntakeSearchConfig) -> dict[str, object]:
    return {
        "name": config.data_source_name,
        "type": "cosmosdb",
        "credentials": {
            "connectionString": (
                f"ResourceId={config.cosmos_account_resource_id};"
                f"Database={config.cosmos_database_name};"
                "IdentityAuthType=AccessToken"
            )
        },
        "container": {
            "name": config.cosmos_container_name,
            "query": (
                "SELECT c.id, c.tenantId, c.createdBy, c.status, "
                "c.updatedAt, c.searchTitle, c.searchText, c._ts "
                "FROM c WHERE IS_DEFINED(c.searchText) "
                "AND NOT IS_NULL(c.searchText)"
            ),
        },
        "dataChangeDetectionPolicy": {
            "@odata.type": (
                "#Microsoft.Azure.Search.HighWaterMarkChangeDetectionPolicy"
            ),
            "highWaterMarkColumnName": "_ts",
        },
    }


def build_skillset(config: IntakeSearchConfig) -> dict[str, object]:
    return {
        "name": config.skillset_name,
        "description": "Vectorizes the persisted intake search projection.",
        "skills": [
            {
                "@odata.type": (
                    "#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill"
                ),
                "name": "intake-embedding",
                "description": "Generate an embedding for the intake record.",
                "context": "/document",
                "resourceUri": config.openai_endpoint,
                "deploymentId": config.embedding_deployment_name,
                "modelName": config.embedding_model_name,
                "dimensions": config.embedding_dimensions,
                "inputs": [
                    {
                        "name": "text",
                        "source": "/document/searchText",
                    }
                ],
                "outputs": [
                    {
                        "name": "embedding",
                        "targetName": "searchVector",
                    }
                ],
            }
        ],
    }


def build_indexer(config: IntakeSearchConfig) -> dict[str, object]:
    return {
        "name": config.indexer_name,
        "dataSourceName": config.data_source_name,
        "targetIndexName": config.index_name,
        "skillsetName": config.skillset_name,
        "schedule": {
            "interval": config.indexer_interval,
        },
        "parameters": {
            "batchSize": 100,
            "maxFailedItems": 0,
            "maxFailedItemsPerBatch": 0,
            "configuration": {
                "executionEnvironment": "private",
            },
        },
        "outputFieldMappings": [
            {
                "sourceFieldName": "/document/searchVector",
                "targetFieldName": "searchVector",
            }
        ],
    }


class SearchRestClient:
    def __init__(
        self,
        endpoint: str,
        credential: DefaultAzureCredential,
    ) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._credential = credential
        self._client = httpx.Client(timeout=60)

    def close(self) -> None:
        self._client.close()

    def _headers(self) -> dict[str, str]:
        token = self._credential.get_token(SEARCH_SCOPE)
        return {
            "Authorization": "Bearer " + token.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, collection: str, name: str, suffix: str = "") -> str:
        return (
            f"{self._endpoint}/{collection}/{quote(name, safe='')}{suffix}"
            f"?api-version={SEARCH_API_VERSION}"
        )

    def put(
        self,
        collection: str,
        name: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        response = self._client.put(
            self._url(collection, name),
            headers=self._headers(),
            json=payload,
        )
        self._raise_for_status(response, f"create or update {collection}/{name}")
        return response.json()

    def get(self, collection: str, name: str) -> dict[str, object]:
        response = self._client.get(
            self._url(collection, name),
            headers=self._headers(),
        )
        self._raise_for_status(response, f"read {collection}/{name}")
        return response.json()

    def post(self, collection: str, name: str, suffix: str) -> None:
        response = self._client.post(
            self._url(collection, name, suffix),
            headers=self._headers(),
        )
        self._raise_for_status(response, f"invoke {collection}/{name}{suffix}")

    @staticmethod
    def _raise_for_status(response: httpx.Response, operation: str) -> None:
        if response.is_success:
            return
        raise RuntimeError(
            f"Azure AI Search failed to {operation}: "
            f"HTTP {response.status_code}: {response.text[:1000]}"
        )


def provision_intake_search(
    client: SearchRestClient,
    config: IntakeSearchConfig,
    *,
    run_indexer: bool,
) -> None:
    client.put("indexes", config.index_name, build_index(config))
    client.put(
        "datasources",
        config.data_source_name,
        build_data_source(config),
    )
    client.put("skillsets", config.skillset_name, build_skillset(config))
    client.put("indexers", config.indexer_name, build_indexer(config))

    for collection, name in (
        ("indexes", config.index_name),
        ("datasources", config.data_source_name),
        ("skillsets", config.skillset_name),
        ("indexers", config.indexer_name),
    ):
        if client.get(collection, name).get("name") != name:
            raise RuntimeError(
                f"Azure AI Search validation failed for {collection}/{name}."
            )

    if run_indexer:
        client.post("indexers", config.indexer_name, "/run")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Provision intake Cosmos indexing in Azure AI Search."
    )
    parser.add_argument(
        "--run-indexer",
        action="store_true",
        help="Run the indexer immediately after provisioning.",
    )
    args = parser.parse_args()

    config = IntakeSearchConfig.from_environment()
    credential = DefaultAzureCredential()
    client = SearchRestClient(config.search_endpoint, credential)
    try:
        provision_intake_search(
            client,
            config,
            run_indexer=args.run_indexer,
        )
    finally:
        client.close()
        credential.close()

    print(
        f"Provisioned intake Search index '{config.index_name}' and "
        f"indexer '{config.indexer_name}'."
    )


if __name__ == "__main__":
    main()
