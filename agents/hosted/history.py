"""Observable Cosmos DB history provider for the hosted agent."""

from collections.abc import Sequence
from typing import Any

from agent_framework import Message
from agent_framework.observability import get_tracer
from agent_framework_azure_cosmos import CosmosHistoryProvider


HISTORY_TRACER = get_tracer(
    instrumenting_module_name="maf_poc.history",
    instrumenting_library_version="1.0.0",
)


class ObservableCosmosHistoryProvider(CosmosHistoryProvider):
    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> list[Message]:
        with HISTORY_TRACER.start_as_current_span(
            "cosmos.history.load",
            attributes={
                "db.system": "cosmosdb",
                "db.namespace": self.database_name,
                "db.collection.name": self.container_name,
                "gen_ai.history.provider": "cosmos",
            },
        ) as span:
            messages = await super().get_messages(
                session_id,
                state=state,
                **kwargs,
            )
            span.set_attribute("gen_ai.history.message_count", len(messages))
            return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages: Sequence[Message],
        *,
        state: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        with HISTORY_TRACER.start_as_current_span(
            "cosmos.history.save",
            attributes={
                "db.system": "cosmosdb",
                "db.namespace": self.database_name,
                "db.collection.name": self.container_name,
                "gen_ai.history.provider": "cosmos",
                "gen_ai.history.message_count": len(messages),
            },
        ):
            await super().save_messages(
                session_id,
                messages,
                state=state,
                **kwargs,
            )

    async def clear(self, session_id: str | None) -> None:
        with HISTORY_TRACER.start_as_current_span(
            "cosmos.history.clear",
            attributes={
                "db.system": "cosmosdb",
                "db.namespace": self.database_name,
                "db.collection.name": self.container_name,
                "gen_ai.history.provider": "cosmos",
            },
        ):
            await super().clear(session_id)
