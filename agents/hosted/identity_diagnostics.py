"""Opt-in diagnostics for Foundry's request-scoped caller context."""

import hashlib
import logging
from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentMiddleware
from azure.ai.agentserver.core import get_request_context


logger = logging.getLogger(__name__)


def hash_header_value(header_name: str, header_value: str) -> str:
    """Create a one-way correlation key without logging the header value."""
    value = f"{header_name}:{header_value}".encode()
    return hashlib.sha256(value).hexdigest()[:16]


class IdentityDiagnosticsMiddleware(AgentMiddleware):
    """Log request-context presence without tokens or raw user identifiers."""

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        request_context = get_request_context()
        user_key = (
            hash_header_value("x-agent-user-id", request_context.user_id)
            if request_context.user_id
            else "none"
        )
        call_key = (
            hash_header_value(
                "x-agent-foundry-call-id",
                request_context.call_id,
            )
            if request_context.call_id
            else "none"
        )
        logger.info(
            "foundry.identity_headers x-agent-user-id=sha256:%s "
            "x-agent-foundry-call-id=sha256:%s session_id=%s",
            user_key,
            call_key,
            "present" if request_context.session_id else "absent",
        )
        await call_next()
