import unittest
from datetime import UTC, datetime
from typing import Any

from azure.cosmos import exceptions

from intake_api.models import IntakeRecord, RequestStatus
from intake_api.repository import (
    CosmosIntakeRepository,
    InvalidContinuationTokenError,
    RepositoryOperationError,
    RepositoryThrottledError,
    RepositoryUnavailableError,
    _translate_unavailable,
)


def record_item() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "id": "request-a",
        "tenantId": "tenant-a",
        "createdBy": "owner-a",
        "status": "draft",
        "schemaVersion": "1.0.0",
        "createdAt": now,
        "updatedAt": now,
        "submittedAt": None,
        "intake": {"title": "A test request"},
        "_etag": '"v1"',
    }


class AsyncItems:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "AsyncItems":
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class AsyncPages:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self._returned = False
        self.continuation_token = "next-page"

    def __aiter__(self) -> "AsyncPages":
        return self

    async def __anext__(self) -> AsyncItems:
        if self._returned:
            raise StopAsyncIteration
        self._returned = True
        return AsyncItems(self._items)


class PagedQuery:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self._items = items
        self.received_token: str | None = None

    def by_page(self, continuation_token: str | None = None) -> AsyncPages:
        self.received_token = continuation_token
        return AsyncPages(self._items)


class ErrorPages:
    def __aiter__(self) -> "ErrorPages":
        return self

    async def __anext__(self) -> AsyncItems:
        raise exceptions.CosmosHttpResponseError(
            status_code=400,
            message="invalid continuation",
        )


class ErrorPagedQuery:
    def by_page(self, continuation_token: str | None = None) -> ErrorPages:
        return ErrorPages()


class FakeContainer:
    def __init__(self) -> None:
        self.query: str | None = None
        self.parameters: list[dict[str, Any]] = []
        self.max_item_count: int | None = None
        self.paged_query = PagedQuery([record_item()])
        self.read_partition_key: list[str] | None = None

    def query_items(
        self,
        *,
        query: str,
        parameters: list[dict[str, Any]],
        max_item_count: int,
    ) -> PagedQuery:
        self.query = query
        self.parameters = parameters
        self.max_item_count = max_item_count
        return self.paged_query

    async def read_item(
        self, *, item: str, partition_key: list[str]
    ) -> dict[str, Any]:
        self.read_partition_key = partition_key
        return record_item()


class CosmosRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_point_read_uses_complete_hierarchical_partition_key(
        self,
    ) -> None:
        container = FakeContainer()
        repository = CosmosIntakeRepository(None, None, container)
        result = await repository.get("tenant-a", "request-a")
        self.assertEqual(["tenant-a", "request-a"], container.read_partition_key)
        self.assertEqual('"v1"', result.etag)

    async def test_list_query_targets_tenant_and_owner_prefix(self) -> None:
        container = FakeContainer()
        repository = CosmosIntakeRepository(None, None, container)
        page = await repository.list(
            "tenant-a",
            created_by="owner-a",
            request_status=RequestStatus.DRAFT,
            limit=10,
            continuation_token="prior-page",
        )
        self.assertIn("c.tenantId = @tenantId", container.query)
        self.assertIn("c.createdBy = @createdBy", container.query)
        self.assertIn("c.status = @status", container.query)
        self.assertEqual(10, container.max_item_count)
        self.assertEqual("prior-page", container.paged_query.received_token)
        self.assertEqual("next-page", page.continuation_token)
        self.assertEqual(1, len(page.items))

    async def test_invalid_continuation_token_is_a_client_error(self) -> None:
        container = FakeContainer()
        container.paged_query = ErrorPagedQuery()
        repository = CosmosIntakeRepository(None, None, container)

        with self.assertRaises(InvalidContinuationTokenError):
            await repository.list(
                "tenant-a",
                created_by="owner-a",
                request_status=None,
                limit=10,
                continuation_token="modified",
            )

    def test_cosmos_errors_preserve_retry_semantics(self) -> None:
        throttled = exceptions.CosmosHttpResponseError(
            status_code=429, message="throttled"
        )
        throttled.headers = {"x-ms-retry-after-ms": "1500"}
        translated = _translate_unavailable(throttled)
        self.assertIsInstance(translated, RepositoryThrottledError)
        self.assertEqual(2, translated.retry_after_seconds)

        unavailable = _translate_unavailable(
            exceptions.CosmosHttpResponseError(
                status_code=503, message="unavailable"
            )
        )
        self.assertIsInstance(unavailable, RepositoryUnavailableError)

        rejected = _translate_unavailable(
            exceptions.CosmosHttpResponseError(
                status_code=400, message="invalid"
            )
        )
        self.assertIsInstance(rejected, RepositoryOperationError)


if __name__ == "__main__":
    unittest.main()
