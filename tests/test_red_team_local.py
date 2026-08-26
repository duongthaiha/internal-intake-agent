import argparse
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.red_team_local import (
    AgentTarget,
    ScanConfig,
    build_target_callback,
    default_azd_environment,
    resolve_config,
    resolve_targets,
    result_path,
    run_target_scan,
    summarize_scorecard,
)


class FakeCredential:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None


class FakeRisk:
    Violence = "violence"
    HateUnfairness = "hate"
    Sexual = "sexual"
    SelfHarm = "self-harm"


class FakeAttack:
    Base64 = "base64"


class RedTeamLocalTests(unittest.IsolatedAsyncioTestCase):
    def make_args(self, **overrides):
        values = {
            "target": "both",
            "project_endpoint": None,
            "environment": None,
            "hosted_agent_name": None,
            "hosted_agent_version": None,
            "prompt_agent_name": None,
            "prompt_agent_version": None,
            "scan_name": "smoke",
            "num_objectives": 1,
            "risk": None,
            "attack_strategy": None,
            "results_root": Path(".foundry/results"),
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_default_environment_reads_azd_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"defaultEnvironment": "maf-poc-byo"}),
                encoding="utf-8",
            )
            self.assertEqual(default_azd_environment(path), "maf-poc-byo")

    def test_config_uses_smoke_defaults(self) -> None:
        environment = {
            "RED_TEAM_PROJECT_ENDPOINT": (
                "https://redteam.services.ai.azure.com/api/projects/evaluation"
            ),
            "AZURE_ENV_NAME": "maf-poc-byo",
        }
        with patch.dict(os.environ, environment, clear=True):
            config = resolve_config(self.make_args())

        self.assertEqual(config.num_objectives, 1)
        self.assertEqual(
            config.risks,
            ("violence", "hate-unfairness", "sexual", "self-harm"),
        )
        self.assertEqual(config.attack_strategies, ("base64",))

    def test_config_rejects_non_project_endpoint(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RED_TEAM_PROJECT_ENDPOINT": "https://example.invalid",
                "AZURE_ENV_NAME": "dev",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Foundry project endpoint"):
                resolve_config(self.make_args())

    def test_both_targets_require_exact_versions(self) -> None:
        environment = {
            "AGENT_MAF_POC_AGENT_NAME": "hosted",
            "AGENT_MAF_POC_AGENT_VERSION": "14",
            "PROMPT_AGENT_NAME": "prompt",
            "PROMPT_AGENT_VERSION": "5",
        }
        with patch.dict(os.environ, environment, clear=True):
            targets = resolve_targets(self.make_args())

        self.assertEqual(
            targets,
            [
                AgentTarget("hosted", "hosted", "14"),
                AgentTarget("prompt", "prompt", "5"),
            ],
        )

    def test_prompt_target_fails_without_version(self) -> None:
        with patch.dict(os.environ, {"PROMPT_AGENT_NAME": "prompt"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "PROMPT_AGENT_VERSION"):
                resolve_targets(self.make_args(target="prompt"))

    async def test_callback_invokes_fresh_conversation(self) -> None:
        invoke = MagicMock(return_value="safe response")
        callback = build_target_callback(
            AgentTarget("hosted", "agent", "7"),
            "dev",
            invoke,
        )

        result = await callback(
            [SimpleNamespace(content=[SimpleNamespace(text="attack prompt")])]
        )

        self.assertEqual(
            result,
            {"messages": [{"role": "assistant", "content": "safe response"}]},
        )
        invoke.assert_called_once()
        self.assertTrue(invoke.call_args.kwargs["new_conversation"])

    async def test_callback_surfaces_agent_invocation_failure(self) -> None:
        callback = build_target_callback(
            AgentTarget("prompt", "agent", "5"),
            "dev",
            MagicMock(side_effect=RuntimeError("invocation failed")),
        )

        with self.assertRaisesRegex(RuntimeError, "invocation failed"):
            await callback([{"content": "attack prompt"}])

    def test_result_path_contains_target_and_timestamp(self) -> None:
        config = ScanConfig(
            project_endpoint="https://a.services.ai.azure.com/api/projects/p",
            environment="dev",
            results_root=Path("results"),
            scan_name="smoke scan",
            num_objectives=1,
            risks=("violence",),
            attack_strategies=("base64",),
        )
        path = result_path(
            config,
            AgentTarget("prompt", "prompt agent", "5"),
            datetime(2026, 8, 26, 12, 30, tzinfo=UTC),
        )

        self.assertEqual(
            path,
            Path(
                "results/dev/red-team-local/"
                "smoke-scan-prompt-prompt-agent-v5-20260826T123000Z"
            ),
        )

    def test_summary_requires_scorecard_sections(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "scorecard"):
            summarize_scorecard({})

    def test_summary_rejects_zero_attack_scan(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "zero attacks"):
            summarize_scorecard(
                {
                    "scorecard": {
                        "risk_category_summary": [
                            {"overall_asr": 0.0, "overall_total": 0}
                        ],
                        "attack_technique_summary": [
                            {"overall_asr": 0.0, "overall_total": 0}
                        ],
                    }
                }
            )

    async def test_run_writes_and_reads_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ScanConfig(
                project_endpoint="https://a.services.ai.azure.com/api/projects/p",
                environment="dev",
                results_root=Path(directory),
                scan_name="smoke",
                num_objectives=1,
                risks=("violence", "hate-unfairness", "sexual", "self-harm"),
                attack_strategies=("base64",),
            )
            red_team_instance = MagicMock()

            async def scan(**kwargs):
                output_directory = Path(kwargs["output_path"])
                output_directory.mkdir(parents=True, exist_ok=True)
                (output_directory / "evaluation_results.json").write_text(
                    json.dumps(
                        {
                            "scorecard": {
                                "risk_category_summary": [
                                    {"overall_asr": 0.0, "overall_total": 2}
                                ],
                                "attack_technique_summary": [
                                    {
                                        "baseline_asr": 0.0,
                                        "easy_complexity_asr": 0.0,
                                        "overall_total": 2,
                                    }
                                ],
                            }
                        }
                    ),
                    encoding="utf-8",
                )

            red_team_instance.scan.side_effect = scan
            red_team_type = MagicMock(return_value=red_team_instance)

            output = await run_target_scan(
                config,
                AgentTarget("hosted", "agent", "14"),
                red_team_type,
                FakeRisk,
                FakeAttack,
                credential_factory=FakeCredential,
                invoke=MagicMock(return_value="response"),
            )

            self.assertTrue(output.exists())
            red_team_type.assert_called_once()
            self.assertEqual(
                red_team_type.call_args.kwargs["output_dir"],
                str(output.parent),
            )
            self.assertEqual(
                red_team_instance.scan.call_args.kwargs["attack_strategies"],
                ["base64"],
            )
            self.assertTrue(
                red_team_instance.scan.call_args.kwargs["skip_upload"]
            )

    async def test_run_fails_when_sdk_does_not_write_scorecard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = ScanConfig(
                project_endpoint="https://a.services.ai.azure.com/api/projects/p",
                environment="dev",
                results_root=Path(directory),
                scan_name="smoke",
                num_objectives=1,
                risks=("violence",),
                attack_strategies=("base64",),
            )
            red_team_instance = MagicMock()

            async def scan(**kwargs):
                Path(kwargs["output_path"]).mkdir(parents=True, exist_ok=True)

            red_team_instance.scan.side_effect = scan

            with self.assertRaisesRegex(RuntimeError, "did not create"):
                await run_target_scan(
                    config,
                    AgentTarget("hosted", "agent", "14"),
                    MagicMock(return_value=red_team_instance),
                    FakeRisk,
                    FakeAttack,
                    credential_factory=FakeCredential,
                )


if __name__ == "__main__":
    unittest.main()
