"""Build the persisted text projection consumed by Azure AI Search."""

from collections.abc import Mapping, Sequence
from typing import Any


def build_search_projection(intake: Mapping[str, Any]) -> tuple[str, str]:
    title = intake.get("title")
    search_title = title.strip() if isinstance(title, str) else ""
    lines: list[str] = []
    _append_value(lines, (), intake)
    return search_title, "\n".join(lines)


def _append_value(
    lines: list[str],
    path: tuple[str, ...],
    value: Any,
) -> None:
    if isinstance(value, Mapping):
        for key in sorted(value):
            _append_value(lines, (*path, str(key)), value[key])
        return

    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        for item in value:
            _append_value(lines, path, item)
        return

    if value is None:
        return
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        return
    if not text:
        return

    label = ".".join(path) if path else "intake"
    lines.append(f"{label}: {text}")
