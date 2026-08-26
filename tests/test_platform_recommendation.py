import unittest

from agents.hosted.platform_recommendation import (
    FRAMEWORK_COMMIT,
    WorkloadProfile,
    build_recommendation,
    load_decision_graph,
)


class PlatformRecommendationTests(unittest.TestCase):
    def test_recommends_existing_m365_capability(self) -> None:
        result = build_recommendation(
            WorkloadProfile(
                ai_need="knowledge_assistance",
                interaction_pattern="conversational",
                user_channel="m365",
                build_approach="use_existing",
                platform_affinity="m365",
                data_grounding="m365",
                hosting_preference="not_applicable",
                workflow_type="not_applicable",
                custom_ui_protocol=False,
                risk_tier="individual_productivity",
                human_oversight=True,
            )
        )

        self.assertEqual(result.disposition, "use_existing")
        self.assertEqual(result.primary_platform, "Microsoft 365 Copilot")
        self.assertEqual(result.framework_commit, FRAMEWORK_COMMIT)

    def test_recommends_foundry_for_custom_azure_agent(self) -> None:
        result = build_recommendation(
            WorkloadProfile(
                ai_need="reasoning_or_generation",
                interaction_pattern="conversational",
                user_channel="web_mobile",
                build_approach="pro_code",
                platform_affinity="azure",
                data_grounding="documents",
                hosting_preference="self_hosted",
                workflow_type="custom_orchestration",
                custom_ui_protocol=False,
                risk_tier="internal_expert",
                human_oversight=True,
                existing_capability_gap="Requires custom networking and evaluation.",
            )
        )

        self.assertEqual(result.primary_platform, "Microsoft Foundry")
        self.assertIn("Security and privacy review", result.required_reviews)
        self.assertIn("Foundry IQ", result.grounding_recommendation)

    def test_prefers_non_ai_solution_when_ai_is_not_needed(self) -> None:
        result = build_recommendation(
            WorkloadProfile(
                ai_need="not_needed",
                interaction_pattern="api_headless",
                user_channel="azure_service",
                build_approach="pro_code",
                platform_affinity="azure",
                data_grounding="none",
                hosting_preference="self_hosted",
                workflow_type="not_applicable",
                custom_ui_protocol=False,
                risk_tier="internal_expert",
                human_oversight=True,
            )
        )

        self.assertEqual(result.disposition, "no_ai")
        self.assertEqual(
            result.primary_platform,
            "Deterministic automation or conventional software",
        )

    def test_rejects_business_critical_actions_without_oversight(self) -> None:
        with self.assertRaisesRegex(ValueError, "human oversight"):
            build_recommendation(
                WorkloadProfile(
                    ai_need="action_orchestration",
                    interaction_pattern="autonomous",
                    user_channel="azure_service",
                    build_approach="low_code",
                    platform_affinity="azure",
                    data_grounding="structured",
                    hosting_preference="managed_paas",
                    workflow_type="enterprise_integration",
                    custom_ui_protocol=False,
                    risk_tier="business_critical",
                    human_oversight=False,
                )
            )

    def test_rejects_pro_code_without_existing_capability_gap(self) -> None:
        with self.assertRaisesRegex(ValueError, "pro-code recommendation"):
            build_recommendation(
                WorkloadProfile(
                    ai_need="reasoning_or_generation",
                    interaction_pattern="conversational",
                    user_channel="web_mobile",
                    build_approach="pro_code",
                    platform_affinity="azure",
                    data_grounding="documents",
                    hosting_preference="self_hosted",
                    workflow_type="custom_orchestration",
                    custom_ui_protocol=False,
                    risk_tier="internal_expert",
                    human_oversight=True,
                )
            )

    def test_graph_is_pinned_to_expected_commit(self) -> None:
        self.assertEqual(load_decision_graph()["frameworkCommit"], FRAMEWORK_COMMIT)


if __name__ == "__main__":
    unittest.main()
