"""Hybrid package backing scripts.evaluate_foundry.

Module boundaries:

- ``config``: constants, project/target settings, and environment resolution.
- ``datasets``: JSONL loading/validation, hashing, dataset registration, and
  dataset item-schema builders.
- ``evaluators``: custom evaluator definitions/registration, testing criteria,
  and region filtering.
- ``runtime``: serialization, polling, output analysis, result persistence,
  metadata updates, evaluation lookup/creation, run data sources, and
  schedule construction/status.
- ``replay``: hosted-agent invocation, response parsing, and conversation
  replay.
- ``suites``: one module per evaluation suite (``smoke``, ``tools``,
  ``comprehensive``) that composes the shared modules above.
- ``cli``: argument parsing, suite default resolution, and dispatch.

``scripts/evaluate_foundry.py`` re-exports the public symbols from these
modules to preserve backward compatibility for callers and tests.
"""
