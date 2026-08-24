from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from azure.core import MatchConditions
from azure.cosmos import exceptions
from azure.cosmos.aio import ContainerProxy, CosmosClient
from azure.identity.aio import DefaultAzureCredential

from intake_api.models import IntakeRecord, RequestStatus


class RecordNotFoundError(LookupError):
    pass


class RecordConflictError(RuntimeError):
    pass


class RecordPreconditionError(RuntimeError):
    pass


class InvalidContinuationTokenError(RuntimeError):
    pass


class RepositoryUnavailableError(RuntimeError):
    pass


class RepositoryOperationError(RuntimeError):
    pass


class RepositoryThrottledError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Intake persistence is temporarily throttled.")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class RecordPage:
    items: list[IntakeRecord]
    continuation_token: str | None


class IntakeRepository(Protocol):
    async def create(self, record: IntakeRecord) -> IntakeRecord: ...

    async def get(self, tenant_id: str, request_id: str) -> IntakeRecord: ...

    async def list(
        self,
        tenant_id: str,
        *,
        created_by: str | None,
        request_status: RequestStatus | None,
        limit: int,
        continuation_token: str | None,
    ) -> RecordPage: ...

    async def replace(self, record: IntakeRecord, etag: str) -> IntakeRecord: ...

    async def check_ready(self) -> None: ...


class CosmosIntakeRepository:
    def __init__(
        self,
        client: CosmosClient,
        credential: DefaultAzureCredential,
        container: ContainerProxy,
    ) -> None:
        self._client = client
        self._credential = credential
        self._container = container

    @classmethod
    def create_client(
        cls,
        *,
        endpoint: str,
        database_name: str,
        container_name: str,
    ) -> "CosmosIntakeRepository":
        credential = DefaultAzureCredential()
        client = CosmosClient(endpoint, credential=credential)
        container = client.get_database_client(database_name).get_container_client(
            container_name
        )
        return cls(client, credential, container)

    async def close(self) -> None:
        await self._client.close()
        await self._credential.close()

    async def create(self, record: IntakeRecord) -> IntakeRecord:
        try:
            item = await self._container.create_item(
                record.model_dump(by_alias=True, mode="json", exclude={"etag"})
            )
        except exceptions.CosmosResourceExistsError as exc:
            raise RecordConflictError("Intake request already exists.") from exc
        except exceptions.CosmosHttpResponseError as exc:
            raise _translate_unavailable(exc) from exc
        return _record_from_item(item)

    async def get(self, tenant_id: str, request_id: str) -> IntakeRecord:
        try:
            item = await self._container.read_item(
                item=request_id,
                partition_key=[tenant_id, request_id],
            )
        except exceptions.CosmosResourceNotFoundError as exc:
            raise RecordNotFoundError("Intake request was not found.") from exc
        except exceptions.CosmosHttpResponseError as exc:
            raise _translate_unavailable(exc) from exc
        return _record_from_item(item)

    async def list(
        self,
        tenant_id: str,
        *,
        created_by: str | None,
        request_status: RequestStatus | None,
        limit: int,
        continuation_token: str | None,
    ) -> RecordPage:
        clauses = ["c.tenantId = @tenantId"]
        parameters: list[dict[str, Any]] = [
            {"name": "@tenantId", "value": tenant_id}
        ]
        if created_by is not None:
            clauses.append("c.createdBy = @createdBy")
            parameters.append({"name": "@createdBy", "value": created_by})
        if request_status is not None:
            clauses.append("c.status = @status")
            parameters.append({"name": "@status", "value": request_status.value})

        query = (
            "SELECT * FROM c WHERE "
            + " AND ".join(clauses)
            + " ORDER BY c.updatedAt DESC"
        )
        try:
            pages: AsyncIterator[AsyncIterator[dict[str, Any]]] = (
                self._container.query_items(
                    query=query,
                    parameters=parameters,
                    max_item_count=limit,
                ).by_page(continuation_token=continuation_token)
            )
            page = await anext(pages)
            items = [_record_from_item(item) async for item in page]
            next_token = getattr(pages, "continuation_token", None)
        except StopAsyncIteration:
            return RecordPage(items=[], continuation_token=None)
        except exceptions.CosmosHttpResponseError as exc:
            if exc.status_code == 400 and continuation_token is not None:
                raise InvalidContinuationTokenError(
                    "The continuation token is invalid for this request."
                ) from exc
            raise _translate_unavailable(exc) from exc
        return RecordPage(items=items, continuation_token=next_token)

    async def replace(self, record: IntakeRecord, etag: str) -> IntakeRecord:
        try:
            item = await self._container.replace_item(
                item=record.id,
                body=record.model_dump(
                    by_alias=True, mode="json", exclude={"etag"}
                ),
                etag=etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except exceptions.CosmosResourceNotFoundError as exc:
            raise RecordNotFoundError("Intake request was not found.") from exc
        except exceptions.CosmosAccessConditionFailedError as exc:
            raise RecordPreconditionError(
                "The intake request was modified by another caller."
            ) from exc
        except exceptions.CosmosHttpResponseError as exc:
            raise _translate_unavailable(exc) from exc
        return _record_from_item(item)

    async def check_ready(self) -> None:
        try:
            await self._container.read()
        except exceptions.CosmosHttpResponseError as exc:
            raise _translate_unavailable(exc) from exc


def _record_from_item(item: dict[str, Any]) -> IntakeRecord:
    data = dict(item)
    data["etag"] = data.pop("_etag", None)
    return IntakeRecord.model_validate(data)


def _translate_unavailable(
    error: exceptions.CosmosHttpResponseError,
) -> RuntimeError:
    if error.status_code == 429:
        headers = getattr(error, "headers", {}) or {}
        retry_after_ms = headers.get("x-ms-retry-after-ms", "1000")
        try:
            retry_after_seconds = max(
                1, (int(retry_after_ms) + 999) // 1000
            )
        except (TypeError, ValueError):
            retry_after_seconds = 1
        return RepositoryThrottledError(retry_after_seconds)
    if error.status_code == 408 or error.status_code >= 500:
        return RepositoryUnavailableError(
            f"Cosmos DB operation failed with status {error.status_code}."
        )
    return RepositoryOperationError(
        f"Cosmos DB rejected an operation with status {error.status_code}."
    )
