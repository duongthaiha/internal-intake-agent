# Hosted Agent Telemetry Fix

## Problem

Microsoft Foundry Operate displayed prompt-agent activity but did not display
the hosted `maf-poc-agent`, even though parts of its telemetry reached
Application Insights.

The investigation found two separate issues:

1. The hosted server emitted dependencies and traces but no request/server
   span for `POST /responses`.
2. Hosted spans did not use the Foundry classification envelope used by the
   prompt agent. The hosting telemetry processors also replaced
   `gen_ai.agent.id` with the managed-identity principal ID.

## Fix

The hosted agent now:

- Creates a correlated OpenTelemetry server span for `POST /responses`.
- Excludes readiness and health probes from request telemetry.
- Preserves incoming W3C trace context for downstream correlation.
- Stores the agent ID in `<name>:<version>` format, for example
  `maf-poc-agent:25`.
- Classifies top-level `invoke_agent` spans with:
  - `gen_ai.provider.name=microsoft.foundry`
  - `microsoft.foundry=true`
  - `span_type=agent`
  - `gen_ai.azure_ai_project.id`
  - `microsoft.foundry.project.id`
- Preserves the original Agent Framework provider under
  `microsoft.agent_framework.provider.name`.

The identity normalization runs after the Agent Server enrichment processor
and before telemetry exporters, preventing the managed-identity principal ID
from overwriting the Foundry agent ID.

## Changed Files

- `agents/hosted/request_telemetry.py`
  - Request-span middleware, context propagation, identity normalization, and
    Foundry classification.
- `agents/hosted/hosted_agent.py`
  - Registers the custom observability configuration and request middleware.
- `agents/hosted/agent.py`
  - Assigns a stable Foundry-compatible agent identity.
- `tests/test_hosted_agent.py`
  - Covers correlation, identity, classification, health-probe exclusion,
    error responses, and exceptions.

## Verification

Hosted agent version **25** is active.

Application Insights resource `appi-tracing-yyqk` contains both the request and
top-level dependency for trace:

`182a836d42ee2dcb1c9c874a427b687e`

Both records contain:

| Attribute | Value |
|---|---|
| `gen_ai.agent.id` | `maf-poc-agent:25` |
| `gen_ai.agent.name` | `maf-poc-agent` |
| `gen_ai.agent.version` | `25` |
| `gen_ai.operation.name` | `invoke_agent` |
| `gen_ai.provider.name` | `microsoft.foundry` |
| `microsoft.foundry` | `true` |
| `span_type` | `agent` |
| Foundry project IDs | Present |

The focused hosted-agent test suite passes with 16 tests.

## Prompt-Agent Comparison

The hosted and prompt agents now share the core Operate classification fields.
Some expected runtime-specific differences remain:

| Field | Prompt agent | Hosted agent |
|---|---|---|
| Span name | Includes version | Does not include version |
| Application role | `responsesapi` | `maf-poc-agent` |
| Channel | `agent_service` | Not set |
| Response ID on top-level span | Present | Not set |
| Blueprint ID | Prompt-agent blueprint | Hosted-agent blueprint |

These differences do not change the shared Foundry agent identity and project
classification.
