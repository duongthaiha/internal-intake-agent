"""Backfill persisted Azure AI Search projections in intake Cosmos records."""

import argparse
import asyncio
import os
from dataclasses import dataclass

from azure.core import MatchConditions
from azure.cosmos import exceptions
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv

from intake_api.search_projection import build_search_projection


def get_required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class BackfillResult:
    examined: int
    updated: int


async def backfill_search_projections(
    container: ContainerProxy,
    *,
    dry_run: bool,
) -> BackfillResult:
    examined = 0
    updated = 0
    items = container.query_items(
        query=(
            "SELECT c.id, c.tenantId, c.intake, c.searchTitle, "
            "c.searchText, c._etag FROM c"
        ),
    )
    async for item in items:
        examined += 1
        intake = item.get("intake")
        if not isinstance(intake, dict):
            raise RuntimeError(
                f"Intake record '{item.get('id', '<unknown>')}' has no object intake."
            )
        search_title, search_text = build_search_projection(intake)
        if (
            item.get("searchTitle") == search_title
            and item.get("searchText") == search_text
        ):
            continue

        updated += 1
        if dry_run:
            continue
        try:
            await container.patch_item(
                item=item["id"],
                partition_key=[item["tenantId"], item["id"]],
                patch_operations=[
                    {
                        "op": "set",
                        "path": "/searchTitle",
                        "value": search_title,
                    },
                    {
                        "op": "set",
                        "path": "/searchText",
                        "value": search_text,
                    },
                ],
                etag=item["_etag"],
                match_condition=MatchConditions.IfNotModified,
            )
        except exceptions.CosmosAccessConditionFailedError as exc:
            raise RuntimeError(
                f"Intake record '{item['id']}' changed during backfill. "
                "Retry the command."
            ) from exc

    return BackfillResult(examined=examined, updated=updated)


async def run(dry_run: bool) -> BackfillResult:
    credential = DefaultAzureCredential()
    client = CosmosClient(
        get_required_setting("INTAKE_COSMOS_ENDPOINT"),
        credential=credential,
    )
    try:
        container = client.get_database_client(
            get_required_setting("INTAKE_COSMOS_DATABASE_NAME")
        ).get_container_client(
            get_required_setting("INTAKE_COSMOS_CONTAINER_NAME")
        )
        return await backfill_search_projections(
            container,
            dry_run=dry_run,
        )
    finally:
        await client.close()
        await credential.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Backfill persisted intake Search projection fields."
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    result = asyncio.run(run(args.dry_run))
    action = "would update" if args.dry_run else "updated"
    print(
        f"Examined {result.examined} intake record(s); "
        f"{action} {result.updated}."
    )


if __name__ == "__main__":
    main()
