import unittest
from collections.abc import AsyncIterator

from scripts.backfill_intake_search import backfill_search_projections


class AsyncItems:
    def __init__(self, items: list[dict]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> AsyncIterator[dict]:
        return self

    async def __anext__(self) -> dict:
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeContainer:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.query: str | None = None
        self.patches: list[dict] = []

    def query_items(self, *, query: str) -> AsyncItems:
        self.query = query
        return AsyncItems(self.items)

    async def patch_item(self, **kwargs: object) -> None:
        self.patches.append(kwargs)


class BackfillIntakeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_backfill_updates_only_changed_projections(self) -> None:
        container = FakeContainer(
            [
                {
                    "id": "a",
                    "tenantId": "tenant",
                    "intake": {"title": "First"},
                    "_etag": '"1"',
                },
                {
                    "id": "b",
                    "tenantId": "tenant",
                    "intake": {"title": "Second"},
                    "searchTitle": "Second",
                    "searchText": "title: Second",
                    "_etag": '"2"',
                },
            ]
        )

        result = await backfill_search_projections(
            container,
            dry_run=False,
        )

        self.assertEqual(2, result.examined)
        self.assertEqual(1, result.updated)
        self.assertEqual(1, len(container.patches))
        patch = container.patches[0]
        self.assertEqual(["tenant", "a"], patch["partition_key"])
        self.assertEqual('"1"', patch["etag"])
        self.assertEqual(
            ["First", "title: First"],
            [
                operation["value"]
                for operation in patch["patch_operations"]
            ],
        )

    async def test_dry_run_does_not_write(self) -> None:
        container = FakeContainer(
            [
                {
                    "id": "a",
                    "tenantId": "tenant",
                    "intake": {"title": "First"},
                    "_etag": '"1"',
                }
            ]
        )

        result = await backfill_search_projections(
            container,
            dry_run=True,
        )

        self.assertEqual(1, result.updated)
        self.assertEqual([], container.patches)

    async def test_invalid_record_fails_explicitly(self) -> None:
        container = FakeContainer(
            [
                {
                    "id": "a",
                    "tenantId": "tenant",
                    "intake": None,
                    "_etag": '"1"',
                }
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "has no object intake"):
            await backfill_search_projections(container, dry_run=False)


if __name__ == "__main__":
    unittest.main()
