import unittest

from scripts.teams_publication import (
    build_activity_endpoint,
    build_endpoint_patch,
    build_publish_payload,
    parse_bot_service_resource_id,
    validate_app_version,
)


BOT_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/"
    "resourceGroups/rg-test/providers/Microsoft.BotService/botServices/test-bot"
)


class TeamsPublicationTests(unittest.TestCase):
    def test_builds_stable_activity_endpoint(self) -> None:
        endpoint = build_activity_endpoint(
            "https://example.services.ai.azure.com/api/projects/project",
            "intake agent",
        )

        self.assertEqual(
            endpoint,
            "https://example.services.ai.azure.com/api/projects/project/"
            "agents/intake%20agent/endpoint/protocols/activityProtocol"
            "?api-version=2025-05-15-preview",
        )

    def test_endpoint_patch_preserves_responses_and_entra(self) -> None:
        endpoint = build_endpoint_patch()["agent_endpoint"]

        self.assertEqual(
            set(endpoint["protocol_configuration"]),
            {"responses", "activity"},
        )
        self.assertEqual(
            [scheme["type"] for scheme in endpoint["authorization_schemes"]],
            ["Entra", "BotServiceTenant"],
        )

    def test_publish_payload_is_tenant_scoped(self) -> None:
        payload = build_publish_payload(
            display_name="Internal Intake",
            bot_service_resource_id=BOT_ID,
            app_version="1.2.3",
            short_description="Manage internal intake requests.",
            full_description="Develop and submit internal intake requests.",
            developer_name="Contoso",
            privacy_url="https://example.test/privacy",
        )

        self.assertEqual(payload["publishScope"], "Tenant")
        self.assertFalse(payload["publishAsAutopilot"])
        self.assertEqual(payload["botServiceArmId"], BOT_ID)
        self.assertNotIn("developerWebsiteUrl", payload)

    def test_rejects_invalid_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "three numeric components"):
            validate_app_version("01.0")

    def test_rejects_non_bot_resource_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "Microsoft.BotService"):
            parse_bot_service_resource_id(
                "/subscriptions/test/resourceGroups/rg/providers/"
                "Microsoft.Web/sites/not-a-bot"
            )

    def test_rejects_non_https_metadata_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute HTTPS URL"):
            build_publish_payload(
                display_name="Internal Intake",
                bot_service_resource_id=BOT_ID,
                app_version="1.0.0",
                short_description="Manage internal intake requests.",
                full_description="Develop and submit internal intake requests.",
                developer_name="Contoso",
                privacy_url="http://example.test/privacy",
            )


if __name__ == "__main__":
    unittest.main()
