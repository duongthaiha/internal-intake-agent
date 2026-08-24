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

## Azure network and authentication changes

- Keep Azure data services deny-by-default and preserve their private endpoints
  and private DNS integration.
- When explicitly requested and permitted by Azure Policy, public access may be
  enabled with narrow IP CIDR allowlists. Never enable unrestricted public access.
- Prefer Microsoft Entra ID and managed identity. Local or shared-key
  authentication may be enabled when explicitly requested, but never commit keys
  or other credentials to source control.
