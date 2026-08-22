import json
import os
import tempfile
import unittest
from argparse import Namespace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.ai.projects.models import ScheduleRun
from azure.core.exceptions import ResourceExistsError
from scripts.evaluate_foundry import (
    BEHAVIOR_EVALUATOR_NAME,
    DATASET_SHA_TAG,
    EvaluationTarget,
    build_run_data_source,
    build_schedule,
    build_testing_criteria,
    build_behavior_evaluator,
    dataset_sha256,
    load_dataset,
    load_evaluation_target,
    register_dataset,
    show_schedule,
)


class EvaluateFoundryTests(unittest.TestCase):
    def test_load_dataset_requires_expected_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps({"query": "hello"}) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "expected_behavior"):
                load_dataset(path)

    def test_dataset_sha256_is_content_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text('{"query":"hello"}\n', encoding="utf-8")

            first = dataset_sha256(path)
            second = dataset_sha256(path)

            self.assertEqual(first, second)
            self.assertEqual(len(first), 64)

    def test_existing_dataset_version_must_match_local_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(
                '{"query":"hello","expected_behavior":"reply"}\n',
                encoding="utf-8",
            )
            project_client = MagicMock()
            project_client.datasets.upload_file.side_effect = ResourceExistsError(
                "exists"
            )
            project_client.datasets.get.return_value = SimpleNamespace(
                tags={DATASET_SHA_TAG: "different"}
            )

            with self.assertRaisesRegex(RuntimeError, "immutable"):
                register_dataset(project_client, path, "dataset", "1")

    def test_target_requires_deployed_agent_version(self) -> None:
        environment = {
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
            "AGENT_MAF_POC_AGENT_NAME": "agent",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(
                RuntimeError,
                "AGENT_MAF_POC_AGENT_VERSION",
            ):
                load_evaluation_target()

    def test_run_data_source_targets_exact_agent_version(self) -> None:
        target = EvaluationTarget("model", "agent", "7")

        data_source = build_run_data_source("dataset-id", target)

        self.assertEqual(data_source["target"]["name"], "agent")
        self.assertEqual(data_source["target"]["version"], "7")
        self.assertEqual(data_source["source"]["id"], "dataset-id")

    def test_schedule_runs_daily_at_selected_utc_hour(self) -> None:
        target = EvaluationTarget("model", "agent", "7")
        schedule = build_schedule(
            "eval-id",
            "daily-run",
            build_run_data_source("dataset-id", target),
            9,
            target,
            "dataset",
            "2",
        )
        value = schedule.as_dict()

        self.assertTrue(value["enabled"])
        self.assertEqual(value["trigger"]["timeZone"], "UTC")
        self.assertEqual(value["trigger"]["schedule"]["hours"], [9])
        self.assertEqual(value["task"]["evalId"], "eval-id")
        self.assertEqual(value["tags"]["agentVersion"], "7")

    def test_schedule_rejects_invalid_hour(self) -> None:
        target = EvaluationTarget("model", "agent", "7")
        with self.assertRaisesRegex(ValueError, "between 0 and 23"):
            build_schedule(
                "eval-id",
                "daily-run",
                build_run_data_source("dataset-id", target),
                24,
                target,
                "dataset",
                "2",
            )

    def test_testing_criteria_include_behavioral_evaluator(self) -> None:
        criteria = build_testing_criteria("model", "3")
        behavioral = next(
            criterion
            for criterion in criteria
            if criterion["evaluator_name"] == BEHAVIOR_EVALUATOR_NAME
        )

        self.assertEqual(behavioral["evaluator_version"], "3")
        self.assertEqual(
            behavioral["data_mapping"]["expected_behavior"],
            "{{item.expected_behavior}}",
        )

    def test_behavioral_evaluator_uses_foundry_placeholders(self) -> None:
        evaluator = build_behavior_evaluator().as_dict()
        prompt = evaluator["definition"]["prompt_text"]

        self.assertIn("{{query}}", prompt)
        self.assertIn("{{response}}", prompt)
        self.assertIn("{{expected_behavior}}", prompt)

    def test_schedule_status_limits_runs_locally(self) -> None:
        project_client = MagicMock()
        project_client.beta.schedules.get.return_value = {"schedule_id": "daily"}
        project_client.beta.schedules.list_runs.return_value = [
            ScheduleRun(
                run_id=str(index),
                schedule_id="daily",
                success=True,
                trigger_time=datetime(2026, 1, index + 1, tzinfo=UTC),
                properties={},
            )
            for index in range(4)
        ]
        args = Namespace(schedule_id="daily", schedule_run_limit=2)

        with patch("builtins.print"):
            show_schedule(project_client, args)

        project_client.beta.schedules.list_runs.assert_called_once_with("daily")


if __name__ == "__main__":
    unittest.main()
