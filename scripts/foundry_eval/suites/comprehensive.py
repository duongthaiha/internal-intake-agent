"""Comprehensive suite: reviewed transcripts, live replay, and agent scheduling."""

import argparse
from datetime import UTC, datetime
from typing import Any

from azure.ai.projects import AIProjectClient

from scripts.foundry_eval.config import (
    COMPREHENSIVE_REPLAY_DATASET_NAME,
    COMPREHENSIVE_REPLAY_EVALUATION_NAME,
    COMPREHENSIVE_REPLAY_RUN_NAME,
    DEFAULT_METADATA_PATH,
    EvaluationTarget,
    required_setting,
)
from scripts.foundry_eval.datasets import (
    build_comprehensive_agent_data_source_config,
    build_comprehensive_conversation_data_source_config,
    build_comprehensive_turn_data_source_config,
    dataset_sha256,
    load_comprehensive_dataset,
    prepare_dataset,
    write_jsonl,
)
from scripts.foundry_eval.evaluators import (
    build_comprehensive_agent_criteria,
    build_comprehensive_conversation_criteria,
    build_comprehensive_turn_criteria,
    ensure_behavior_evaluator,
    filter_criteria_for_region,
)
from scripts.foundry_eval.replay import replay_conversations, replay_dataset_version
from scripts.foundry_eval.runtime import (
    analyze_output_items,
    build_inline_jsonl_run_data_source,
    build_inline_run_data_source,
    build_jsonl_run_data_source,
    build_run_data_source,
    build_schedule,
    find_or_create_evaluation,
    poll_run,
    save_json,
    selected_environment,
    serialize,
    update_eval_metadata,
    uses_inline_data,
)


def prepare_comprehensive_evaluations(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    model_deployment_name: str,
) -> tuple[Any, list[tuple[str, Any]], list[dict[str, Any]]]:
    dataset_items = load_comprehensive_dataset(args.dataset)
    dataset = prepare_dataset(
        project_client,
        args.dataset,
        args.dataset_name,
        args.dataset_version,
        inline_data=uses_inline_data(args),
        description=(
            "Reviewed multi-turn internal-intake conversations for comprehensive "
            "Microsoft Foundry evaluation."
        ),
    )
    behavior_evaluator = ensure_behavior_evaluator(project_client)
    evaluations = [
        (
            "turn",
            find_or_create_evaluation(
                openai_client,
                f"{args.evaluation_name}-v{args.dataset_version}-turn",
                filter_criteria_for_region(
                    build_comprehensive_turn_criteria(
                        model_deployment_name,
                        behavior_evaluator.version,
                    ),
                    required_setting("AZURE_LOCATION"),
                ),
                build_comprehensive_turn_data_source_config(),
                version_definition=True,
            ),
        ),
        (
            "conversation",
            find_or_create_evaluation(
                openai_client,
                f"{args.evaluation_name}-v{args.dataset_version}-conversation",
                build_comprehensive_conversation_criteria(model_deployment_name),
                build_comprehensive_conversation_data_source_config(),
                version_definition=True,
            ),
        ),
    ]
    return dataset, evaluations, dataset_items


