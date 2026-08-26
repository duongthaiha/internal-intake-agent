import json
import os
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from azure.ai.projects.models import ScheduleRun
from azure.core.exceptions import ResourceExistsError
from scripts.evaluate_foundry import (
    BEHAVIOR_EVALUATOR_NAME,
    COMPREHENSIVE_DATASET_PATH,
    DATASET_SHA_TAG,
    PREAPPROVAL_TOOL_EVALUATOR_NAME,
    TOOL_DATASET_PATH,
    TOOL_OPENAPI_PATH,
    EvaluationTarget,
    build_intake_tool_definitions,
    build_comprehensive_agent_criteria,
    build_comprehensive_conversation_criteria,
    build_comprehensive_turn_criteria,
    build_jsonl_run_data_source,
    build_inline_jsonl_run_data_source,
    build_inline_literal_run_data_source,
    build_inline_run_data_source,
    build_preapproval_tool_evaluator,
    build_run_data_source,
    build_schedule,
    build_testing_criteria,
    build_tool_data_source_config,
    build_tool_score_items,
    build_tool_testing_criteria,
    build_behavior_evaluator,
    dataset_sha256,
    load_comprehensive_dataset,
    load_dataset,
    load_evaluation_target,
    load_tool_dataset,
    find_or_create_evaluation,
    filter_criteria_for_region,
    parse_args,
    parse_raw_agent_response,
    prepare_tool_evaluation,
    register_dataset,
    replay_conversations,
    replay_dataset_version,
    run_comprehensive_evaluation,
    score_tool_items_locally,
    show_schedule,
    analyze_output_items,
    upsert_comprehensive_agent_schedule,
)


