"""Hosted-agent invocation, response parsing, and conversation replay."""

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from typing import Any

from scripts.foundry_eval.config import EvaluationTarget
from scripts.foundry_eval.datasets import message_text


def extract_agent_output_text(response: dict[str, Any]) -> str:
    if response.get("status") == "failed":
        error = response.get("error")
        if isinstance(error, dict):
            raise RuntimeError(
                f"Hosted agent failed ({error.get('code', 'unknown')}): "
                f"{error.get('message', 'no message')}"
            )
        raise RuntimeError("Hosted agent returned failed status")

    texts: list[str] = []
    output = response.get("output")
    if isinstance(output, list):
        for output_item in output:
            if not isinstance(output_item, dict):
                continue
            content = output_item.get("content")
            if not isinstance(content, list):
                continue
            for content_item in content:
                if (
                    isinstance(content_item, dict)
                    and content_item.get("type") == "output_text"
                    and isinstance(content_item.get("text"), str)
                ):
                    texts.append(content_item["text"])
    text = "\n".join(texts).strip()
    if not text:
        raise RuntimeError("Hosted agent response contained no output_text")
    return text


def parse_raw_agent_response(raw_response: str) -> str:
    _, separator, body = raw_response.partition("\r\n\r\n")
    if not separator:
        _, separator, body = raw_response.partition("\n\n")
    if not separator:
        raise RuntimeError("Raw azd response is missing HTTP headers")

    content_type = ""
    header_text = raw_response[: raw_response.index(separator)]
    for header in header_text.splitlines():
        name, _, value = header.partition(":")
        if name.lower() == "content-type":
            content_type = value.strip().lower()
            break

    if "application/json" in content_type:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Raw azd response contains invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("Raw azd response JSON is not an object")
        return extract_agent_output_text(payload)

    completed_response: dict[str, Any] | None = None
    event_name = ""
    for line in body.splitlines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        try:
            event = json.loads(data)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Raw azd response contains invalid SSE JSON") from exc
        if event_name == "response.output_text.delta":
            pass
        elif event_name == "response.completed":
            response = event.get("response")
            if isinstance(response, dict):
                completed_response = response
        elif event_name in {
            "response.incomplete",
            "response.failed",
            "response.cancelled",
        }:
            response = event.get("response")
            detail = ""
            if isinstance(response, dict):
                error = response.get("error")
                if isinstance(error, dict):
                    detail = str(error.get("message") or "")
            raise RuntimeError(
                f"Hosted agent stream ended with {event_name}"
                f"{f': {detail}' if detail else ''}"
            )
        elif event_name == "error":
            raise RuntimeError(
                f"Hosted agent stream failed ({event.get('code', 'unknown')}): "
                f"{event.get('message', 'no message')}"
            )
        event_name = ""

    if completed_response is not None:
        return extract_agent_output_text(completed_response)
    raise RuntimeError("Raw azd response contained no response.completed event")


def invoke_agent_turn(
    message: str,
    target: EvaluationTarget,
    environment_name: str,
    *,
    new_conversation: bool,
    runner: Any = subprocess.run,
) -> str:
    command = [
        "azd",
        "ai",
        "agent",
        "invoke",
        "--environment",
        environment_name,
        "--version",
        target.agent_version,
        "--output",
        "raw",
        "--no-prompt",
        target.agent_name,
    ]
    if new_conversation:
        command.extend(["--new-session", "--new-conversation"])
    command.append(message)
    process_environment = os.environ.copy()
    process_environment["AZURE_DEV_USER_AGENT"] = "microsoft_foundry_skill"
    try:
        completed = runner(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=process_environment,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise RuntimeError(
            f"Hosted agent invocation failed: {detail or 'azd returned a nonzero exit code'}"
        ) from exc
    return parse_raw_agent_response(completed.stdout)


def replay_conversations(
    items: list[dict[str, Any]],
    target: EvaluationTarget,
    environment_name: str,
    invoke: Any = invoke_agent_turn,
) -> list[dict[str, Any]]:
    replayed_items: list[dict[str, Any]] = []
    for item in items:
        generated_messages: list[dict[str, Any]] = []
        first_user_turn = True
        final_response = ""
        for message in item["messages"]:
            role = message["role"]
            if role == "system":
                generated_messages.append(message)
                continue
            if role == "assistant":
                continue
            if role != "user":
                raise RuntimeError(
                    f"Replay does not support {role!r} messages in case "
                    f"{item['case_id']!r}"
                )
            generated_messages.append(message)
            final_response = invoke(
                message_text(message),
                target,
                environment_name,
                new_conversation=first_user_turn,
            )
            first_user_turn = False
            generated_messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": final_response}],
                }
            )

        if not final_response:
            raise RuntimeError(f"Case {item['case_id']!r} contains no user turn")
        replayed = dict(item)
        replayed["messages"] = generated_messages
        replayed["reviewed_response"] = item["response"]
        replayed["response"] = final_response
        replayed["replay"] = {
            "agent_name": target.agent_name,
            "agent_version": target.agent_version,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        replayed_items.append(replayed)
    return replayed_items


def replay_dataset_version(
    source_version: str,
    source_sha: str,
    agent_version: str,
    capture_id: str,
) -> str:
    safe_agent_version = re.sub(r"[^A-Za-z0-9._-]", "-", agent_version)
    safe_capture_id = re.sub(r"[^A-Za-z0-9._-]", "-", capture_id)
    return (
        f"{source_version}-agent-{safe_agent_version}-"
        f"{source_sha[:12]}-{safe_capture_id}"
    )
