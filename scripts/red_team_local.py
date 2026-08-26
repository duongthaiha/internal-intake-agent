"""Run local AI red-team smoke scans against deployed Foundry agents."""

import argparse
import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv

from scripts.foundry_eval.config import EvaluationTarget
from scripts.foundry_eval.replay import invoke_agent_turn


DEFAULT_RESULTS_ROOT = Path(".foundry/results")
DEFAULT_RISKS = ("violence", "hate-unfairness", "sexual", "self-harm")
DEFAULT_ATTACK_STRATEGIES = ("base64",)
RISK_ATTRIBUTES = {
    "violence": "Violence",
    "hate-unfairness": "HateUnfairness",
    "sexual": "Sexual",
    "self-harm": "SelfHarm",
    "protected-material": "ProtectedMaterial",
    "code-vulnerability": "CodeVulnerability",
    "ungrounded-attributes": "UngroundedAttributes",
}
ATTACK_ATTRIBUTES = {
    "base64": "Base64",
    "flip": "Flip",
    "morse": "Morse",
    "tense": "Tense",
    "jailbreak": "Jailbreak",
}


@dataclass(frozen=True)
class AgentTarget:
    kind: str
    name: str
    version: str

    @property
    def evaluation_target(self) -> EvaluationTarget:
        return EvaluationTarget(
            model_deployment_name="",
            agent_name=self.name,
            agent_version=self.version,
        )


@dataclass(frozen=True)
class ScanConfig:
    project_endpoint: str
    environment: str
    results_root: Path
    scan_name: str
    num_objectives: int
    risks: tuple[str, ...]
    attack_strategies: tuple[str, ...]


def required_value(value: str | None, message: str) -> str:
    if value and value.strip():
        return value.strip()
    raise RuntimeError(message)


def validate_project_endpoint(endpoint: str) -> str:
    endpoint = endpoint.rstrip("/")
    if not endpoint.startswith("https://") or "/api/projects/" not in endpoint:
        raise RuntimeError(
            "RED_TEAM_PROJECT_ENDPOINT must be a Foundry project endpoint such as "
            "https://<account>.services.ai.azure.com/api/projects/<project>."
        )
    return endpoint


def default_azd_environment(config_path: Path = Path(".azure/config.json")) -> str | None:
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid azd configuration: {config_path}") from exc
    if not isinstance(config, dict):
        raise RuntimeError(f"Invalid azd configuration: {config_path}")
    value = config.get("defaultEnvironment")
    return value if isinstance(value, str) and value.strip() else None


def resolve_targets(args: argparse.Namespace) -> list[AgentTarget]:
    targets: list[AgentTarget] = []
    if args.target in {"hosted", "both"}:
        targets.append(
            AgentTarget(
                kind="hosted",
                name=required_value(
                    args.hosted_agent_name
                    or os.getenv("AGENT_MAF_POC_AGENT_NAME"),
                    "Set AGENT_MAF_POC_AGENT_NAME or pass --hosted-agent-name.",
                ),
                version=required_value(
                    args.hosted_agent_version
                    or os.getenv("AGENT_MAF_POC_AGENT_VERSION"),
                    "Set AGENT_MAF_POC_AGENT_VERSION or pass "
                    "--hosted-agent-version.",
                ),
            )
        )
    if args.target in {"prompt", "both"}:
        targets.append(
            AgentTarget(
                kind="prompt",
                name=required_value(
                    args.prompt_agent_name
                    or os.getenv("PROMPT_AGENT_NAME")
                    or "prompt-intake-agent",
                    "Set PROMPT_AGENT_NAME or pass --prompt-agent-name.",
                ),
                version=required_value(
                    args.prompt_agent_version
                    or os.getenv("PROMPT_AGENT_VERSION"),
                    "Set PROMPT_AGENT_VERSION or pass --prompt-agent-version.",
                ),
            )
        )
    return targets


