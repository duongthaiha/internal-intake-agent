"""Stable Foundry entry point for the hosted agent implementation."""

from agents.hosted.hosted_agent import (
    get_boolean_setting,
    get_required_setting,
    initialize_rag,
    main,
    verify_cosmos_access,
)


__all__ = [
    "get_boolean_setting",
    "get_required_setting",
    "initialize_rag",
    "main",
    "verify_cosmos_access",
]


if __name__ == "__main__":
    main()