def run_comprehensive_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    model_deployment_name: str,
) -> None:
    dataset, evaluations, dataset_items = prepare_comprehensive_evaluations(
        project_client,
        openai_client,
        args,
        model_deployment_name,
    )
    for evaluation_level, evaluation in evaluations:
        create_options: dict[str, Any] = {
            "eval_id": evaluation.id,
            "name": f"{args.run_name}-{evaluation_level}",
            "data_source": (
                build_inline_jsonl_run_data_source(dataset_items)
                if uses_inline_data(args)
                else build_jsonl_run_data_source(dataset.id)
            ),
        }
        if evaluation_level == "conversation":
            create_options["extra_body"] = {"evaluation_level": "conversation"}

        eval_run = openai_client.evals.runs.create(**create_options)
        environment_name = selected_environment(args)
        update_eval_metadata(
            getattr(args, "metadata_path", DEFAULT_METADATA_PATH),
            environment_name,
            evaluation_level,
            {
                "evalId": evaluation.id,
                "evalRunId": eval_run.id,
                "runName": create_options["name"],
                "datasetName": dataset.name,
                "datasetVersion": dataset.version,
                "agentVersion": getattr(args, "agent_version", None),
                "startedAt": datetime.now(UTC),
            },
        )
        print(f"{evaluation_level.title()} evaluation ID: {evaluation.id}")
        print(f"{evaluation_level.title()} run ID: {eval_run.id}")
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
            / environment_name
            / evaluation.id
            / f"{eval_run.id}.json"
        )
        analysis = analyze_output_items(output_items)
        save_json(
            result_path,
            {
                "evaluation": evaluation,
                "run": current,
                "evaluation_level": evaluation_level,
                "dataset": {
                    "id": dataset.id,
                    "name": dataset.name,
                    "version": dataset.version,
                    "source": str(args.dataset),
                    "sha256": dataset_sha256(args.dataset),
                    "items": dataset_items,
                },
                "judge_model": model_deployment_name,
                "output_items": output_items,
                "analysis": analysis,
                "captured_at": datetime.now(UTC),
            },
        )
        print(f"Results: {result_path}")
        if current.status != "completed":
            raise RuntimeError(
                f"{evaluation_level.title()} evaluation ended with "
                f"status {current.status}"
            )
        if analysis["errored_results"]:
            raise RuntimeError(
                f"{evaluation_level.title()} evaluation completed with "
                f"{analysis['errored_results']} evaluator errors; review {result_path}"
            )


def prepare_comprehensive_agent_evaluation(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> tuple[Any, Any, list[dict[str, Any]]]:
    dataset_items = load_comprehensive_dataset(args.dataset)
    dataset = prepare_dataset(
        project_client,
        args.dataset,
        args.dataset_name,
        args.dataset_version,
        inline_data=uses_inline_data(args),
        description=(
            "Reviewed internal-intake cases with standalone prompts for live "
            "hosted-agent regression."
        ),
    )
    behavior_evaluator = ensure_behavior_evaluator(project_client)
    evaluation = find_or_create_evaluation(
        openai_client,
        (
            f"{args.evaluation_name}-v{args.dataset_version}-"
            f"agent-v{target.agent_version}"
        ),
        filter_criteria_for_region(
            build_comprehensive_agent_criteria(
                target.model_deployment_name,
                behavior_evaluator.version,
            ),
            required_setting("AZURE_LOCATION"),
        ),
        build_comprehensive_agent_data_source_config(),
        version_definition=True,
    )
    return dataset, evaluation, dataset_items


def run_comprehensive_replay(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> None:
    source_items = load_comprehensive_dataset(args.dataset)
    source_sha = dataset_sha256(args.dataset)
    capture_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    replay_version = replay_dataset_version(
        args.dataset_version,
        source_sha,
        target.agent_version,
        capture_id,
    )
    replay_path = (
        args.replay_root
        / f"{target.agent_name}-comprehensive-replay-{replay_version}.jsonl"
    )
    replayed_items = replay_conversations(
        source_items,
        target,
        args.environment,
    )
    write_jsonl(replay_path, replayed_items)
    replay_args = argparse.Namespace(
        **{
            **vars(args),
            "dataset": replay_path,
            "dataset_name": COMPREHENSIVE_REPLAY_DATASET_NAME,
            "dataset_version": replay_version,
            "evaluation_name": COMPREHENSIVE_REPLAY_EVALUATION_NAME,
            "run_name": COMPREHENSIVE_REPLAY_RUN_NAME,
            "agent_version": target.agent_version,
        }
    )
    run_comprehensive_evaluation(
        project_client,
        openai_client,
        replay_args,
        target.model_deployment_name,
    )


def upsert_comprehensive_agent_schedule(
    project_client: AIProjectClient,
    openai_client: Any,
    args: argparse.Namespace,
    target: EvaluationTarget,
) -> None:
    dataset, evaluation, dataset_items = prepare_comprehensive_agent_evaluation(
        project_client,
        openai_client,
        args,
        target,
    )
    data_source = (
        build_inline_run_data_source(dataset_items, target, "agent_query")
        if uses_inline_data(args)
        else build_run_data_source(dataset.id, target, "agent_query")
    )
    schedule = build_schedule(
        evaluation.id,
        args.run_name,
        data_source,
        args.schedule_hour_utc,
        target,
        dataset.name,
        dataset.version,
        display_name="MAF POC daily comprehensive agent evaluation",
        description=(
            "Runs standalone prompts derived from the comprehensive dataset "
            "against the hosted agent."
        ),
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
