"""Serialization, polling, result persistence, run data sources, and schedules."""

import argparse
import hashlib
import json
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    DailyRecurrenceSchedule,
    EvaluationScheduleTask,
    RecurrenceTrigger,
    Schedule,
    TestingCriterionAzureAIEvaluator,
)
from openai.types.eval_create_params import DataSourceConfigCustom

from scripts.foundry_eval.config import EvaluationTarget
from scripts.foundry_eval.datasets import build_data_source_config


def serialize(value: Any) -> Any:
    if is_dataclass(value):
        return serialize(asdict(value))
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Iterable) and not isinstance(
        value, (str, bytes, dict, list, tuple)
    ):
        return [serialize(item) for item in value]
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, tuple):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def find_or_create_evaluation(
    openai_client: Any,
    name: str,
    testing_criteria: list[TestingCriterionAzureAIEvaluator],
    data_source_config: DataSourceConfigCustom | None = None,
    *,
    version_definition: bool = False,
) -> Any:
    resolved_config = data_source_config or build_data_source_config()
    if version_definition:
        definition = {
            "data_source_config": serialize(resolved_config),
            "testing_criteria": serialize(testing_criteria),
        }
        digest = hashlib.sha256(
            json.dumps(
                definition,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:12]
        name = f"{name}-d{digest}"
    for evaluation in openai_client.evals.list(limit=100, order="desc"):
        if evaluation.name == name:
            return evaluation
    return openai_client.evals.create(
        name=name,
        data_source_config=resolved_config,
        testing_criteria=testing_criteria,
    )


def build_run_data_source(
    dataset_id: str,
    target: EvaluationTarget,
    query_field: str = "query",
) -> dict[str, Any]:
    return {
        "type": "azure_ai_target_completions",
        "source": {"type": "file_id", "id": dataset_id},
        "input_messages": {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {
                        "type": "input_text",
                        "text": f"{{{{item.{query_field}}}}}",
                    },
                }
            ],
        },
        "target": {
            "type": "azure_ai_agent",
            "name": target.agent_name,
            "version": target.agent_version,
        },
    }


def build_inline_run_data_source(
    items: list[dict[str, Any]],
    target: EvaluationTarget,
    query_field: str = "query",
) -> dict[str, Any]:
    data_source = build_run_data_source("inline", target, query_field)
    data_source["source"] = {
        "type": "file_content",
        "content": [{"item": item} for item in items],
    }
    return data_source


def build_inline_literal_run_data_source(
    item: dict[str, Any],
    target: EvaluationTarget,
) -> dict[str, Any]:
    query = item.get("query")
    if not isinstance(query, str) or not query.strip():
        raise RuntimeError("Inline literal evaluation item has no query")
    data_source = build_inline_run_data_source([item], target)
    data_source["input_messages"]["template"][0]["content"]["text"] = query
    return data_source


def build_jsonl_run_data_source(dataset_id: str) -> dict[str, Any]:
    return {
        "type": "jsonl",
        "source": {"type": "file_id", "id": dataset_id},
    }


def build_inline_jsonl_run_data_source(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "jsonl",
        "source": {
            "type": "file_content",
            "content": [{"item": item} for item in items],
        },
    }


def build_schedule(
    evaluation_id: str,
    run_name: str,
    data_source: dict[str, Any],
    schedule_hour_utc: int,
    target: EvaluationTarget,
    dataset_name: str,
    dataset_version: str,
    display_name: str = "MAF POC daily regression evaluation",
    description: str = "Runs the reviewed regression dataset against the hosted agent.",
) -> Schedule:
    if not 0 <= schedule_hour_utc <= 23:
        raise ValueError("schedule_hour_utc must be between 0 and 23")
    eval_run = {
        "eval_id": evaluation_id,
        "name": run_name,
        "data_source": data_source,
    }
    return Schedule(
        display_name=display_name,
        description=description,
        enabled=True,
        trigger=RecurrenceTrigger(
            interval=1,
            time_zone="UTC",
            schedule=DailyRecurrenceSchedule(hours=[schedule_hour_utc]),
        ),
        task=EvaluationScheduleTask(
            eval_id=evaluation_id,
            eval_run=eval_run,
        ),
        tags={
            "agent": target.agent_name,
            "agentVersion": target.agent_version,
            "dataset": dataset_name,
            "datasetVersion": dataset_version,
            "tier": "regression",
        },
    )


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(serialize(value), indent=2), encoding="utf-8")


