"""Dataset loading/validation, hashing, registration, and schema builders."""

import hashlib
import json
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import FileDatasetVersion
from azure.core.exceptions import ResourceExistsError
from openai.types.eval_create_params import DataSourceConfigCustom

from scripts.foundry_eval.config import DATASET_SHA_TAG, InlineDataset


def load_dataset(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as dataset_file:
        for line_number, line in enumerate(dataset_file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON at {path}:{line_number}") from exc
            if not isinstance(item.get("query"), str) or not item["query"].strip():
                raise RuntimeError(f"Missing string query at {path}:{line_number}")
            expected_behavior = item.get("expected_behavior")
            if not isinstance(expected_behavior, str) or not expected_behavior.strip():
                raise RuntimeError(
                    f"Missing string expected_behavior at {path}:{line_number}"
                )
            items.append(item)

    if not items:
        raise RuntimeError(f"No evaluation items found in {path}")
    return items


def message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item["text"]
        for item in content
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )


def load_comprehensive_dataset(path: Path) -> list[dict[str, Any]]:
    items = load_dataset(path)
    required_strings = {
        "agent_query",
        "case_id",
        "category",
        "response",
        "ground_truth",
        "context",
    }
    case_ids: set[str] = set()

    for line_number, item in enumerate(items, start=1):
        for field in required_strings:
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise RuntimeError(f"Missing string {field} at {path}:{line_number}")

        case_id = item["case_id"]
        if case_id in case_ids:
            raise RuntimeError(f"Duplicate case_id {case_id!r} at {path}:{line_number}")
        case_ids.add(case_id)

        messages = item.get("messages")
        if not isinstance(messages, list) or len(messages) < 2:
            raise RuntimeError(f"Missing conversation messages at {path}:{line_number}")
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "system",
                "user",
                "assistant",
                "tool",
            }:
                raise RuntimeError(f"Invalid conversation message at {path}:{line_number}")
            if not message_text(message):
                raise RuntimeError(
                    f"Conversation message has no text at {path}:{line_number}"
                )

        assistant_messages = [
            message for message in messages if message.get("role") == "assistant"
        ]
        if not assistant_messages:
            raise RuntimeError(f"Conversation has no assistant response at {path}:{line_number}")
        if message_text(assistant_messages[-1]) != item["response"]:
            raise RuntimeError(
                f"Final assistant message does not match response at {path}:{line_number}"
            )

        for field in ("retrieval_ground_truth", "retrieved_documents"):
            if not isinstance(item.get(field), list) or not item[field]:
                raise RuntimeError(f"Missing {field} list at {path}:{line_number}")

    return items


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            f"{json.dumps(item, ensure_ascii=True, separators=(',', ':'))}\n"
            for item in items
        ),
        encoding="utf-8",
    )


def dataset_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as dataset_file:
        for chunk in iter(lambda: dataset_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_dataset(
    project_client: AIProjectClient,
    path: Path,
    name: str,
    version: str,
    description: str = "Reviewed MAF POC Foundry regression dataset.",
) -> Any:
    local_sha = dataset_sha256(path)
    try:
        dataset = project_client.datasets.upload_file(
            name=name,
            version=version,
            file_path=str(path),
        )
        dataset = project_client.datasets.create_or_update(
            name=name,
            version=version,
            dataset_version=FileDatasetVersion(
                data_uri=dataset.data_uri,
                is_reference=dataset.is_reference,
                connection_name=dataset.connection_name,
                description=description,
                tags={DATASET_SHA_TAG: local_sha},
            ),
        )
        return dataset
    except ResourceExistsError:
        dataset = project_client.datasets.get(name, version)

    remote_sha = (dataset.tags or {}).get(DATASET_SHA_TAG)
    if not remote_sha:
        raise RuntimeError(
            f"Dataset {name}:{version} exists without a {DATASET_SHA_TAG} tag. "
            "Use a new dataset version rather than adopting unverifiable content."
        )
    if remote_sha != local_sha:
        raise RuntimeError(
            f"Dataset {name}:{version} content differs from {path}. "
            "Registered dataset versions are immutable; use a new version."
        )
    return dataset


def prepare_dataset(
    project_client: AIProjectClient,
    path: Path,
    name: str,
    version: str,
    *,
    inline_data: bool,
    description: str,
) -> Any:
    if inline_data:
        return InlineDataset(id=None, name=name, version=version)
    return register_dataset(
        project_client,
        path,
        name,
        version,
        description=description,
    )


def build_data_source_config() -> DataSourceConfigCustom:
    return DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "expected_behavior": {"type": "string"},
            },
            "required": ["query", "expected_behavior"],
        },
        include_sample_schema=True,
    )


def build_tool_data_source_config() -> DataSourceConfigCustom:
    return DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "expected_behavior": {"type": "string"},
                "tool_expectation": {"type": "object"},
                "tool_definitions": {"type": "array"},
            },
            "required": [
                "query",
                "expected_behavior",
                "tool_expectation",
                "tool_definitions",
            ],
        },
        include_sample_schema=True,
    )


def build_tool_score_data_source_config() -> DataSourceConfigCustom:
    return DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "response": {"type": "array"},
                "tool_expectation": {"type": "object"},
            },
            "required": ["query", "response", "tool_expectation"],
        },
        include_sample_schema=False,
    )


def build_comprehensive_turn_data_source_config() -> DataSourceConfigCustom:
    return DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "response": {"type": "string"},
                "ground_truth": {"type": "string"},
                "context": {"type": "string"},
                "expected_behavior": {"type": "string"},
                "retrieval_ground_truth": {"type": "array"},
                "retrieved_documents": {"type": "array"},
            },
            "required": [
                "query",
                "response",
                "ground_truth",
                "context",
                "expected_behavior",
                "retrieval_ground_truth",
                "retrieved_documents",
            ],
        },
        include_sample_schema=False,
    )


def build_comprehensive_agent_data_source_config() -> DataSourceConfigCustom:
    config = build_comprehensive_turn_data_source_config()
    config["item_schema"]["properties"]["agent_query"] = {"type": "string"}
    config["item_schema"]["required"].append("agent_query")
    config["include_sample_schema"] = True
    return config


def build_comprehensive_conversation_data_source_config() -> DataSourceConfigCustom:
    return DataSourceConfigCustom(
        type="custom",
        item_schema={
            "type": "object",
            "properties": {"messages": {"type": "array"}},
            "required": ["messages"],
        },
        include_sample_schema=False,
    )
