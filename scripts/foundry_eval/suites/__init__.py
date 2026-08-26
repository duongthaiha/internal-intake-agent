"""Suite implementations for the Foundry evaluation CLI.

Each module owns the orchestration unique to one dataset/evaluator
combination and depends only on the shared ``foundry_eval`` modules
(``config``, ``datasets``, ``evaluators``, ``runtime``, ``replay``), never on
another suite module.
"""
