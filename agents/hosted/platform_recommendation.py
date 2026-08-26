"""Deterministic Microsoft AI platform recommendation for the hosted agent."""

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from agent_framework import tool
from pydantic import BaseModel, Field


FRAMEWORK_COMMIT = "5939bfac7f2f80c5ae773042fb089c5ab01fd893"
DECISION_GRAPH_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "ai-decision-framework"
    / "decision-graph.json"
)


class WorkloadProfile(BaseModel):
    ai_need: Literal[
        "not_needed",
        "knowledge_assistance",
        "reasoning_or_generation",
        "action_orchestration",
    ] = Field(description="Why AI is or is not materially needed.")
    interaction_pattern: Literal[
        "conversational",
        "autonomous",
        "api_headless",
    ] = Field(description="How the workload is invoked or experienced.")
    user_channel: Literal[
        "m365",
        "teams_custom",
        "multi_channel",
        "web_mobile",
        "azure_service",
        "local_edge",
    ] = Field(description="Primary user or deployment channel.")
    build_approach: Literal["use_existing", "low_code", "pro_code"] = Field(
        description="Lowest justified build-before-buy rung."
    )
    platform_affinity: Literal["m365", "azure", "fabric", "none"] = Field(
        description="Existing governed platform estate."
    )
    data_grounding: Literal[
        "none",
        "m365",
        "documents",
        "structured",
        "analytics",
    ] = Field(description="Primary grounding pattern.")
    hosting_preference: Literal[
        "managed_paas",
        "self_hosted",
        "local_edge",
        "not_applicable",
    ] = Field(description="Required hosting control.")
    workflow_type: Literal[
        "enterprise_integration",
        "custom_orchestration",
        "not_applicable",
    ] = Field(description="Dominant autonomous workflow shape.")
    custom_ui_protocol: bool = Field(
        description="Whether a custom agent UI protocol is required."
    )
    risk_tier: Literal[
        "individual_productivity",
        "internal_expert",
        "business_critical",
    ] = Field(description="Consequence and governance tier.")
    human_oversight: bool = Field(
        description="Whether a named person remains accountable for consequential actions."
    )
    existing_capability_gap: str | None = Field(
        default=None,
        description="Why an existing or configurable capability is insufficient.",
        max_length=1000,
    )


class RecommendationResult(BaseModel):
    disposition: str
    primary_platform: str
    alternatives: list[str]
    rationale: list[str]
    grounding_recommendation: str
    deployment_target: str
    classifications: dict[str, str]
    assumptions: list[str]
    limitations: list[str]
    required_reviews: list[str]
    framework_commit: str
    framework_source: str


@lru_cache(maxsize=1)
def load_decision_graph() -> dict[str, object]:
    try:
        graph = json.loads(DECISION_GRAPH_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"AI decision graph does not exist: {DECISION_GRAPH_PATH}"
        ) from exc
    if graph.get("frameworkCommit") != FRAMEWORK_COMMIT:
        raise RuntimeError(
            "AI decision graph framework commit does not match the hosted "
            "recommendation implementation."
        )
    return graph


@tool(
    name="recommend_ai_platform",
    description=(
        "Return a deterministic advisory Microsoft AI platform recommendation "
        "after the platform-advisor skill has collected all required workload inputs."
    ),
    schema=WorkloadProfile,
    approval_mode="never_require",
)
def recommend_ai_platform(
    ai_need: str,
    interaction_pattern: str,
    user_channel: str,
    build_approach: str,
    platform_affinity: str,
    data_grounding: str,
    hosting_preference: str,
    workflow_type: str,
    custom_ui_protocol: bool,
    risk_tier: str,
    human_oversight: bool,
    existing_capability_gap: str | None = None,
) -> dict[str, object]:
    profile = WorkloadProfile(
        ai_need=ai_need,
        interaction_pattern=interaction_pattern,
        user_channel=user_channel,
        build_approach=build_approach,
        platform_affinity=platform_affinity,
        data_grounding=data_grounding,
        hosting_preference=hosting_preference,
        workflow_type=workflow_type,
        custom_ui_protocol=custom_ui_protocol,
        risk_tier=risk_tier,
        human_oversight=human_oversight,
        existing_capability_gap=existing_capability_gap,
    )
    return build_recommendation(profile).model_dump()