def resolve_config(args: argparse.Namespace) -> ScanConfig:
    project_endpoint = validate_project_endpoint(
        required_value(
            args.project_endpoint
            or os.getenv("RED_TEAM_PROJECT_ENDPOINT")
            or os.getenv("AZURE_AI_PROJECT"),
            "Set RED_TEAM_PROJECT_ENDPOINT or pass --project-endpoint. The project "
            "must be in a region that supports AI red teaming.",
        )
    )
    environment = required_value(
        args.environment
        or os.getenv("AZURE_ENV_NAME")
        or default_azd_environment(),
        "Set AZURE_ENV_NAME or pass --environment for agent invocation.",
    )
    return ScanConfig(
        project_endpoint=project_endpoint,
        environment=environment,
        results_root=args.results_root,
        scan_name=args.scan_name,
        num_objectives=args.num_objectives,
        risks=tuple(args.risk or DEFAULT_RISKS),
        attack_strategies=tuple(
            args.attack_strategy or DEFAULT_ATTACK_STRATEGIES
        ),
    )


def message_text(message: Any) -> str:
    content = (
        message.get("content")
        if isinstance(message, dict)
        else getattr(message, "content", None)
    )
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            text = (
                item.get("text")
                if isinstance(item, dict)
                else getattr(item, "text", None)
            )
            if isinstance(text, str):
                parts.append(text)
        text = "\n".join(parts).strip()
        if text:
            return text
    raise RuntimeError("Red-team callback received a message without text content.")


def build_target_callback(
    target: AgentTarget,
    environment: str,
    invoke: Callable[..., str] = invoke_agent_turn,
) -> Callable[..., Any]:
    async def callback(
        messages: list[Any],
        stream: bool = False,
        session_state: Any = None,
        context: Any = None,
    ) -> dict[str, list[dict[str, str]]]:
        del stream, session_state, context
        if not messages:
            raise RuntimeError("Red-team callback received no messages.")
        response = await asyncio.to_thread(
            invoke,
            message_text(messages[-1]),
            target.evaluation_target,
            environment,
            new_conversation=True,
        )
        return {"messages": [{"role": "assistant", "content": response}]}

    return callback


def enum_values(
    enum_type: Any,
    names: tuple[str, ...],
    attributes: dict[str, str],
) -> list[Any]:
    values: list[Any] = []
    for name in names:
        attribute = attributes[name]
        try:
            values.append(getattr(enum_type, attribute))
        except AttributeError as exc:
            raise RuntimeError(
                f"The installed Azure AI Evaluation SDK does not support {name!r}."
            ) from exc
    return values


def safe_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return normalized or "scan"


def result_path(
    config: ScanConfig,
    target: AgentTarget,
    now: datetime | None = None,
) -> Path:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    directory = (
        f"{safe_name(config.scan_name)}-{target.kind}-"
        f"{safe_name(target.name)}-v{safe_name(target.version)}-{timestamp}"
    )
    return (
        config.results_root
        / safe_name(config.environment)
        / "red-team-local"
        / directory
    )


def summarize_scorecard(payload: dict[str, Any]) -> dict[str, Any]:
    scorecard = payload.get("scorecard")
    if not isinstance(scorecard, dict):
        raise RuntimeError("Red-team result does not contain scorecard.")

    risk_rows = scorecard.get("risk_category_summary")
    attack_rows = scorecard.get("attack_technique_summary")
    if (
        not isinstance(risk_rows, list)
        or not risk_rows
        or not isinstance(risk_rows[0], dict)
    ):
        raise RuntimeError("Red-team scorecard has no risk category summary.")
    if (
        not isinstance(attack_rows, list)
        or not attack_rows
        or not isinstance(attack_rows[0], dict)
    ):
        raise RuntimeError("Red-team scorecard has no attack technique summary.")
    total_attacks = risk_rows[0].get("overall_total")
    if not isinstance(total_attacks, int) or total_attacks < 1:
        raise RuntimeError(
            "Red-team scan evaluated zero attacks. Check scanner-project region, "
            "network access, permissions, and attack-objective generation errors."
        )
    return {
        "risk_category": risk_rows[0],
        "attack_technique": attack_rows[0],
    }


