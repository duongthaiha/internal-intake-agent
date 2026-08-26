"""Load trusted repository-bundled skills for the hosted agent."""

from pathlib import Path

import yaml
from agent_framework import SkillsProvider


DEFAULT_SKILLS_PATH = Path(__file__).resolve().parent / "skills"
PLATFORM_ADVISOR_SKILL = "microsoft-ai-platform-advisor"


def build_skills_provider(
    skills_path: Path = DEFAULT_SKILLS_PATH,
) -> SkillsProvider:
    skill_file = skills_path / PLATFORM_ADVISOR_SKILL / "SKILL.md"
    try:
        content = skill_file.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Hosted agent skill does not exist: {skill_file}") from exc

    frontmatter = _parse_frontmatter(content, skill_file)
    if frontmatter.get("name") != PLATFORM_ADVISOR_SKILL:
        raise RuntimeError(
            f"{skill_file} must define skill name '{PLATFORM_ADVISOR_SKILL}'."
        )
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        raise RuntimeError(f"{skill_file} must define a non-empty description.")

    return SkillsProvider.from_paths(
        skills_path,
        script_extensions=(),
        disable_load_skill_approval=True,
        disable_read_skill_resource_approval=True,
        source_id="hosted_agent_skills",
    )


def _parse_frontmatter(content: str, path: Path) -> dict[str, object]:
    if not content.startswith("---\n"):
        raise RuntimeError(f"{path} must begin with YAML front matter.")
    marker = content.find("\n---\n", 4)
    if marker < 0:
        raise RuntimeError(f"{path} has unterminated YAML front matter.")
    parsed = yaml.safe_load(content[4:marker])
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{path} front matter must be a YAML object.")
    return parsed
