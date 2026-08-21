import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
from azure.core.exceptions import ResourceExistsError
from azure.identity import AzureCliCredential
from dotenv import load_dotenv
from openai.types.eval_create_params import DataSourceConfigCustom


DEFAULT_DATASET_PATH = Path("evals/foundry_smoke.jsonl")
DEFAULT_RESULTS_ROOT = Path(".foundry/results")
DEFAULT_POLL_SECONDS = 15


def required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_dataset(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as dataset_file:
        for line_number, line in enumerate(dataset_file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid JSON at {path}:{line_number}"
                ) from exc
            if not isinstance(item.get("query"), str):
                raise RuntimeError(
                    f"Missing string query at {path}:{line_number}"
                )
            if not isinstance(item.get("expected_behavior"), str):
                raise RuntimeError(
                    f"Missing string expected_behavior at {path}:{line_number}"
                )
            items.append(item)

    if not items:
        raise RuntimeError(f"No evaluation items found in {path}")
    return items


def build_testing_criteria(
    model_deployment_name: str,
) -> list[TestingCriterionAzureAIEvaluator]:
    output_text_mapping = {
        "query": "{{item.query}}",
        "response": "{{sample.output_text}}",
    }
    output_items_mapping = {
        "query": "{{item.query}}",
        "response": "{{sample.output_items}}",
    }
    return [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="relevance",
            evaluator_name="builtin.relevance",
            initialization_parameters={"model": model_deployment_name},
            data_mapping=output_text_mapping,
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="task_adherence",
            evaluator_name="builtin.task_adherence",
            initialization_parameters={"model": model_deployment_name},
            data_mapping=output_items_mapping,
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="intent_resolution",
            evaluator_name="builtin.intent_resolution",
            initialization_parameters={"model": model_deployment_name},
            data_mapping=output_items_mapping,
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="indirect_attack",
            evaluator_name="builtin.indirect_attack",
            data_mapping=output_text_mapping,
        ),
    ]


def serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    return value


def run(args: argparse.Namespace) -> None:
    project_endpoint = required_setting("FOUNDRY_PROJECT_ENDPOINT")
    model_deployment_name = required_setting("AZURE_AI_MODEL_DEPLOYMENT_NAME")
    tenant_id = required_setting("AZURE_TENANT_ID")
    agent_name = os.getenv("AGENT_MAF_POC_AGENT_NAME", "maf-poc-agent")
    agent_version = os.getenv("AGENT_MAF_POC_AGENT_VERSION", "2")
    dataset_items = load_dataset(args.dataset)

    credential = AzureCliCredential(tenant_id=tenant_id)
    with (
        credential,
        AIProjectClient(
            endpoint=project_endpoint,
            credential=credential,
            allow_preview=True,
        ) as project_client,
        project_client.get_openai_client() as openai_client,
    ):
        try:
            dataset = project_client.datasets.upload_file(
                name=args.dataset_name,
                version=args.dataset_version,
                file_path=str(args.dataset),
            )
        except ResourceExistsError:
            dataset = project_client.datasets.get(
                args.dataset_name,
                args.dataset_version,
            )

        data_source_config = DataSourceConfigCustom(
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
        evaluation = openai_client.evals.create(
            name=args.evaluation_name,
            data_source_config=data_source_config,
            testing_criteria=build_testing_criteria(model_deployment_name),
        )

        input_messages = {
            "type": "template",
            "template": [
                {
                    "type": "message",
                    "role": "user",
                    "content": {
                        "type": "input_text",
                        "text": "{{item.query}}",
                    },
                }
            ],
        }
        eval_run = openai_client.evals.runs.create(
            eval_id=evaluation.id,
            name=args.run_name,
            data_source={
                "type": "azure_ai_target_completions",
                "source": {"type": "file_id", "id": dataset.id},
                "input_messages": input_messages,
                "target": {
                    "type": "azure_ai_agent",
                    "name": agent_name,
                    "version": agent_version,
                },
            },
        )

        print(f"Evaluation ID: {evaluation.id}")
        print(f"Run ID: {eval_run.id}")
        print(f"Dataset: {dataset.name}:{dataset.version}")

        while True:
            current = openai_client.evals.runs.retrieve(
                eval_run.id,
                eval_id=evaluation.id,
            )
            print(f"Status: {current.status}")
            if current.status in {"completed", "failed", "cancelled"}:
                break
            time.sleep(args.poll_seconds)

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
        / os.getenv("AZURE_ENV_NAME", "default")
        / evaluation.id
        / f"{eval_run.id}.json"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(
            {
                "evaluation": serialize(evaluation),
                "run": serialize(current),
                "dataset": {
                    "id": dataset.id,
                    "name": dataset.name,
                    "version": dataset.version,
                    "source": str(args.dataset),
                    "items": dataset_items,
                },
                "output_items": serialize(output_items),
                "captured_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Results: {result_path}")

    if current.status != "completed":
        raise RuntimeError(f"Evaluation ended with status {current.status}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run a Foundry cloud evaluation against maf-poc-agent."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--dataset-name", default="maf-poc-smoke")
    parser.add_argument("--dataset-version", default="1")
    parser.add_argument("--evaluation-name", default="maf-poc-agent-smoke")
    parser.add_argument("--run-name", default="maf-poc-private-smoke-v2")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
    )
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    run(args)


if __name__ == "__main__":
    main()