class EvaluateFoundryTests(unittest.TestCase):
    def test_intake_tool_definitions_cover_prompt_operations(self) -> None:
        definitions = build_intake_tool_definitions(
            TOOL_OPENAPI_PATH,
            tool_name_style="prompt",
        )

        names = [definition["name"] for definition in definitions]
        self.assertEqual(
            names,
            [
                "create_intake_request",
                "get_intake_request",
                "list_intake_requests",
                "replace_intake_request",
                "submit_intake_request",
            ],
        )
        by_name = {
            definition["name"]: definition
            for definition in definitions
        }
        create_parameters = by_name["create_intake_request"]["parameters"]
        self.assertIn("IntakeRequest", create_parameters["required"])
        self.assertEqual(
            create_parameters["properties"]["IntakeRequest"]["required"],
            [
                "title",
                "problemOpportunity",
                "proposedIdea",
                "expectedOutcome",
                "requester",
            ],
        )
        replace_parameters = by_name["replace_intake_request"]["parameters"]
        self.assertEqual(
            replace_parameters["required"],
            ["request_id", "If-Match", "IntakeRequest"],
        )
        submit_parameters = by_name["submit_intake_request"]["parameters"]
        self.assertEqual(
            submit_parameters["required"],
            ["request_id", "If-Match"],
        )
        self.assertEqual(
            submit_parameters["properties"]["body"],
            {"type": "string", "description": "Request body"},
        )

    def test_intake_tool_definitions_prefix_hosted_names(self) -> None:
        definitions = build_intake_tool_definitions(
            TOOL_OPENAPI_PATH,
            tool_name_style="hosted",
        )

        self.assertTrue(
            all(
                definition["name"].startswith("intake_mcp___")
                for definition in definitions
            )
        )

    def test_tool_dataset_adds_definitions_to_every_case(self) -> None:
        items = load_tool_dataset(
            TOOL_DATASET_PATH,
            tool_name_style="prompt",
        )

        self.assertGreaterEqual(len(items), 9)
        self.assertTrue(all(len(item["tool_definitions"]) == 5 for item in items))
        self.assertIsNot(
            items[0]["tool_definitions"],
            items[1]["tool_definitions"],
        )
        self.assertEqual(
            items[0]["tool_expectation"]["allowed_calls"][0]["name"],
            "list_intake_requests",
        )

    def test_tool_criteria_use_structured_agent_output(self) -> None:
        criteria = build_tool_testing_criteria("model", "1")
        by_name = {criterion["name"]: criterion for criterion in criteria}

        self.assertEqual(
            set(by_name),
            {
                "preapproval_tool_call",
            },
        )
        tool_criterion = by_name["preapproval_tool_call"]
        self.assertEqual(
            tool_criterion["evaluator_name"],
            PREAPPROVAL_TOOL_EVALUATOR_NAME,
        )
        self.assertEqual(tool_criterion["evaluator_version"], "1")
        self.assertEqual(
            tool_criterion.get("data_mapping"),
            None,
        )
        self.assertEqual(
            tool_criterion["initialization_parameters"],
            {"deployment_name": "model", "pass_threshold": 1},
        )
    def test_tool_data_source_requires_tool_definitions(self) -> None:
        config = build_tool_data_source_config()

        self.assertEqual(
            config["item_schema"]["required"],
            [
                "query",
                "expected_behavior",
                "tool_expectation",
                "tool_definitions",
            ],
        )

    def test_preapproval_tool_evaluator_scores_approved_calls(self) -> None:
        namespace: dict[str, object] = {}
        evaluator = build_preapproval_tool_evaluator()
        exec(evaluator.definition.code_text, namespace)
        grade = namespace["grade"]
        response = [
            {
                "role": "assistant",
                "content": json.dumps(
                    [
                        {
                            "type": "function_call",
                            "name": "create_intake_request",
                            "arguments": {
                                "IntakeRequest": {"title": "Example"},
                            },
                        }
                    ]
                ),
            },
            {
                "role": "assistant",
                "content": json.dumps(
                    [
                        {
                            "type": "mcp_approval_request",
                            "name": "create_intake_request",
                            "arguments": {
                                "IntakeRequest": {"title": "Example"},
                                "Idempotency-Key": "generated",
                            },
                        }
                    ]
                ),
            },
        ]
        expectation = {
            "minimum_calls": 1,
            "maximum_calls": 1,
            "allowed_calls": [
                {
                    "name": "create_intake_request",
                    "arguments": {"IntakeRequest": {"title": "Example"}},
                    "allowed_extra_arguments": ["Idempotency-Key"],
                }
            ],
        }

        self.assertEqual(
            grade({"output": response}, {"tool_expectation": expectation}),
            0.0,
        )
        response[0]["content"] = json.dumps(
            [
                {
                    "type": "function_call",
                    "name": "create_intake_request",
                    "arguments": {
                        "IntakeRequest": {"title": "Example"},
                        "Idempotency-Key": "generated",
                    },
                }
            ]
        )
        self.assertEqual(
            grade({"output": response}, {"tool_expectation": expectation}),
            1.0,
        )
        self.assertEqual(
            grade(
                {"output": json.dumps(response)},
                {"item": {"tool_expectation": json.dumps(expectation)}},
            ),
            1.0,
        )
        self.assertEqual(
            grade(
                {},
                {
                    "item.response": response,
                    "item.tool_expectation.minimum_calls": 1,
                    "item.tool_expectation.maximum_calls": 1,
                    "item.tool_expectation.allowed_calls": expectation[
                        "allowed_calls"
                    ],
                },
            ),
            1.0,
        )

    def test_preapproval_tool_evaluator_rejects_unapproved_function_call(self) -> None:
        namespace: dict[str, object] = {}
        evaluator = build_preapproval_tool_evaluator()
        exec(evaluator.definition.code_text, namespace)
        grade = namespace["grade"]
        response = [
            {
                "role": "assistant",
                "content": json.dumps(
                    [
                        {
                            "type": "function_call",
                            "name": "submit_intake_request",
                            "arguments": {},
                        }
                    ]
                ),
            }
        ]

        self.assertEqual(
            grade(
                {"output": response},
                {
                    "tool_expectation": {
                        "minimum_calls": 0,
                        "maximum_calls": 0,
                        "allowed_calls": [],
                    },
                },
            ),
            0.0,
        )

    def test_registered_tool_dataset_materializes_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project_client = MagicMock()
            project_client.datasets.upload_file.return_value = SimpleNamespace(
                data_uri="azureml://dataset",
                is_reference=False,
                connection_name=None,
            )
            project_client.datasets.create_or_update.return_value = SimpleNamespace(
                id="dataset-id",
                name="tool-dataset",
                version="1",
            )
            project_client.beta.evaluators.get_version.return_value = SimpleNamespace(
                version="1"
            )
            openai_client = MagicMock()
            openai_client.evals.list.return_value = []
            openai_client.evals.create.return_value = SimpleNamespace(id="eval-id")
            args = Namespace(
                dataset=TOOL_DATASET_PATH,
                dataset_name="tool-dataset",
                dataset_version="1",
                evaluation_name="tool-evaluation",
                inline_data=False,
                replay_root=Path(directory),
                tool_openapi=TOOL_OPENAPI_PATH,
                tool_name_style="prompt",
            )

            with patch.dict(os.environ, {"AZURE_LOCATION": "eastus"}, clear=False):
                _, _, items, prepared_path = prepare_tool_evaluation(
                    project_client,
                    openai_client,
                    args,
                    EvaluationTarget("model", "agent", "1"),
                )

            registered_path = Path(
                project_client.datasets.upload_file.call_args.kwargs["file_path"]
            )
            self.assertEqual(registered_path, prepared_path)
            self.assertEqual(
                project_client.datasets.upload_file.call_args.kwargs["name"],
                "tool-dataset-prompt",
            )
            self.assertTrue(prepared_path.exists())
            registered_items = [
                json.loads(line)
                for line in prepared_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(registered_items, items)
            self.assertTrue(all("tool_definitions" in item for item in registered_items))

    def test_tool_score_items_extract_capture_output(self) -> None:
        output_items = [
            {
                "datasource_item": {
                    "item": {
                        "query": "List requests",
                        "tool_expectation": {
                            "minimum_calls": 1,
                            "maximum_calls": 1,
                            "allowed_calls": [],
                        },
                    }
                },
                "sample": {
                    "output": [
                        {
                            "role": "assistant",
                            "content": "[]",
                        }
                    ]
                },
            }
        ]

        self.assertEqual(
            build_tool_score_items(output_items),
            [
                {
                    "query": "List requests",
                    "response": [
                        {
                            "role": "assistant",
                            "content": "[]",
                        }
                    ],
                    "tool_expectation": {
                        "minimum_calls": 1,
                        "maximum_calls": 1,
                        "allowed_calls": [],
                    },
                }
            ],
        )

    def test_tool_score_items_are_scored_deterministically(self) -> None:
        items = [
            {
                "query": "List requests",
                "response": [
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            [
                                {
                                    "type": "mcp_approval_request",
                                    "name": "list_intake_requests",
                                    "arguments": {"limit": 100},
                                }
                            ]
                        ),
                    }
                ],
                "tool_expectation": {
                    "minimum_calls": 1,
                    "maximum_calls": 1,
                    "allowed_calls": [
                        {
                            "name": "list_intake_requests",
                            "arguments": {},
                            "allowed_extra_arguments": ["limit"],
                        }
                    ],
                },
            }
        ]

        self.assertEqual(score_tool_items_locally(items)[0]["score"], 1.0)

    def test_tools_suite_resolves_defaults(self) -> None:
        with patch(
            "sys.argv",
            [
                "evaluate_foundry.py",
                "--suite",
                "tools",
                "--tool-name-style",
                "prompt",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.dataset, TOOL_DATASET_PATH)
        self.assertEqual(args.dataset_name, "maf-poc-intake-tools")
        self.assertEqual(args.dataset_version, "1")
        self.assertEqual(args.tool_name_style, "prompt")

    def test_tools_suite_rejects_scheduling(self) -> None:
        with (
            patch(
                "sys.argv",
                [
                    "evaluate_foundry.py",
                    "--suite",
                    "tools",
                    "--action",
                    "schedule",
                ],
            ),
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit),
        ):
            parse_args()

    def test_analysis_counts_nested_evaluator_errors(self) -> None:
        analysis = analyze_output_items(
            [
                {
                    "results": [
                        {
                            "status": "error",
                            "sample": {
                                "error": {
                                    "message": (
                                        "PermissionDenied: principal lacks the "
                                        "required data action"
                                    )
                                }
                            },
                        },
                        {
                            "status": "error",
                            "sample": {
                                "error": {
                                    "message": "Capability is not supported in region"
                                }
                            },
                        },
                    ]
                }
            ]
        )

        self.assertEqual(analysis["errored_items"], 1)
        self.assertEqual(analysis["errored_results"], 2)
        self.assertEqual(analysis["clusters"]["permission_error"], 1)
        self.assertEqual(analysis["clusters"]["unsupported_capability"], 1)

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
        self.assertTrue(all(item["agent_query"].strip() for item in items))
        self.assertTrue(
            all(
                all(
                    message_text in item["agent_query"]
                    for message_text in (
                        "".join(
                            content["text"]
                            for content in message["content"]
                            if content["type"] == "text"
                        )
                        for message in item["messages"]
                        if message["role"] == "user"
                    )
                )
                for item in items
            )
        )

    def test_comprehensive_dataset_requires_response_to_match_conversation(self) -> None:
        item = {
            "case_id": "mismatch",
            "category": "test",
            "agent_query": "hello",
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

    def test_target_accepts_generic_prompt_agent_environment(self) -> None:
        environment = {
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
            "EVALUATION_AGENT_NAME": "prompt-agent",
            "EVALUATION_AGENT_VERSION": "3",
        }
        with patch.dict(os.environ, environment, clear=True):
            target = load_evaluation_target()

        self.assertEqual(target.agent_name, "prompt-agent")
        self.assertEqual(target.agent_version, "3")

    def test_run_data_source_targets_exact_agent_version(self) -> None:
        target = EvaluationTarget("model", "agent", "7")

        data_source = build_run_data_source("dataset-id", target)

        self.assertEqual(data_source["target"]["name"], "agent")
        self.assertEqual(data_source["target"]["version"], "7")
        self.assertEqual(data_source["source"]["id"], "dataset-id")

    def test_agent_run_data_source_uses_standalone_query(self) -> None:
        target = EvaluationTarget("model", "agent", "7")

        data_source = build_run_data_source("dataset-id", target, "agent_query")

        self.assertEqual(
            data_source["input_messages"]["template"][0]["content"]["text"],
            "{{item.agent_query}}",
        )

    def test_inline_agent_data_source_wraps_items(self) -> None:
        target = EvaluationTarget("model", "agent", "7")
        items = [{"query": "hello", "expected_behavior": "reply"}]

        data_source = build_inline_run_data_source(items, target)

        self.assertEqual(data_source["source"]["type"], "file_content")
        self.assertEqual(data_source["source"]["content"], [{"item": items[0]}])
        self.assertEqual(data_source["target"]["version"], "7")

    def test_inline_agent_data_source_supports_nested_query(self) -> None:
        target = EvaluationTarget("model", "agent", "7")

        data_source = build_inline_run_data_source(
            [{"query": "hello"}],
            target,
            query_field="item.query",
        )

        self.assertEqual(
            data_source["input_messages"]["template"][0]["content"]["text"],
            "{{item.item.query}}",
        )

    def test_inline_literal_data_source_avoids_template_lookup(self) -> None:
        target = EvaluationTarget("model", "agent", "7")

        data_source = build_inline_literal_run_data_source(
            {"query": "hello"},
            target,
        )

        self.assertEqual(
            data_source["input_messages"]["template"][0]["content"]["text"],
            "hello",
        )

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

    def test_comprehensive_agent_criteria_map_live_output(self) -> None:
        criteria = build_comprehensive_agent_criteria("model", "3")
        by_evaluator = {
            criterion["evaluator_name"]: criterion for criterion in criteria
        }

        self.assertNotIn("builtin.retrieval", by_evaluator)
        self.assertNotIn("builtin.document_retrieval", by_evaluator)
        self.assertEqual(
            by_evaluator["builtin.coherence"]["data_mapping"]["query"],
            "{{item.agent_query}}",
        )
        self.assertEqual(
            by_evaluator["builtin.coherence"]["data_mapping"]["response"],
            "{{sample.output_text}}",
        )
        self.assertEqual(
            by_evaluator["builtin.task_adherence"]["data_mapping"]["response"],
            "{{sample.output_items}}",
        )

    def test_uk_south_filters_region_unsupported_evaluators(self) -> None:
        criteria = build_comprehensive_turn_criteria("model", "3")

        with patch("builtins.print"):
            filtered = filter_criteria_for_region(criteria, "uksouth")

        evaluators = {criterion["evaluator_name"] for criterion in filtered}
        self.assertNotIn("builtin.indirect_attack", evaluators)
        self.assertNotIn("builtin.groundedness_pro", evaluators)
        self.assertNotIn("builtin.protected_material", evaluators)
        self.assertIn("builtin.groundedness", evaluators)
        self.assertIn("builtin.task_adherence", evaluators)

    def test_jsonl_run_data_source_uses_registered_dataset(self) -> None:
        self.assertEqual(
            build_jsonl_run_data_source("dataset-id"),
            {
                "type": "jsonl",
                "source": {"type": "file_id", "id": "dataset-id"},
            },
        )

    def test_inline_jsonl_data_source_wraps_items(self) -> None:
        items = [{"messages": [{"role": "user", "content": "hello"}]}]

        self.assertEqual(
            build_inline_jsonl_run_data_source(items),
            {
                "type": "jsonl",
                "source": {
                    "type": "file_content",
                    "content": [{"item": items[0]}],
                },
            },
        )

    def test_versioned_evaluation_name_changes_with_mapping(self) -> None:
        openai_client = MagicMock()
        openai_client.evals.list.return_value = []
        openai_client.evals.create.side_effect = [
            SimpleNamespace(id="first"),
            SimpleNamespace(id="second"),
        ]
        first = [
            {
                "name": "quality",
                "evaluator_name": "builtin.coherence",
                "data_mapping": {"response": "{{item.response}}"},
            }
        ]
        second = [
            {
                "name": "quality",
                "evaluator_name": "builtin.coherence",
                "data_mapping": {"response": "{{sample.output_text}}"},
            }
        ]

        find_or_create_evaluation(
            openai_client,
            "evaluation",
            first,
            version_definition=True,
        )
        find_or_create_evaluation(
            openai_client,
            "evaluation",
            second,
            version_definition=True,
        )

        names = [
            call.kwargs["name"]
            for call in openai_client.evals.create.call_args_list
        ]
        self.assertNotEqual(names[0], names[1])
        self.assertTrue(all(name.startswith("evaluation-d") for name in names))

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
                "scripts.foundry_eval.suites.comprehensive."
                "prepare_comprehensive_evaluations",
                return_value=(dataset, evaluations, []),
            ),
            patch(
                "scripts.foundry_eval.suites.comprehensive.poll_run",
                return_value=SimpleNamespace(status="completed"),
            ),
            patch("scripts.foundry_eval.suites.comprehensive.update_eval_metadata"),
            patch("scripts.foundry_eval.suites.comprehensive.save_json"),
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

    def test_comprehensive_agent_schedule_uses_prepared_inline_items(self) -> None:
        # Regression test: upsert_comprehensive_agent_schedule previously
        # discarded the dataset items returned by
        # prepare_comprehensive_agent_evaluation and then referenced an
        # undefined `dataset_items` name when building the inline data
        # source, raising NameError for --inline-data schedules.
        project_client = MagicMock()
        openai_client = MagicMock()
        dataset = SimpleNamespace(id="dataset-id", name="dataset", version="1")
        evaluation = SimpleNamespace(id="eval-id")
        dataset_items = [{"agent_query": "hello", "query": "hello"}]
        target = EvaluationTarget("model", "agent", "7")
        args = Namespace(
            dataset=COMPREHENSIVE_DATASET_PATH,
            dataset_name="dataset",
            dataset_version="1",
            evaluation_name="comprehensive-agent",
            run_name="comprehensive-agent-run",
            inline_data=True,
            schedule_hour_utc=9,
            schedule_id="maf-poc-daily-comprehensive",
            schedule_cache_root=Path(tempfile.mkdtemp()),
        )
        project_client.beta.schedules.create_or_update.return_value = SimpleNamespace(
            schedule_id="maf-poc-daily-comprehensive",
            provisioning_status="succeeded",
        )

        with (
            patch(
                "scripts.foundry_eval.suites.comprehensive."
                "prepare_comprehensive_agent_evaluation",
                return_value=(dataset, evaluation, dataset_items),
            ),
            patch("scripts.foundry_eval.suites.comprehensive.save_json"),
            patch("builtins.print"),
        ):
            upsert_comprehensive_agent_schedule(
                project_client,
                openai_client,
                args,
                target,
            )

        schedule_arg = project_client.beta.schedules.create_or_update.call_args.kwargs[
            "schedule"
        ]
        eval_run = schedule_arg.as_dict()["task"]["evalRun"]
        self.assertEqual(
            eval_run["data_source"]["source"]["content"],
            [{"item": dataset_items[0]}],
        )

    def test_parse_raw_agent_response_reads_completed_sse(self) -> None:
        response = {
            "status": "completed",
            "output": [
                {
                    "content": [
                        {"type": "output_text", "text": "captured response"}
                    ]
                }
            ],
        }
        raw = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream\r\n\r\n"
            "event: response.completed\n"
            f"data: {json.dumps({'response': response})}\n\n"
        )

        self.assertEqual(parse_raw_agent_response(raw), "captured response")

    def test_parse_raw_agent_response_rejects_failed_agent(self) -> None:
        response = {
            "status": "failed",
            "error": {"code": "agent_error", "message": "boom"},
        }
        raw = (
            "HTTP/1.1 200 OK\n"
            "Content-Type: text/event-stream\n\n"
            "event: response.completed\n"
            f"data: {json.dumps({'response': response})}\n\n"
        )

        with self.assertRaisesRegex(RuntimeError, "agent_error"):
            parse_raw_agent_response(raw)

    def test_parse_raw_agent_response_rejects_truncated_stream(self) -> None:
        raw = (
            "HTTP/1.1 200 OK\n"
            "Content-Type: text/event-stream\n\n"
            "event: response.output_text.delta\n"
            'data: {"delta":"partial"}\n\n'
        )

        with self.assertRaisesRegex(RuntimeError, "response.completed"):
            parse_raw_agent_response(raw)

    def test_replay_uses_fresh_conversation_and_replaces_assistant_turns(self) -> None:
        item = {
            "case_id": "multi-turn",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reviewed first"},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "reviewed second"},
            ],
            "response": "reviewed second",
        }
        calls: list[tuple[str, bool]] = []

        def invoke(
            message: str,
            target: EvaluationTarget,
            environment_name: str,
            *,
            new_conversation: bool,
        ) -> str:
            self.assertEqual(target.agent_version, "7")
            self.assertEqual(environment_name, "prod")
            calls.append((message, new_conversation))
            return f"live {message}"

        replayed = replay_conversations(
            [item],
            EvaluationTarget("model", "agent", "7"),
            "prod",
            invoke,
        )

        self.assertEqual(calls, [("first", True), ("second", False)])
        self.assertEqual(replayed[0]["response"], "live second")
        self.assertEqual(replayed[0]["reviewed_response"], "reviewed second")
        self.assertEqual(
            [
                message["content"]
                for message in replayed[0]["messages"]
                if message["role"] == "assistant"
            ],
            [
                [{"type": "text", "text": "live first"}],
                [{"type": "text", "text": "live second"}],
            ],
        )

    def test_replay_dataset_version_pins_source_and_agent(self) -> None:
        version = replay_dataset_version(
            "2",
            "abcdef1234567890",
            "7/preview",
            "20260822T010203Z",
        )

        self.assertEqual(
            version,
            "2-agent-7-preview-abcdef123456-20260822T010203Z",
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
