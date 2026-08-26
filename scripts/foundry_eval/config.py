"""Shared constants, settings dataclasses, and environment resolution."""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATASET_PATH = Path("evals/foundry_smoke.jsonl")
DEFAULT_RESULTS_ROOT = Path(".foundry/results")
DEFAULT_SCHEDULE_CACHE_ROOT = Path(".foundry/schedules")
DEFAULT_REPLAY_ROOT = Path(".foundry/datasets")
DEFAULT_METADATA_PATH = Path(".foundry/agent-metadata.yaml")
DEFAULT_POLL_SECONDS = 15
DEFAULT_CAPTURE_ATTEMPTS = 2
DEFAULT_DATASET_NAME = "maf-poc-smoke"
DEFAULT_DATASET_VERSION = "2"
DEFAULT_EVALUATION_NAME = "maf-poc-agent-regression"
DEFAULT_RUN_NAME = "maf-poc-agent-regression"
DEFAULT_SCHEDULE_ID = "maf-poc-daily-regression"
DEFAULT_SCHEDULE_HOUR_UTC = 9
TOOL_DATASET_PATH = Path("evals/shared/intake_tool_calls.jsonl")
TOOL_OPENAPI_PATH = Path("openapi/intake-api.openapi.json")
TOOL_DATASET_NAME = "maf-poc-intake-tools"
TOOL_DATASET_VERSION = "1"
TOOL_EVALUATION_NAME = "maf-poc-agent-tools"
TOOL_RUN_NAME = "maf-poc-agent-tools"
COMPREHENSIVE_DATASET_PATH = Path("evals/foundry_comprehensive_multi_turn.jsonl")
COMPREHENSIVE_DATASET_NAME = "maf-poc-comprehensive"
COMPREHENSIVE_DATASET_VERSION = "2"
COMPREHENSIVE_EVALUATION_NAME = "maf-poc-agent-comprehensive"
COMPREHENSIVE_RUN_NAME = "maf-poc-agent-comprehensive"
COMPREHENSIVE_REPLAY_DATASET_NAME = "maf-poc-agent-comprehensive-replay"
COMPREHENSIVE_REPLAY_EVALUATION_NAME = "maf-poc-agent-live-comprehensive"
COMPREHENSIVE_REPLAY_RUN_NAME = "maf-poc-agent-live-comprehensive"
COMPREHENSIVE_AGENT_EVALUATION_NAME = "maf-poc-agent-daily-comprehensive"
COMPREHENSIVE_AGENT_RUN_NAME = "maf-poc-agent-daily-comprehensive"
COMPREHENSIVE_AGENT_SCHEDULE_ID = "maf-poc-daily-comprehensive"
BEHAVIOR_EVALUATOR_NAME = "maf_poc_expected_behavior_json"
PREAPPROVAL_TOOL_EVALUATOR_NAME = "maf_poc_preapproval_tool_call"
PREAPPROVAL_TOOL_EVALUATOR_VERSION = "6"
TOOL_CAPTURE_EVALUATOR_NAME = "maf_poc_tool_capture_complete"
TOOL_CAPTURE_EVALUATOR_VERSION = "2"
DATASET_SHA_TAG = "source_sha256"
UK_SOUTH_UNAVAILABLE_EVALUATORS = {
    "builtin.groundedness_pro",
    "builtin.hate_unfairness",
    "builtin.indirect_attack",
    "builtin.protected_material",
    "builtin.self_harm",
    "builtin.sexual",
    "builtin.ungrounded_attributes",
    "builtin.violence",
}


@dataclass(frozen=True)
class ProjectSettings:
    project_endpoint: str
    tenant_id: str


@dataclass(frozen=True)
class EvaluationTarget:
    model_deployment_name: str
    agent_name: str
    agent_version: str


@dataclass(frozen=True)
class InlineDataset:
    id: None
    name: str
    version: str


def required_setting(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_project_settings() -> ProjectSettings:
    return ProjectSettings(
        project_endpoint=required_setting("FOUNDRY_PROJECT_ENDPOINT"),
        tenant_id=required_setting("AZURE_TENANT_ID"),
    )


def load_evaluation_target(
    agent_name: str | None = None,
    agent_version: str | None = None,
) -> EvaluationTarget:
    resolved_name = (
        agent_name
        or os.getenv("EVALUATION_AGENT_NAME")
        or os.getenv("AGENT_MAF_POC_AGENT_NAME")
    )
    resolved_version = (
        agent_version
        or os.getenv("EVALUATION_AGENT_VERSION")
        or os.getenv("AGENT_MAF_POC_AGENT_VERSION")
    )
    if not resolved_name:
        raise RuntimeError(
            "Set EVALUATION_AGENT_NAME, AGENT_MAF_POC_AGENT_NAME, or pass "
            "--agent-name."
        )
    if not resolved_version:
        raise RuntimeError(
            "Set EVALUATION_AGENT_VERSION, AGENT_MAF_POC_AGENT_VERSION, or pass "
            "--agent-version."
        )
    return EvaluationTarget(
        model_deployment_name=required_setting("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
        agent_name=resolved_name,
        agent_version=resolved_version,
    )