def build_recommendation(profile: WorkloadProfile) -> RecommendationResult:
    graph = load_decision_graph()
    _validate_profile(profile)
    rule = _select_rule(profile, graph)
    platforms = graph["platforms"]
    platform = platforms[rule["platform"]]
    grounding = graph["grounding"][profile.data_grounding]
    deployment = graph["deploymentTargets"][profile.user_channel]

    alternatives = _alternatives(profile, platform["label"])
    assumptions: list[str] = []
    if not profile.existing_capability_gap and profile.build_approach != "use_existing":
        assumptions.append(
            "The requester has not yet documented why existing or configurable "
            "capabilities are insufficient."
        )

    required_reviews = ["Human architecture validation"]
    if profile.risk_tier != "individual_productivity":
        required_reviews.extend(
            ["Security and privacy review", "Responsible AI review"]
        )
    if profile.risk_tier == "business_critical":
        required_reviews.append("Operational resilience and audit review")

    rationale = [
        f"The workload interaction pattern is {profile.interaction_pattern}.",
        f"The lowest justified build approach is {profile.build_approach}.",
        f"The existing governed platform affinity is {profile.platform_affinity}.",
        f"The primary grounding pattern is {profile.data_grounding}.",
    ]
    if profile.ai_need == "not_needed":
        rationale.insert(
            0,
            "The stated workload does not require language understanding, "
            "generation, uncertain reasoning, or adaptive orchestration.",
        )

    return RecommendationResult(
        disposition=rule["disposition"],
        primary_platform=platform["label"],
        alternatives=alternatives,
        rationale=rationale,
        grounding_recommendation=grounding,
        deployment_target=deployment,
        classifications={
            "aiNeed": profile.ai_need,
            "interactionPattern": profile.interaction_pattern,
            "riskTier": profile.risk_tier,
            "buildApproach": profile.build_approach,
        },
        assumptions=assumptions,
        limitations=[
            *platform["limitations"],
            "Verify current licensing, region, quota, availability, and GA or "
            "preview status before implementation or procurement.",
        ],
        required_reviews=required_reviews,
        framework_commit=FRAMEWORK_COMMIT,
        framework_source=graph["sourceUrl"],
    )


def _validate_profile(profile: WorkloadProfile) -> None:
    if (
        profile.risk_tier == "business_critical"
        and profile.ai_need == "action_orchestration"
        and not profile.human_oversight
    ):
        raise ValueError(
            "Business-critical action orchestration requires explicit human oversight."
        )
    if (
        profile.build_approach == "pro_code"
        and profile.ai_need != "not_needed"
        and not profile.existing_capability_gap
    ):
        raise ValueError(
            "A pro-code recommendation requires the gap in existing or "
            "configurable capabilities."
        )


def _select_rule(
    profile: WorkloadProfile,
    graph: dict[str, object],
) -> dict[str, str]:
    values = profile.model_dump()
    for raw_rule in graph["rules"]:
        conditions = raw_rule["when"]
        if all(values.get(key) == value for key, value in conditions.items()):
            return raw_rule
    raise ValueError(
        "The workload profile does not match a reviewed decision path. "
        "Collect more specific platform-affinity, channel, build, and hosting inputs."
    )


def _alternatives(profile: WorkloadProfile, primary: str) -> list[str]:
    candidates: list[str] = []
    if profile.platform_affinity == "m365":
        candidates.extend(["Microsoft 365 Copilot", "Microsoft Copilot Studio"])
    if profile.platform_affinity == "fabric" or profile.data_grounding == "analytics":
        candidates.append("Microsoft Fabric")
    if profile.build_approach == "pro_code":
        candidates.extend(["Microsoft Foundry", "Microsoft 365 Agents SDK"])
    elif profile.build_approach == "low_code":
        candidates.extend(["Microsoft Copilot Studio", "Azure Logic Apps"])
    candidates.append("Deterministic automation or conventional software")
    return list(dict.fromkeys(item for item in candidates if item != primary))[:3]
