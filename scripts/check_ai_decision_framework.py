"""Report whether the curated Microsoft AI Decision Framework snapshot is current."""

import argparse
import json
from pathlib import Path

import httpx


MANIFEST_PATH = Path("data/ai-decision-framework/manifest.json")
COMMITS_URL = (
    "https://api.github.com/repos/microsoft/"
    "Microsoft-AI-Decision-Framework/commits/main"
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare the curated AI Decision Framework snapshot with upstream."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
    )
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    pinned = manifest["upstreamCommit"]
    response = httpx.get(
        COMMITS_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=30,
    )
    response.raise_for_status()
    latest = response.json()["sha"]
    print(f"Pinned commit: {pinned}")
    print(f"Latest commit: {latest}")
    if latest != pinned:
        raise SystemExit(
            "The upstream framework changed. Review the diff before refreshing "
            "the curated snapshot."
        )
    print("The curated framework snapshot matches upstream main.")
