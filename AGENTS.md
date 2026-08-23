# Agent development guidance

This project was built with the microsoft-foundry skill. Before working on or
answering questions about Foundry agents, read the microsoft-foundry skill
first.

The repository contains two implementations of the same intake behavior:

- `agents/hosted/` is the code-first hosted agent.
- `agents/prompt/` is the repository-managed prompt agent.
- `agents/shared/` is the canonical shared instruction source.

Keep runtime-specific code inside its implementation folder. Do not duplicate
the shared intake instructions.
