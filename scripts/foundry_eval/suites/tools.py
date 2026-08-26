"""Tools suite: OpenAPI tool projection, capture evaluation, and local scoring."""

import argparse
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient

from agents.shared.intake_tools import INTAKE_MCP_OPERATIONS, intake_tool_name
from scripts.foundry_eval.config import (
    DEFAULT_CAPTURE_ATTEMPTS,
    EvaluationTarget,
    TOOL_OPENAPI_PATH,
    required_setting,
)
from scripts.foundry_eval.datasets import (
    build_tool_data_source_config,
    dataset_sha256,
    load_dataset,
    prepare_dataset,
    write_jsonl,
)
from scripts.foundry_eval.evaluators import (
    PREAPPROVAL_TOOL_EVALUATOR_CODE,
    build_tool_capture_testing_criteria,
    ensure_tool_capture_evaluator,
    filter_criteria_for_region,
)
from scripts.foundry_eval.runtime import (
    build_inline_literal_run_data_source,
    build_run_data_source,
    find_or_create_evaluation,
    poll_run,
    save_json,
    selected_environment,
    serialize,
    update_eval_metadata,
    uses_inline_data,
)


def _resolve_openapi_schema(
    document: dict[str, Any],
    value: Any,
    resolving: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, list):
        return [
            _resolve_openapi_schema(document, item, resolving)
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if isinstance(reference, str):
        if not reference.startswith("#/"):
            raise RuntimeError(f"Unsupported external OpenAPI reference: {reference}")
        if reference in resolving:
            raise RuntimeError(f"Cyclic OpenAPI reference: {reference}")
        target: Any = document
        for segment in reference[2:].split("/"):
            key = segment.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or key not in target:
                raise RuntimeError(f"Invalid OpenAPI reference: {reference}")
            target = target[key]
        merged = copy.deepcopy(target)
        merged.update({key: item for key, item in value.items() if key != "$ref"})
        return _resolve_openapi_schema(
            document,
            merged,
            (*resolving, reference),
        )

    return {
        key: _resolve_openapi_schema(document, item, resolving)
        for key, item in value.items()
    }


def _project_apim_mcp_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_project_apim_mcp_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    any_of = value.get("anyOf")
    if isinstance(any_of, list):
        non_null = [
            item
            for item in any_of
            if not (isinstance(item, dict) and item.get("type") == "null")
        ]
        if len(non_null) == 1:
            projected = _project_apim_mcp_schema(non_null[0])
            if not isinstance(projected, dict):
                return projected
            for key in ("description", "title", "default"):
                if key in value and key not in projected:
                    projected[key] = value[key]
            return projected

    projected = {
        key: _project_apim_mcp_schema(item)
        for key, item in value.items()
    }
    if projected.get("type") == "object":
        projected.setdefault("required", [])
    return projected


def build_intake_tool_definitions(
    path: Path = TOOL_OPENAPI_PATH,
    *,
    tool_name_style: str,
) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"OpenAPI document does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid OpenAPI document: {path}") from exc

    paths = document.get("paths")
    if not isinstance(paths, dict):
        raise RuntimeError(f"OpenAPI document has no paths object: {path}")

    operations: dict[str, tuple[str, dict[str, Any]]] = {}
    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if operation_id in INTAKE_MCP_OPERATIONS:
                operations[operation_id] = (method.upper(), operation)

    missing = sorted(set(INTAKE_MCP_OPERATIONS) - operations.keys())
    if missing:
        raise RuntimeError(
            f"OpenAPI document is missing intake MCP operations: {', '.join(missing)}"
        )

    definitions: list[dict[str, Any]] = []
    for operation_id in INTAKE_MCP_OPERATIONS:
        method, operation = operations[operation_id]
        properties: dict[str, Any] = {}
        required: list[str] = []

        parameters = operation.get("parameters", [])
        if not isinstance(parameters, list):
            raise RuntimeError(f"Invalid parameters for OpenAPI operation {operation_id}")
        for parameter in parameters:
            if not isinstance(parameter, dict):
                raise RuntimeError(
                    f"Invalid parameter for OpenAPI operation {operation_id}"
                )
            name = parameter.get("name")
            schema = parameter.get("schema")
            if not isinstance(name, str) or not isinstance(schema, dict):
                raise RuntimeError(
                    f"Incomplete parameter for OpenAPI operation {operation_id}"
                )
            resolved = _project_apim_mcp_schema(
                _resolve_openapi_schema(document, schema)
            )
            description = parameter.get("description")
            if isinstance(description, str) and "description" not in resolved:
                resolved["description"] = description
            properties[name] = resolved
            if parameter.get("required") is True:
                required.append(name)

        request_body = operation.get("requestBody")
        if isinstance(request_body, dict):
            content = request_body.get("content")
            media_type = (
                content.get("application/json")
                if isinstance(content, dict)
                else None
            )
            schema = media_type.get("schema") if isinstance(media_type, dict) else None
            if not isinstance(schema, dict):
                raise RuntimeError(
                    f"OpenAPI operation {operation_id} has no JSON request schema"
                )
            resolved_body = _project_apim_mcp_schema(
                _resolve_openapi_schema(document, schema)
            )
            reference = schema.get("$ref")
            body_name = (
                reference.rsplit("/", 1)[-1]
                if isinstance(reference, str) and reference.startswith("#/")
                else resolved_body.get("title")
            )
            if not isinstance(body_name, str) or not body_name.strip():
                body_name = "body"
            properties[body_name] = resolved_body
            if request_body.get("required") is True:
                required.append(body_name)
        elif method in {"POST", "PUT", "PATCH"}:
            properties["body"] = {
                "type": "string",
                "description": "Request body",
            }

        parameter_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

        summary = operation.get("summary")
        description = operation.get("description")
        description_parts = [
            item.strip()
            for item in (summary, description)
            if isinstance(item, str) and item.strip()
        ]
        definitions.append(
            {
                "name": intake_tool_name(operation_id, tool_name_style),
                "description": (
                    " ".join(description_parts)
                    or f"{method} intake API operation {operation_id}."
                ),
                "parameters": parameter_schema,
            }
        )
    return definitions


