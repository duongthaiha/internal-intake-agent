import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from agent_framework import FileSkillsSource, SkillsProvider, SkillsSourceContext

from agents.hosted.skills import (
    DEFAULT_SKILLS_PATH,
    PLATFORM_ADVISOR_SKILL,
    build_skills_provider,
)


class HostedSkillsTests(unittest.TestCase):
    def test_builds_trusted_file_skills_provider(self) -> None:
        provider = build_skills_provider()

        self.assertIsInstance(provider, SkillsProvider)
        self.assertEqual(provider.source_id, "hosted_agent_skills")

    def test_rejects_missing_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "does not exist"):
                build_skills_provider(Path(directory))

    def test_rejects_wrong_skill_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_directory = root / PLATFORM_ADVISOR_SKILL
            skill_directory.mkdir()
            (skill_directory / "SKILL.md").write_text(
                "---\nname: wrong\ndescription: Test\n---\n\nBody\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "must define skill name"):
                build_skills_provider(root)

    def test_skill_contains_version_marker(self) -> None:
        skill_file = (
            Path("agents")
            / "hosted"
            / "skills"
            / PLATFORM_ADVISOR_SKILL
            / "SKILL.md"
        )

        self.assertIn(
            "hosted-platform-advisor-v2",
            skill_file.read_text(encoding="utf-8"),
        )

    def test_custom_instruction_contains_customer_platform_mapping(self) -> None:
        instruction_file = (
            Path("agents")
            / "hosted"
            / "skills"
            / PLATFORM_ADVISOR_SKILL
            / "custom-instruction.md"
        )

        content = instruction_file.read_text(encoding="utf-8")
        self.assertIn("Nebula for AI Development on Azure", content)
        self.assertIn("disposition\n  `build`", content)
        self.assertIn("Do not apply the Nebula preference to `no_ai`", content)


class HostedSkillsDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_only_instruction_and_reference_resources(self) -> None:
        source = FileSkillsSource(
            DEFAULT_SKILLS_PATH,
            script_extensions=(),
        )

        skills = await source.get_skills(SkillsSourceContext(agent=MagicMock()))

        self.assertEqual(
            [PLATFORM_ADVISOR_SKILL],
            [skill.frontmatter.name for skill in skills],
        )
        self.assertIsNone(await skills[0].get_script("anything.py"))
        self.assertIsNotNone(
            await skills[0].get_resource("framework-reference.md")
        )
        self.assertIsNotNone(
            await skills[0].get_resource("custom-instruction.md")
        )


if __name__ == "__main__":
    unittest.main()