def print_summary(target: AgentTarget, output_path: Path, summary: dict[str, Any]) -> None:
    print(f"\n{target.kind.title()} agent: {target.name}:{target.version}")
    print(f"Result: {output_path}")
    for section_name in ("risk_category", "attack_technique"):
        print(section_name.replace("_", " ").title())
        for name, value in summary[section_name].items():
            print(f"  {name}: {value}")


async def run_target_scan(
    config: ScanConfig,
    target: AgentTarget,
    red_team_type: Any,
    risk_type: Any,
    attack_type: Any,
    credential_factory: Callable[[], Any] = DefaultAzureCredential,
    invoke: Callable[..., str] = invoke_agent_turn,
) -> Path:
    output_path = result_path(config, target)
    output_path.mkdir(parents=True, exist_ok=True)
    risks = enum_values(risk_type, config.risks, RISK_ATTRIBUTES)
    attacks = enum_values(
        attack_type,
        config.attack_strategies,
        ATTACK_ATTRIBUTES,
    )
    callback = build_target_callback(target, config.environment, invoke)

    with credential_factory() as credential:
        red_team = red_team_type(
            azure_ai_project=config.project_endpoint,
            credential=credential,
            risk_categories=risks,
            num_objectives=config.num_objectives,
            output_dir=str(output_path),
        )
        await red_team.scan(
            target=callback,
            scan_name=f"{config.scan_name}-{target.kind}",
            attack_strategies=attacks,
            skip_upload=True,
            output_path=str(output_path),
        )

    scorecard_path = output_path / "evaluation_results.json"
    if not scorecard_path.exists():
        raise RuntimeError(
            f"Red-team scan did not create its scorecard: {scorecard_path}"
        )
    try:
        payload = json.loads(scorecard_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Red-team scorecard contains invalid JSON: {scorecard_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"Red-team scorecard must be a JSON object: {scorecard_path}"
        )
    print_summary(target, scorecard_path, summarize_scorecard(payload))
    return scorecard_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run local AI red-team smoke scans against deployed Foundry agents."
    )
    parser.add_argument(
        "--target",
        choices=("hosted", "prompt", "both"),
        default="both",
    )
    parser.add_argument("--project-endpoint")
    parser.add_argument("--environment")
    parser.add_argument("--hosted-agent-name")
    parser.add_argument("--hosted-agent-version")
    parser.add_argument("--prompt-agent-name")
    parser.add_argument("--prompt-agent-version")
    parser.add_argument("--scan-name", default="maf-poc-red-team-smoke")
    parser.add_argument("--num-objectives", type=int, default=1)
    parser.add_argument(
        "--risk",
        action="append",
        choices=tuple(RISK_ATTRIBUTES),
        help="Risk category to include. Repeat to select multiple categories.",
    )
    parser.add_argument(
        "--attack-strategy",
        action="append",
        choices=tuple(ATTACK_ATTRIBUTES),
        help="Attack strategy to include in addition to baseline attacks.",
    )
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    args = parser.parse_args()
    if args.num_objectives < 1:
        parser.error("--num-objectives must be at least 1")
    return args


async def run(args: argparse.Namespace) -> list[Path]:
    try:
        from azure.ai.evaluation.red_team import AttackStrategy, RedTeam, RiskCategory
    except ImportError as exc:
        raise RuntimeError(
            "Install requirements.txt to use local red teaming."
        ) from exc

    config = resolve_config(args)
    outputs: list[Path] = []
    for target in resolve_targets(args):
        outputs.append(
            await run_target_scan(
                config,
                target,
                RedTeam,
                RiskCategory,
                AttackStrategy,
            )
        )
    return outputs


def main() -> None:
    load_dotenv()
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
