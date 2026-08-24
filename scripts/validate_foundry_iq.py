import argparse

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from scripts.provision_foundry_iq import FoundryIqConfig, SearchRestClient


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description=(
            "Validate Foundry IQ configuration and grounded retrieval. Run "
            "from a host with private connectivity to Azure AI Search."
        )
    )
    parser.add_argument(
        "--question",
        default="Which model deployment does this POC use?",
    )
    args = parser.parse_args()

    config = FoundryIqConfig.from_environment()
    credential = DefaultAzureCredential()
    client = SearchRestClient(config.search_endpoint, credential)
    try:
        source = client.get(
            "knowledgesources", config.knowledge_source_name
        )
        base = client.get("knowledgebases", config.knowledge_base_name)
        result = client.retrieve(config.knowledge_base_name, args.question)
    finally:
        client.close()
        credential.close()

    schedule = (
        source.get("azureBlobParameters", {})
        .get("ingestionParameters", {})
        .get("ingestionSchedule", {})
        .get("interval")
    )
    if schedule != config.ingestion_interval:
        raise RuntimeError(
            "Foundry IQ ingestion schedule does not match the configured "
            f"interval: expected {config.ingestion_interval}, found {schedule}."
        )
    if base.get("outputMode") != "answerSynthesis":
        raise RuntimeError("Foundry IQ knowledge base is not using answer synthesis.")

    references = result.get("references")
    if not isinstance(references, list) or not references:
        raise RuntimeError(
            "Foundry IQ retrieval returned no citations. Wait for the first "
            "indexer run and inspect the generated indexer status."
        )

    print(
        f"Foundry IQ retrieval returned {len(references)} cited reference(s)."
    )


if __name__ == "__main__":
    main()