def selected_environment(args: argparse.Namespace) -> str:
    return (
        getattr(args, "environment", None)
        or os.getenv("AZURE_ENV_NAME")
        or "default"
    )


def uses_inline_data(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "inline_data", False))


def update_eval_metadata(
    path: Path,
    environment_name: str,
    run_key: str,
    values: dict[str, Any],
) -> None:
    metadata: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is not None and not isinstance(loaded, dict):
            raise RuntimeError(f"Foundry metadata must be an object: {path}")
        metadata = loaded or {}
    metadata.setdefault("azd", {})
    metadata["azd"].update(
        {"environmentName": environment_name, "service": "maf-poc-agent"}
    )
    environment = metadata.setdefault("environments", {}).setdefault(
        environment_name,
        {},
    )
    environment.setdefault("evaluationRuns", {})[run_key] = serialize(values)
    environment["lastEval"] = serialize(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(metadata, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def analyze_output_items(output_items: list[Any]) -> dict[str, Any]:
    summary = {
        "items": len(output_items),
        "failed_results": 0,
        "errored_items": 0,
        "errored_results": 0,
        "null_scores": 0,
        "null_reasons": 0,
        "clusters": {
            "runtime_error": 0,
            "permission_error": 0,
            "unsupported_capability": 0,
            "safety_or_content": 0,
            "incomplete_answer": 0,
            "incorrect_or_ungrounded": 0,
            "off_topic_or_refusal": 0,
            "other": 0,
        },
    }
    for raw_item in output_items:
        item = serialize(raw_item)
        item_has_error = bool(item.get("error"))
        if item.get("error"):
            summary["clusters"]["runtime_error"] += 1
        for result in item.get("results", []):
            sample = result.get("sample")
            sample_error = sample.get("error") if isinstance(sample, dict) else None
            if result.get("status") == "error" or sample_error:
                summary["errored_results"] += 1
                item_has_error = True
                error_message = ""
                if isinstance(sample_error, dict):
                    error_message = str(sample_error.get("message") or "").lower()
                if any(
                    term in error_message
                    for term in ("permissiondenied", "lacks the required data action")
                ):
                    summary["clusters"]["permission_error"] += 1
                elif any(
                    term in error_message
                    for term in ("not supported", "unsupported", "capability")
                ):
                    summary["clusters"]["unsupported_capability"] += 1
                else:
                    summary["clusters"]["runtime_error"] += 1
            if result.get("score") is None:
                summary["null_scores"] += 1
            if result.get("reason") is None:
                summary["null_reasons"] += 1
            if result.get("passed") is not False:
                continue
            summary["failed_results"] += 1
            reason = str(result.get("reason") or "").lower()
            if any(term in reason for term in ("error", "timeout", "exception")):
                cluster = "runtime_error"
            elif any(
                term in reason
                for term in ("violence", "sexual", "hate", "harm", "unsafe")
            ):
                cluster = "safety_or_content"
            elif any(term in reason for term in ("incomplete", "missing", "omits")):
                cluster = "incomplete_answer"
            elif any(
                term in reason
                for term in ("incorrect", "fabricat", "ungrounded", "unsupported")
            ):
                cluster = "incorrect_or_ungrounded"
            elif any(term in reason for term in ("refus", "off-topic", "irrelevant")):
                cluster = "off_topic_or_refusal"
            else:
                cluster = "other"
            summary["clusters"][cluster] += 1
        if item_has_error:
            summary["errored_items"] += 1
    return summary


def poll_run(
    openai_client: Any,
    evaluation_id: str,
    run_id: str,
    poll_seconds: int,
) -> Any:
    while True:
        current = openai_client.evals.runs.retrieve(
            run_id,
            eval_id=evaluation_id,
        )
        print(f"Status: {current.status}")
        if current.status in {"completed", "failed", "cancelled"}:
            return current
        time.sleep(poll_seconds)


def show_schedule(project_client: AIProjectClient, args: argparse.Namespace) -> None:
    schedule = project_client.beta.schedules.get(args.schedule_id)
    runs = sorted(
        project_client.beta.schedules.list_runs(args.schedule_id),
        key=lambda run: run.trigger_time or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    runs = runs[: args.schedule_run_limit]
    print(json.dumps({"schedule": serialize(schedule), "runs": serialize(runs)}, indent=2))
