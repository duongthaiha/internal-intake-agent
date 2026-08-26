"""Smoke suite: reviewed-query preparation, one-off run, and daily schedule."""

import argparse
from datetime import UTC, datetime
from typing import Any

from azure.ai.projects import AIProjectClient

from scripts.foundry_eval.config import EvaluationTarget, required_setting
from scripts.foundry_eval.datasets import dataset_sha256, load_dataset, prepare_dataset
from scripts.foundry_eval.evaluators import (
    build_testing_criteria,
    ensure_behavior_evaluator,
    filter_criteria_for_region,
)
from scripts.foundry_eval.runtime import (
    analyze_output_items,
    build_inline_run_data_source,
    build_run_data_source,
    build_schedule,
    find_or_create_evaluation,
    poll_run,
    save_json,
    selected_environment,
    serialize,
    uses_inline_data,
)


def prepare_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    dataset_items = load_dataset(args.dataset)
    dataset = prepare_dataset(
        project_client,
        args.dataset,
        args.dataset_name,
        args.dataset_version,
        inline_data=uses_inline_data(args),
        description="Reviewed MAF POC Foundry regression dataset.",
    )
    behavior_evaluator = ensure_behavior_evaluator(project_client)
    evaluation = find_or_create_evaluation(
        openai_client,
        args.evaluation_name,
        filter_criteria_for_region(
            build_testing_criteria(
                target.model_deployment_name,
                behavior_evaluator.version,
            ),
            required_setting("AZURE_LOCATION"),
        ),
    )
    return dataset, evaluation, dataset_items


def run_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> None:
    dataset, evaluation, dataset_items = prepare_evaluation(
        project_client,
        openai_client,
        args,
        target,
    )
    data_source = (
        build_inline_run_data_source(dataset_items, target)
        if uses_inline_data(args)
        else build_run_data_source(dataset.id, target)
    )
    eval_run = openai_client.evals.runs.create(
        eval_id=evaluation.id,
        name=args.run_name,
        data_source=data_source,
    )
    print(f"Evaluation ID: {evaluation.id}")
    print(f"Run ID: {eval_run.id}")
    print(f"Dataset: {dataset.name}:{dataset.version}")
    current = poll_run(
        openai_client,
        evaluation.id,
        eval_run.id,
        args.poll_seconds,
    )
    output_items = list(
        openai_client.evals.runs.output_items.list(
            eval_run.id,
            eval_id=evaluation.id,
            limit=100,
            order="asc",
        )
    )
    result_path = (
        args.results_root
        / selected_environment(args)
        / evaluation.id
        / f"{eval_run.id}.json"
    )
    analysis = analyze_output_items(output_items)
    save_json(
        result_path,
        {
            "evaluation": evaluation,
            "run": current,
            "dataset": {
                "id": dataset.id,
                "name": dataset.name,
                "version": dataset.version,
                "source": str(args.dataset),
                "sha256": dataset_sha256(args.dataset),
                "items": dataset_items,
            },
            "target": serialize(target),
            "output_items": output_items,
            "analysis": analysis,
            "captured_at": datetime.now(UTC),
        },
    )
    print(f"Results: {result_path}")
    if current.status != "completed":
        raise RuntimeError(f"Evaluation ended with status {current.status}")
    if analysis["errored_results"]:
        raise RuntimeError(
            f"Evaluation completed with {analysis['errored_results']} evaluator errors; "
            f"review {result_path}"
        )


def upsert_schedule(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> None:
    dataset, evaluation, dataset_items = prepare_evaluation(
        project_client,
        openai_client,
        args,
        target,
    )
    data_source = (
        build_inline_run_data_source(dataset_items, target)
        if uses_inline_data(args)
        else build_run_data_source(dataset.id, target)
    )
    schedule = build_schedule(
        evaluation.id,
        args.run_name,
        data_source,
        args.schedule_hour_utc,
        target,
        dataset.name,
        dataset.version,
    )
    created = project_client.beta.schedules.create_or_update(
        schedule_id=args.schedule_id,
        schedule=schedule,
    )
    cache_path = args.schedule_cache_root / f"{args.schedule_id}.json"
    save_json(
        cache_path,
        {
            "schedule": created,
            "evaluationId": evaluation.id,
            "dataset": {
                "id": dataset.id,
                "name": dataset.name,
                "version": dataset.version,
                "sha256": dataset_sha256(args.dataset),
            },
            "target": serialize(target),
            "updatedAt": datetime.now(UTC),
        },
    )
    print(f"Schedule: {created.schedule_id}")
    print(f"Provisioning status: {created.provisioning_status}")
    print(f"Cache: {cache_path}")