def load_tool_dataset(
    path: Path,
    *,
    openapi_path: Path = TOOL_OPENAPI_PATH,
    tool_name_style: str,
) -> list[dict[str, Any]]:
    definitions = build_intake_tool_definitions(
        openapi_path,
        tool_name_style=tool_name_style,
    )
    items = load_dataset(path)
    for item in items:
        expectation = item.get("tool_expectation")
        if not isinstance(expectation, dict):
            raise RuntimeError(f"Missing tool_expectation object in {path}")
        allowed_calls = expectation.get("allowed_calls")
        if not isinstance(allowed_calls, list):
            raise RuntimeError(f"Missing tool_expectation.allowed_calls in {path}")
        for call in allowed_calls:
            if not isinstance(call, dict):
                raise RuntimeError(f"Invalid allowed tool call in {path}")
            operation_id = call.get("name")
            if operation_id not in INTAKE_MCP_OPERATIONS:
                raise RuntimeError(
                    f"Unknown intake operation in tool expectation: {operation_id!r}"
                )
            call["name"] = intake_tool_name(operation_id, tool_name_style)
        item["tool_definitions"] = copy.deepcopy(definitions)
    return items


def prepare_tool_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> tuple[Any, Any, list[dict[str, Any]], Path]:
    dataset_items = load_tool_dataset(
        args.dataset,
        openapi_path=args.tool_openapi,
        tool_name_style=args.tool_name_style,
    )
    prepared_dataset_path = args.dataset
    if not uses_inline_data(args):
        prepared_dataset_path = (
            args.replay_root
            / f"{args.dataset.stem}-{args.tool_name_style}.jsonl"
        )
        write_jsonl(prepared_dataset_path, dataset_items)
    dataset = prepare_dataset(
        project_client,
        prepared_dataset_path,
        f"{args.dataset_name}-{args.tool_name_style}",
        args.dataset_version,
        inline_data=uses_inline_data(args),
        description=(
            "Reviewed pre-approval MCP tool-selection and argument regression cases."
        ),
    )
    capture_evaluator = ensure_tool_capture_evaluator(project_client)
    capture_evaluation = find_or_create_evaluation(
        openai_client,
        f"{args.evaluation_name}-{args.tool_name_style}-capture",
        filter_criteria_for_region(
            build_tool_capture_testing_criteria(
                target.model_deployment_name,
                capture_evaluator.version,
            ),
            required_setting("AZURE_LOCATION"),
        ),
        build_tool_data_source_config(),
        version_definition=True,
    )
    return dataset, capture_evaluation, dataset_items, prepared_dataset_path


def build_tool_score_items(output_items: list[Any]) -> list[dict[str, Any]]:
    score_items: list[dict[str, Any]] = []
    for position, output_item in enumerate(output_items, start=1):
        value = serialize(output_item)
        if not isinstance(value, dict):
            raise RuntimeError(f"Invalid tool capture output item {position}")
        datasource_item = value.get("datasource_item")
        if isinstance(datasource_item, dict) and isinstance(
            datasource_item.get("item"),
            dict,
        ):
            datasource_item = datasource_item["item"]
        sample = value.get("sample")
        if not isinstance(datasource_item, dict) or not isinstance(sample, dict):
            raise RuntimeError(f"Incomplete tool capture output item {position}")
        query = datasource_item.get("query")
        expectation = datasource_item.get("tool_expectation")
        response = sample.get("output")
        if (
            not isinstance(query, str)
            or not isinstance(expectation, dict)
            or not isinstance(response, list)
        ):
            raise RuntimeError(f"Malformed tool capture output item {position}")
        score_items.append(
            {
                "query": query,
                "response": response,
                "tool_expectation": expectation,
            }
        )
    return score_items


