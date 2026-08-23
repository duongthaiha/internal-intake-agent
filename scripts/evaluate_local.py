import argparse
import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from agent_framework import (
    ExpectedToolCall,
    LocalEvaluator,
    evaluate_agent,
    evaluator,
    tool_call_args_match,
    tool_calls_present,
)
from agent_framework_azure_ai_search import AzureAISearchContextProvider
from agent_framework_azure_cosmos import CosmosHistoryProvider
from dotenv import load_dotenv

from agents.hosted.agent import build_agent
from agents.hosted.logging_config import LOG_LEVEL_NAMES, configure_logging


DEFAULT_CASES_PATH = Path("evals/local_cases.jsonl")


def load_cases(
    path: Path,
) -> tuple[list[str], list[str], list[list[ExpectedToolCall]]]:
    queries: list[str] = []
    expected_outputs: list[str] = []
    expected_tool_calls: list[list[ExpectedToolCall]] = []

    with path.open(encoding="utf-8") as case_file:
        for line_number, line in enumerate(case_file, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
                query = case["query"]
                expected_output = case["expected_output"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise RuntimeError(
                    f"Invalid evaluation case at {path}:{line_number}"
                ) from exc

            if not isinstance(query, str) or not isinstance(expected_output, dict):
                raise RuntimeError(
                    f"Invalid evaluation case at {path}:{line_number}"
                )

            raw_tool_calls = case.get("expected_tool_calls", [])
            if not isinstance(raw_tool_calls, list):
                raise RuntimeError(
                    f"Invalid expected_tool_calls at {path}:{line_number}"
                )
            tool_calls: list[ExpectedToolCall] = []
            for raw_tool_call in raw_tool_calls:
                if not isinstance(raw_tool_call, dict):
                    raise RuntimeError(
                        f"Invalid expected tool call at {path}:{line_number}"
                    )
                name = raw_tool_call.get("name")
                arguments = raw_tool_call.get("arguments")
                if not isinstance(name, str) or (
                    arguments is not None and not isinstance(arguments, dict)
                ):
                    raise RuntimeError(
                        f"Invalid expected tool call at {path}:{line_number}"
                    )
                tool_calls.append(ExpectedToolCall(name, arguments))

            queries.append(query)
            expected_outputs.append(json.dumps(expected_output))
            expected_tool_calls.append(tool_calls)

    if not queries:
        raise RuntimeError(f"No evaluation cases found in {path}")
    return queries, expected_outputs, expected_tool_calls


def parse_expected_output(expected_output: str) -> dict[str, Any]:
    value = json.loads(expected_output)
    if not isinstance(value, dict):
        raise ValueError("expected_output must be a JSON object")
    return value


@evaluator(name="non_empty_response")
def non_empty_response(response: str) -> dict[str, object]:
    passed = bool(response.strip())
    return {
        "passed": passed,
        "reason": "response contains text" if passed else "response is empty",
    }


@evaluator(name="concise_response")
def concise_response(response: str) -> dict[str, object]:
    word_count = len(response.split())
    return {
        "passed": word_count <= 120,
        "reason": f"response contains {word_count} words; limit is 120",
    }


@evaluator(name="expected_terms")
def expected_terms(response: str, expected_output: str) -> dict[str, object]:
    expected = parse_expected_output(expected_output)
    term_groups = expected.get("terms")
    if not isinstance(term_groups, list):
        raise ValueError("expected_output.terms must be a list")

    normalized_response = response.casefold()
    missing_groups: list[list[str]] = []
    for group in term_groups:
        aliases = [group] if isinstance(group, str) else group
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) for alias in aliases
        ):
            raise ValueError("Each expected term must be a string or list of strings")
        if not any(alias.casefold() in normalized_response for alias in aliases):
            missing_groups.append(aliases)

    return {
        "passed": not missing_groups,
        "reason": (
            "all expected terms are present"
            if not missing_groups
            else f"missing expected term groups: {missing_groups}"
        ),
    }


@evaluator(name="source_citation")
def source_citation(response: str, expected_output: str) -> dict[str, object]:
    expected = parse_expected_output(expected_output)
    source = expected.get("source")
    if not isinstance(source, str):
        raise ValueError("expected_output.source must be a string")
    passed = source.casefold() in response.casefold()
    return {
        "passed": passed,
        "reason": (
            f"response cites {source}"
            if passed
            else f"response does not cite {source}"
        ),
    }


def print_results(results) -> None:
    for result in results:
        print(f"\n{result.provider}: {result.passed}/{result.total} passed")
        for item in result.items:
            print(f"\n[{item.status.upper()}] {item.input_text}")
            print(item.output_text)
            for score in item.scores:
                reason = (score.sample or {}).get("reason", "")
                status = "PASS" if score.passed else "FAIL"
                print(f"  {status} {score.name}: {reason}")


async def run_evaluation(args: argparse.Namespace) -> None:
    os.environ["HISTORY_PROVIDER"] = args.history_provider
    os.environ["RAG_PROVIDER"] = args.rag_provider
    queries, expected_outputs, expected_tool_calls = load_cases(args.cases)
    components = build_agent()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(components.credential)
        if isinstance(components.history_provider, CosmosHistoryProvider):
            await stack.enter_async_context(components.history_provider)
        if isinstance(components.rag_provider, AzureAISearchContextProvider):
            await stack.enter_async_context(components.rag_provider)
        agent = await stack.enter_async_context(components.agent)

        run_queries: list[str] = []
        run_expected_outputs: list[str] = []
        run_expected_tool_calls: list[list[ExpectedToolCall]] = []
        responses = []
        for _ in range(args.repetitions):
            for query, expected_output, tool_calls in zip(
                queries,
                expected_outputs,
                expected_tool_calls,
            ):
                session = agent.create_session()
                responses.append(await agent.run(query, session=session))
                run_queries.append(query)
                run_expected_outputs.append(expected_output)
                run_expected_tool_calls.append(tool_calls)

        results = await evaluate_agent(
            agent=agent,
            queries=run_queries,
            expected_output=run_expected_outputs,
            expected_tool_calls=run_expected_tool_calls,
            responses=responses,
            evaluators=LocalEvaluator(
                non_empty_response,
                concise_response,
                expected_terms,
                source_citation,
                tool_calls_present,
                tool_call_args_match,
            ),
            eval_name=args.name,
        )

    print_results(results)
    for result in results:
        result.raise_for_status()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run local Microsoft Agent Framework evaluations."
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"JSONL evaluation dataset (default: {DEFAULT_CASES_PATH}).",
    )
    parser.add_argument(
        "--name",
        default="maf-poc-local",
        help="Evaluation run name.",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Number of independent runs per query.",
    )
    parser.add_argument(
        "--history-provider",
        choices=("memory", "cosmos"),
        default="memory",
        help="History provider used by the evaluated agent.",
    )
    parser.add_argument(
        "--rag-provider",
        choices=("memory", "azure_search", "none"),
        default="memory",
        help="RAG provider used by the evaluated agent.",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVEL_NAMES,
        help="Override LOG_LEVEL for this process.",
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1")

    configure_logging(args.log_level)
    asyncio.run(run_evaluation(args))


if __name__ == "__main__":
    main()
