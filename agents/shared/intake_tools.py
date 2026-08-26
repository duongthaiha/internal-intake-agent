"""Shared intake MCP tool identifiers."""

INTAKE_MCP_SERVER_LABEL = "intake_mcp"
INTAKE_MCP_OPERATIONS = (
    "create_intake_request",
    "get_intake_request",
    "list_intake_requests",
    "replace_intake_request",
    "submit_intake_request",
)


def intake_tool_name(operation: str, style: str) -> str:
    if operation not in INTAKE_MCP_OPERATIONS:
        raise ValueError(f"Unknown intake MCP operation: {operation}")
    if style == "prompt":
        return operation
    if style == "hosted":
        return f"{INTAKE_MCP_SERVER_LABEL}___{operation}"
    raise ValueError("Tool name style must be 'hosted' or 'prompt'.")


def intake_tool_names(style: str) -> tuple[str, ...]:
    return tuple(intake_tool_name(operation, style) for operation in INTAKE_MCP_OPERATIONS)
