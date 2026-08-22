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
    COMPREHENSIVE_DATASET_PATH,
    DATASET_SHA_TAG,
    EvaluationTarget,
    build_comprehensive_conversation_criteria,
    build_comprehensive_turn_criteria,
    build_jsonl_run_data_source,
    build_run_data_source,
    build_schedule,
    build_testing_criteria,
    build_behavior_evaluator,
    dataset_sha256,
    load_comprehensive_dataset,
    load_dataset,
    load_evaluation_target,
    register_dataset,
    run_comprehensive_evaluation,
    show_schedule,
)


class EvaluateFoundryTests(unittest.TestCase):
    def test_load_dataset_requires_expected_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps({"query": "hello"}) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "expected_behavior"):
                load_dataset(path)

    def test_comprehensive_dataset_has_valid_multi_turn_rows(self) -> None:
        items = load_comprehensive_dataset(COMPREHENSIVE_DATASET_PATH)

        self.assertGreaterEqual(len(items), 20)
        self.assertEqual(len({item["case_id"] for item in items}), len(items))
        self.assertTrue(all(len(item["messages"]) >= 4 for item in items))
        self.assertTrue(all(item["retrieval_ground_truth"] for item in items))
        self.assertTrue(all(item["retrieved_documents"] for item in items))

    def test_comprehensive_dataset_requires_response_to_match_conversation(self) -> None:
        item = {
            "case_id": "mismatch",
            "category": "test",
            "query": "hello",
            "response": "different",
            "ground_truth": "answer",
            "context": "context",
            "expected_behavior": "reply",
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "answer"},
            ],
            "retrieval_ground_truth": [{"document_id": "1"}],
            "retrieved_documents": [{"document_id": "1"}],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cases.jsonl"
            path.write_text(json.dumps(item) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "does not match response"):
                load_comprehensive_dataset(path)

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

    def test_smoke_criteria_remain_unchanged(self) -> None:
        criteria = build_testing_criteria("model", "3")

        self.assertEqual(
            [criterion["name"] for criterion in criteria],
            [
                "relevance",
                "task_adherence",
                "intent_resolution",
                "indirect_attack",
                "expected_behavior",
            ],
        )

    def test_comprehensive_turn_criteria_cover_applicable_evaluators(self) -> None:
        criteria = build_comprehensive_turn_criteria("model", "3")
        evaluators = {criterion["evaluator_name"] for criterion in criteria}

        self.assertEqual(
            evaluators,
            {
                "builtin.coherence",
                "builtin.fluency",
                "builtin.similarity",
                "builtin.f1_score",
                "builtin.bleu_score",
                "builtin.gleu_score",
                "builtin.rouge_score",
                "builtin.meteor_score",
                "builtin.retrieval",
                "builtin.document_retrieval",
                "builtin.groundedness",
                "builtin.groundedness_pro",
                "builtin.relevance",
                "builtin.response_completeness",
                "builtin.hate_unfairness",
                "builtin.sexual",
                "builtin.violence",
                "builtin.self_harm",
                "builtin.protected_material",
                "builtin.indirect_attack",
                "builtin.ungrounded_attributes",
                "builtin.task_adherence",
                "builtin.task_completion",
                "builtin.intent_resolution",
                "builtin.quality_grader",
                BEHAVIOR_EVALUATOR_NAME,
            },
        )
        self.assertNotIn("builtin.tool_call_accuracy", evaluators)
        expected_behavior = next(
            criterion
            for criterion in criteria
            if criterion["evaluator_name"] == BEHAVIOR_EVALUATOR_NAME
        )
        self.assertEqual(expected_behavior["evaluator_version"], "3")
        by_name = {criterion["evaluator_name"]: criterion for criterion in criteria}
        self.assertEqual(
            by_name["builtin.coherence"]["initialization_parameters"],
            {"model": "model"},
        )
        self.assertEqual(
            by_name["builtin.task_completion"]["initialization_parameters"],
            {"deployment_name": "model"},
        )
        self.assertEqual(
            by_name["builtin.quality_grader"]["initialization_parameters"],
            {"deployment_name": "model"},
        )

    def test_comprehensive_conversation_criteria_use_messages(self) -> None:
        criteria = build_comprehensive_conversation_criteria("model")

        self.assertEqual(
            {criterion["evaluator_name"] for criterion in criteria},
            {
                "builtin.customer_satisfaction",
                "builtin.task_completion",
                "builtin.coherence",
                "builtin.groundedness",
            },
        )
        self.assertTrue(
            all(
                criterion["data_mapping"] == {"messages": "{{item.messages}}"}
                for criterion in criteria
            )
        )
        self.assertTrue(
            all(
                criterion["initialization_parameters"] == {"model": "model"}
                for criterion in criteria
            )
        )

    def test_jsonl_run_data_source_uses_registered_dataset(self) -> None:
        self.assertEqual(
            build_jsonl_run_data_source("dataset-id"),
            {
                "type": "jsonl",
                "source": {"type": "file_id", "id": "dataset-id"},
            },
        )

    def test_comprehensive_run_sets_conversation_evaluation_level(self) -> None:
        project_client = MagicMock()
        openai_client = MagicMock()
        openai_client.evals.runs.create.side_effect = [
            SimpleNamespace(id="turn-run"),
            SimpleNamespace(id="conversation-run"),
        ]
        openai_client.evals.runs.output_items.list.return_value = []
        dataset = SimpleNamespace(id="dataset-id", name="dataset", version="1")
        evaluations = [
            ("turn", SimpleNamespace(id="turn-eval")),
            ("conversation", SimpleNamespace(id="conversation-eval")),
        ]
        args = Namespace(
            dataset=COMPREHENSIVE_DATASET_PATH,
            dataset_name="dataset",
            dataset_version="1",
            evaluation_name="comprehensive",
            run_name="comprehensive-run",
            results_root=Path(".foundry/results"),
            poll_seconds=1,
        )

        with (
            patch(
                "scripts.evaluate_foundry.prepare_comprehensive_evaluations",
                return_value=(dataset, evaluations, []),
            ),
            patch(
                "scripts.evaluate_foundry.poll_run",
                return_value=SimpleNamespace(status="completed"),
            ),
            patch("scripts.evaluate_foundry.save_json"),
            patch("builtins.print"),
        ):
            run_comprehensive_evaluation(
                project_client,
                openai_client,
                args,
                "model",
            )

        turn_call, conversation_call = openai_client.evals.runs.create.call_args_list
        self.assertEqual(turn_call.kwargs["name"], "comprehensive-run-turn")
        self.assertNotIn("extra_body", turn_call.kwargs)
        self.assertEqual(
            conversation_call.kwargs["name"],
            "comprehensive-run-conversation",
        )
        self.assertEqual(
            conversation_call.kwargs["extra_body"],
            {"evaluation_level": "conversation"},
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
