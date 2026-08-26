"""Argument parsing, suite default resolution, and dispatch."""

import argparse
import os
from pathlib import Path

from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from dotenv import load_dotenv

from scripts.foundry_eval.config import (
    COMPREHENSIVE_AGENT_EVALUATION_NAME,
    COMPREHENSIVE_AGENT_RUN_NAME,
    COMPREHENSIVE_AGENT_SCHEDULE_ID,
    COMPREHENSIVE_DATASET_NAME,
    COMPREHENSIVE_DATASET_PATH,
    COMPREHENSIVE_DATASET_VERSION,
    COMPREHENSIVE_EVALUATION_NAME,
    COMPREHENSIVE_RUN_NAME,
    DEFAULT_DATASET_NAME,
    DEFAULT_DATASET_PATH,
    DEFAULT_DATASET_VERSION,
    DEFAULT_EVALUATION_NAME,
    DEFAULT_METADATA_PATH,
    DEFAULT_POLL_SECONDS,
    DEFAULT_REPLAY_ROOT,
    DEFAULT_RESULTS_ROOT,
    DEFAULT_RUN_NAME,
    DEFAULT_SCHEDULE_CACHE_ROOT,
    DEFAULT_SCHEDULE_HOUR_UTC,
    DEFAULT_SCHEDULE_ID,
    TOOL_DATASET_NAME,
    TOOL_DATASET_PATH,
    TOOL_DATASET_VERSION,
    TOOL_EVALUATION_NAME,
    TOOL_OPENAPI_PATH,
    TOOL_RUN_NAME,
    load_evaluation_target,
    load_project_settings,
    required_setting,
)
from scripts.foundry_eval.runtime import show_schedule
from scripts.foundry_eval.suites.comprehensive import (
    run_comprehensive_evaluation,
    run_comprehensive_replay,
    upsert_comprehensive_agent_schedule,
)
from scripts.foundry_eval.suites.smoke import run_evaluation, upsert_schedule
from scripts.foundry_eval.suites.tools import run_tool_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or schedule a Foundry cloud evaluation for maf-poc-agent."
    )
    parser.add_argument(
        "--suite",
        choices=(
            "smoke",
            "tools",
            "comprehensive",
            "comprehensive-replay",
            "comprehensive-agent",
        ),
        default="smoke",
        help=(
            "Run smoke, pre-approval MCP tools, reviewed transcripts, exact "
            "multi-turn replay, or the native live-agent comprehensive suite."
        ),
    )
    parser.add_argument(
        "--action",
        choices=("run", "schedule", "status"),
        default="run",
        help="Run once, upsert the native daily schedule, or show schedule status.",
    )
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--dataset-name")
    parser.add_argument("--dataset-version")
    parser.add_argument("--evaluation-name")
    parser.add_argument("--run-name")
    parser.add_argument(
        "--tool-openapi",
        type=Path,
        default=TOOL_OPENAPI_PATH,
        help=f"OpenAPI source for MCP tool definitions (default: {TOOL_OPENAPI_PATH}).",
    )
    parser.add_argument(
        "--tool-name-style",
        choices=("hosted", "prompt"),
        default="hosted",
        help=(
            "Use Foundry Toolbox-prefixed names for hosted agents or operation IDs "
            "for prompt-agent MCP tools."
        ),
    )
    parser.add_argument(
        "--agent-name",
        help=(
            "Foundry agent name. Defaults to EVALUATION_AGENT_NAME, then the "
            "legacy AGENT_MAF_POC_AGENT_NAME."
        ),
    )
    parser.add_argument(
        "--agent-version",
        help=(
            "Immutable Foundry agent version. Defaults to "
            "EVALUATION_AGENT_VERSION, then the legacy "
            "AGENT_MAF_POC_AGENT_VERSION."
        ),
    )
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument(
        "--inline-data",
        action="store_true",
        help=(
            "Embed JSONL rows as Foundry file_content instead of uploading a "
            "registered dataset. Use this when the runner cannot reach private Storage."
        ),
    )
    parser.add_argument("--replay-root", type=Path, default=DEFAULT_REPLAY_ROOT)
    parser.add_argument("--metadata-path", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--environment", default=os.getenv("AZURE_ENV_NAME"))
    parser.add_argument("--schedule-id", default=DEFAULT_SCHEDULE_ID)
    parser.add_argument(
        "--schedule-hour-utc",
        type=int,
        choices=range(24),
        default=DEFAULT_SCHEDULE_HOUR_UTC,
    )
    parser.add_argument(
        "--schedule-cache-root",
        type=Path,
        default=DEFAULT_SCHEDULE_CACHE_ROOT,
    )
    parser.add_argument("--schedule-run-limit", type=int, default=10)
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=DEFAULT_POLL_SECONDS,
    )
    args = parser.parse_args()
    if args.poll_seconds < 1:
        parser.error("--poll-seconds must be at least 1")
    if args.schedule_run_limit < 1:
        parser.error("--schedule-run-limit must be at least 1")
    if args.suite == "tools":
        if args.action != "run":
            parser.error("The tools suite supports --action run only")
        args.dataset = args.dataset or TOOL_DATASET_PATH
        args.dataset_name = args.dataset_name or TOOL_DATASET_NAME
        args.dataset_version = args.dataset_version or TOOL_DATASET_VERSION
        args.evaluation_name = args.evaluation_name or TOOL_EVALUATION_NAME
        args.run_name = args.run_name or TOOL_RUN_NAME
    elif args.suite in {
        "comprehensive",
        "comprehensive-replay",
        "comprehensive-agent",
    }:
        if args.suite in {"comprehensive", "comprehensive-replay"} and args.action != "run":
            parser.error(f"The {args.suite} suite supports --action run only")
        if args.suite == "comprehensive-agent" and args.action == "run":
            parser.error(
                "The comprehensive-agent suite supports --action schedule or status"
            )
        args.dataset = args.dataset or COMPREHENSIVE_DATASET_PATH
        args.dataset_name = args.dataset_name or COMPREHENSIVE_DATASET_NAME
        args.dataset_version = (
            args.dataset_version or COMPREHENSIVE_DATASET_VERSION
        )
        if args.suite == "comprehensive-agent":
            args.evaluation_name = (
                args.evaluation_name or COMPREHENSIVE_AGENT_EVALUATION_NAME
            )
            args.run_name = args.run_name or COMPREHENSIVE_AGENT_RUN_NAME
            if args.schedule_id == DEFAULT_SCHEDULE_ID:
                args.schedule_id = COMPREHENSIVE_AGENT_SCHEDULE_ID
        else:
            args.evaluation_name = (
                args.evaluation_name or COMPREHENSIVE_EVALUATION_NAME
            )
            args.run_name = args.run_name or COMPREHENSIVE_RUN_NAME
    else:
        args.dataset = args.dataset or DEFAULT_DATASET_PATH
        args.dataset_name = args.dataset_name or DEFAULT_DATASET_NAME
        args.dataset_version = args.dataset_version or DEFAULT_DATASET_VERSION
        args.evaluation_name = args.evaluation_name or DEFAULT_EVALUATION_NAME
        args.run_name = args.run_name or DEFAULT_RUN_NAME
    return args


