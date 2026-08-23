"""Load the canonical instructions shared by both intake agents."""

from pathlib import Path


DEFAULT_INSTRUCTIONS_PATH = (
    Path(__file__).resolve().parent / "instructions" / "intake_agent.md"
)


def load_intake_instructions(
    path: Path = DEFAULT_INSTRUCTIONS_PATH,
) -> str:
    try:
        instructions = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Agent instruction file does not exist: {path}") from exc

    if not instructions:
        raise RuntimeError(f"Agent instruction file is empty: {path}")
    return instructions
