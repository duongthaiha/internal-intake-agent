"""Build and validate Microsoft 365 publication contracts for the hosted agent."""

from __future__ import annotations

import argparse
import json
import re
from urllib.parse import quote, urlsplit


_SEMVER_PATTERN = re.compile(r"^[1-9]\d*\.\d+\.\d+$")
_BOT_RESOURCE_ID_PATTERN = re.compile(
    r"^/subscriptions/([^/]+)/resourceGroups/([^/]+)/providers/"
    r"Microsoft\.BotService/botServices/([^/]+)$",
    re.IGNORECASE,
)


def validate_app_version(value: str) -> str:
    if not _SEMVER_PATTERN.fullmatch(value):
        raise ValueError(
            "Teams app version must contain three numeric components and "
            "must not start with zero, for example '1.0.0'."
        )
    return value


def validate_https_url(name: str, value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{name} must be an absolute HTTPS URL.")
    return value


def parse_bot_service_resource_id(value: str) -> tuple[str, str, str]:
    match = _BOT_RESOURCE_ID_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(
            "Bot Service resource ID must identify "
            "Microsoft.BotService/botServices."
        )
    return match.group(1), match.group(2), match.group(3)


def build_activity_endpoint(project_endpoint: str, agent_name: str) -> str:
    parsed = urlsplit(project_endpoint)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Foundry project endpoint must be an absolute HTTPS URL.")
    if not agent_name.strip():
        raise ValueError("Agent name must not be empty.")
    return (
        f"{project_endpoint.rstrip('/')}/agents/{quote(agent_name, safe='')}"
        "/endpoint/protocols/activityProtocol?api-version=2025-05-15-preview"
    )


def build_endpoint_patch() -> dict[str, object]:
    return {
        "agent_endpoint": {
            "protocol_configuration": {
                "responses": {},
                "activity": {},
            },
            "authorization_schemes": [
                {"type": "Entra"},
                {"type": "BotServiceTenant"},
            ],
        }
    }


def build_publish_payload(
    *,
    display_name: str,
    bot_service_resource_id: str,
    app_version: str,
    short_description: str,
    full_description: str,
    developer_name: str,
    developer_website_url: str | None = None,
    privacy_url: str | None = None,
    terms_of_use_url: str | None = None,
) -> dict[str, object]:
    required = {
        "display name": display_name,
        "short description": short_description,
        "full description": full_description,
        "developer name": developer_name,
    }
    for name, value in required.items():
        if not value.strip():
            raise ValueError(f"Teams {name} must not be empty.")
    if len(developer_name) > 32:
        raise ValueError("Teams developer name must be 32 characters or fewer.")
    if len(short_description) > 80:
        raise ValueError("Teams short description must be 80 characters or fewer.")
    if len(full_description) > 4000:
        raise ValueError("Teams full description must be 4,000 characters or fewer.")

    parse_bot_service_resource_id(bot_service_resource_id)
    validate_app_version(app_version)

    payload: dict[str, object] = {
        "agentDisplayName": display_name,
        "botServiceArmId": bot_service_resource_id,
        "publishScope": "Tenant",
        "publishAsAutopilot": False,
        "appVersion": app_version,
        "shortDescription": short_description,
        "fullDescription": full_description,
        "developerName": developer_name,
    }
    optional_urls = {
        "developerWebsiteUrl": validate_https_url(
            "Teams developer website URL", developer_website_url
        ),
        "privacyUrl": validate_https_url("Teams privacy URL", privacy_url),
        "termsOfUseUrl": validate_https_url(
            "Teams terms-of-use URL", terms_of_use_url
        ),
    }
    payload.update({key: value for key, value in optional_urls.items() if value})
    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    activity = subparsers.add_parser("activity-endpoint")
    activity.add_argument("--project-endpoint", required=True)
    activity.add_argument("--agent-name", required=True)

    subparsers.add_parser("endpoint-patch")

    payload = subparsers.add_parser("publish-payload")
    payload.add_argument("--display-name", required=True)
    payload.add_argument("--bot-service-resource-id", required=True)
    payload.add_argument("--app-version", required=True)
    payload.add_argument("--short-description", required=True)
    payload.add_argument("--full-description", required=True)
    payload.add_argument("--developer-name", required=True)
    payload.add_argument("--developer-website-url")
    payload.add_argument("--privacy-url")
    payload.add_argument("--terms-of-use-url")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    if args.command == "activity-endpoint":
        print(build_activity_endpoint(args.project_endpoint, args.agent_name))
        return
    if args.command == "endpoint-patch":
        print(json.dumps(build_endpoint_patch(), separators=(",", ":")))
        return

    payload = build_publish_payload(
        display_name=args.display_name,
        bot_service_resource_id=args.bot_service_resource_id,
        app_version=args.app_version,
        short_description=args.short_description,
        full_description=args.full_description,
        developer_name=args.developer_name,
        developer_website_url=args.developer_website_url,
        privacy_url=args.privacy_url,
        terms_of_use_url=args.terms_of_use_url,
    )
    print(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