def main() -> None:
    load_dotenv()
    args = parse_args()
    if args.suite == "comprehensive-replay" and not args.environment:
        raise RuntimeError(
            "Set AZURE_ENV_NAME or pass --environment for hosted-agent replay"
        )
    settings = load_project_settings()
    credential = AzureCliCredential(tenant_id=settings.tenant_id)
    with (
        credential,
        AIProjectClient(
            endpoint=settings.project_endpoint,
            credential=credential,
            allow_preview=True,
        ) as project_client,
    ):
        if args.action == "status":
            show_schedule(project_client, args)
            return
        with project_client.get_openai_client() as openai_client:
            if args.suite == "comprehensive":
                run_comprehensive_evaluation(
                    project_client,
                    openai_client,
                    args,
                    required_setting("AZURE_AI_MODEL_DEPLOYMENT_NAME"),
                )
            elif args.suite == "comprehensive-replay":
                run_comprehensive_replay(
                    project_client,
                    openai_client,
                    args,
                    load_evaluation_target(args.agent_name, args.agent_version),
                )
            elif args.suite == "comprehensive-agent":
                target = load_evaluation_target(
                    args.agent_name,
                    args.agent_version,
                )
                if args.action == "schedule":
                    upsert_comprehensive_agent_schedule(
                        project_client,
                        openai_client,
                        args,
                        target,
                    )
                else:
                    show_schedule(project_client, args)
            elif args.suite == "tools":
                run_tool_evaluation(
                    project_client,
                    openai_client,
                    args,
                    load_evaluation_target(args.agent_name, args.agent_version),
                )
            else:
                target = load_evaluation_target(
                    args.agent_name,
                    args.agent_version,
                )
                if args.action == "schedule":
                    upsert_schedule(project_client, openai_client, args, target)
                else:
                    run_evaluation(project_client, openai_client, args, target)