def score_tool_items_locally(
    score_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    namespace: dict[str, Any] = {}
    exec(PREAPPROVAL_TOOL_EVALUATOR_CODE, namespace)
    grade = namespace["grade"]
    return [
        {
            "query": item["query"],
            "score": grade({}, item),
            "tool_expectation": item["tool_expectation"],
            "response": item["response"],
        }
        for item in score_items
    ]


def run_tool_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> None:
    (
        dataset,
        capture_evaluation,
        dataset_items,
        prepared_dataset_path,
    ) = prepare_tool_evaluation(project_client, openai_client, args, target)
    print(f"Capture evaluation ID: {capture_evaluation.id}")
    print(f"Dataset: {dataset.name}:{dataset.version}")
    print(f"Tool name style: {args.tool_name_style}")
    capture_runs: list[Any] = []
    capture_currents: list[Any] = []
    capture_output_items: list[Any] = []
    capture_sources = (
        [
            build_inline_literal_run_data_source(item, target)
            for item in dataset_items
        ]
        if uses_inline_data(args)
        else [build_run_data_source(dataset.id, target)]
    )
    for position, capture_source in enumerate(capture_sources, start=1):
        for attempt in range(1, DEFAULT_CAPTURE_ATTEMPTS + 1):
            capture_run = openai_client.evals.runs.create(
                eval_id=capture_evaluation.id,
                name=(
                    f"{args.run_name}-capture-{position:02d}"
                    f"-attempt-{attempt}"
                ),
                data_source=capture_source,
            )
            capture_runs.append(capture_run)
            print(
                f"Capture run ID ({position}/{len(capture_sources)}, "
                f"attempt {attempt}): {capture_run.id}"
            )
            capture_current = poll_run(
                openai_client,
                capture_evaluation.id,
                capture_run.id,
                args.poll_seconds,
            )
            capture_currents.append(capture_current)
            if capture_current.status == "completed":
                captured = list(
                    openai_client.evals.runs.output_items.list(
                        capture_run.id,
                        eval_id=capture_evaluation.id,
                        limit=100,
                        order="asc",
                    )
                )
                capture_output_items.extend(captured)
                break
            if attempt == DEFAULT_CAPTURE_ATTEMPTS:
                raise RuntimeError(
                    "Tool capture evaluation ended with status "
                    f"{capture_current.status} after {attempt} attempts"
                )
            print(
                f"Retrying capture case {position} after status "
                f"{capture_current.status}."
            )

    score_items = build_tool_score_items(capture_output_items)
    score_output_items = score_tool_items_locally(score_items)
    passed = sum(item["score"] == 1.0 for item in score_output_items)
    analysis = {
        "items": len(score_output_items),
        "passed": passed,
        "failed": len(score_output_items) - passed,
        "pass_rate": passed / len(score_output_items),
    }
    result_path = (
        args.results_root
        / selected_environment(args)
        / capture_evaluation.id
        / f"{capture_runs[-1].id}-scored.json"
    )
    save_json(
        result_path,
        {
            "evaluation": capture_evaluation,
            "capture": {
                "evaluation": capture_evaluation,
                "runs": capture_currents,
                "output_items": capture_output_items,
            },
            "dataset": {
                "id": dataset.id,
                "name": dataset.name,
                "version": dataset.version,
                "source": str(prepared_dataset_path),
                "sha256": dataset_sha256(prepared_dataset_path),
                "items": dataset_items,
            },
            "target": serialize(target),
            "tool_name_style": args.tool_name_style,
            "output_items": score_output_items,
            "analysis": analysis,
            "captured_at": datetime.now(UTC),
        },
    )
    update_eval_metadata(
        args.metadata_path,
        selected_environment(args),
        f"tools-{args.tool_name_style}",
        {
            "evaluationId": capture_evaluation.id,
            "captureRunIds": [run.id for run in capture_runs],
            "resultFile": str(result_path),
            "agentName": target.agent_name,
            "agentVersion": target.agent_version,
            "toolNameStyle": args.tool_name_style,
        },
    )
    print(f"Results: {result_path}")
    print(f"Deterministic tool score: {passed}/{len(score_output_items)}")
