import argparse
import os
import secrets

from agent_framework.devui import register_cleanup, serve
from agent_framework_azure_cosmos import CosmosHistoryProvider
from agent_framework_azure_ai_search import AzureAISearchContextProvider
from dotenv import load_dotenv
from watchfiles import run_process

from agent import build_agent
from logging_config import LOG_LEVEL_NAMES, configure_logging, get_log_level


def run_server(
    port: int,
    auto_open: bool,
    tracing: bool,
    auth_token: str,
    log_level: str,
) -> None:
    load_dotenv(override=True)
    configure_logging(log_level)
    components = build_agent()
    cleanup_hooks = [components.credential.close]
    if isinstance(components.history_provider, CosmosHistoryProvider):
        cleanup_hooks.append(components.history_provider.close)
    if isinstance(components.rag_provider, AzureAISearchContextProvider):
        cleanup_hooks.append(components.rag_provider.close)
    register_cleanup(components.agent, *cleanup_hooks)

    print(f"DevUI auth token: {auth_token}", flush=True)
    print(
        f"API authorization header: Authorization: Bearer {auth_token}",
        flush=True,
    )

    serve(
        entities=[components.agent],
        host="127.0.0.1",
        port=port,
        auto_open=auto_open,
        instrumentation_enabled=tracing,
        auth_enabled=True,
        auth_token=auth_token,
    )


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Run the Microsoft Agent Framework agent in DevUI."
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open DevUI automatically in the default browser.",
    )
    tracing_group = parser.add_mutually_exclusive_group()
    tracing_group.add_argument(
        "--tracing",
        dest="tracing",
        action="store_true",
        help="Enable OpenTelemetry tracing in DevUI (default).",
    )
    tracing_group.add_argument(
        "--no-tracing",
        dest="tracing",
        action="store_false",
        help="Disable OpenTelemetry tracing in DevUI.",
    )
    parser.set_defaults(tracing=True)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Restart DevUI when local project files change.",
    )
    parser.add_argument(
        "--log-level",
        type=str.upper,
        choices=LOG_LEVEL_NAMES,
        help="Override LOG_LEVEL for this process.",
    )
    args = parser.parse_args()
    log_level = get_log_level(args.log_level)

    auth_token = os.getenv("DEVUI_AUTH_TOKEN") or secrets.token_urlsafe(32)
    if args.reload:
        configure_logging(log_level)
        print(f"DevUI reload enabled at http://127.0.0.1:{args.port}", flush=True)
        run_process(
            ".",
            target=run_server,
            kwargs={
                "port": args.port,
                "auto_open": False,
                "tracing": args.tracing,
                "auth_token": auth_token,
                "log_level": log_level,
            },
        )
        return

    run_server(
        port=args.port,
        auto_open=not args.no_open,
        tracing=args.tracing,
        auth_token=auth_token,
        log_level=log_level,
    )


if __name__ == "__main__":
    main()
