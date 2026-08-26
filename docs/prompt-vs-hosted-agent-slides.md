---
marp: true
theme: default
paginate: true
size: 16:9
title: Prompt Agent vs Hosted Agent
description: Microsoft Foundry comparison grounded in the internal intake agent implementation
style: |
  section {
    font-size: 27px;
    padding: 48px 56px;
  }
  h1 {
    color: #0f6cbd;
  }
  h2 {
    color: #115ea3;
  }
  table {
    font-size: 20px;
  }
  th {
    background: #e9f2fb;
  }
  code {
    font-size: 0.82em;
  }
---

<!-- _class: lead -->

# Prompt Agent vs Hosted Agent

## Choosing the right Microsoft Foundry runtime

**Case study:** internal intake agent  
**Audience:** developers and architects

*Microsoft Foundry spans a spectrum from declarative configuration to full
application code.*

---

# Same behavior, different runtime ownership

```text
                         Shared contract
              instructions + schema + evaluations
                              |
                +-------------+-------------+
                |                           |
        Prompt agent                   Hosted agent
     Foundry-managed logic          Repository-owned logic
        + direct MCP                  + Foundry Toolbox
```

- Both variants load
  `agents/shared/instructions/intake_agent.md`.
- Both support the same five intake operations and require approval before tool
  execution.
- Both use a Foundry model deployment and Microsoft Entra-based access.
- The decision is therefore **not about intake behavior**; it is about how much
  runtime logic and operational control the team owns.

*Repository evidence: `agents/shared/`, `agents/prompt/config.yaml`,
`agents/hosted/agent.py`*

---

# Foundry platform comparison

| Dimension | Prompt agent | Hosted agent |
| --- | --- | --- |
| Authoring | Portal, SDK, or REST configuration | Python/C# framework or custom code |
| Runtime code | None to maintain | Team owns agent application code |
| Hosting | Fully managed | Foundry-managed container compute |
| Tools | Attached to the agent definition | Usually consumed through a Toolbox MCP endpoint |
| Custom orchestration | Limited to supported configuration | Full control over orchestration and middleware |
| Protocols | Managed agent endpoint | Responses, Invocations, WebSocket, Activity, A2A |
| Scaling | Automatic | Automatic per session/request |
| Cost shape | Inference + tool usage | Inference + tools + container compute |

> **Rule of thumb:** start with a prompt agent when configuration is enough;
> move to hosted when application code is a requirement.

*Source: [Agents in Microsoft Foundry](https://learn.microsoft.com/azure/foundry/agents/overview)
and [What are hosted agents?](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)*

---

# How this repository implements each option

| Prompt agent | Hosted agent |
| --- | --- |
| `PromptAgentDefinition` | Microsoft Agent Framework harness |
| Model + shared instructions + MCP configuration | Python 3.13 Responses host |
| Five allowlisted tools; approval `always` | Toolbox allowlist; approval always required |
| Foundry-managed conversations and runtime | Cosmos or in-memory history |
| SDK synchronization creates versions as needed | Foundry IQ, Azure AI Search, memory, or no RAG |
| No repository RAG provider | Custom telemetry, middleware, and startup checks |

**Primary files**

| Prompt | Hosted |
| --- | --- |
| `agents/prompt/config.yaml` | `agents/hosted/agent.py` |
| `agents/prompt/sync.py` | `agents/hosted/hosted_agent.py`, `azure.yaml` |

---

# Engineering trade-offs in this workload

| Concern | Prompt agent impact | Hosted agent impact |
| --- | --- | --- |
| Delivery speed | Smaller definition and deployment surface | More code, dependencies, and runtime configuration |
| Retrieval | No RAG configured in this implementation | Multiple provider choices with source attribution |
| History | Foundry-managed state | Explicit Cosmos/in-memory provider control |
| Observability | Platform-managed tracing | Platform tracing plus custom middleware and spans |
| Security boundary | Fewer custom runtime components | More flexibility and more code to secure |
| Testing | Definition/configuration tests | Unit tests across providers, middleware, startup, and hosting |
| Operations | Lower runtime ownership | Versioned container resources and dependency health checks |

> Hosted capability is valuable only when the workload needs it; otherwise it
> becomes avoidable engineering and operational surface area.

---

# Selection guidance for the intake agent

## Choose the prompt variant when

- Shared instructions plus the five MCP operations meet the requirement.
- Fast iteration and low runtime ownership matter more than customization.
- Foundry-managed state, tools, identity, and observability are sufficient.

## Choose the hosted variant when

- Grounding through Foundry IQ or Azure AI Search is required.
- Cosmos history, custom middleware, telemetry, or startup checks are required.
- The agent needs custom orchestration, providers, protocols, or resource sizing.

> **Recommendation:** keep the shared behavior contract, use the prompt agent as
> the simpler baseline, and use the hosted agent where the repository's custom
> RAG, history, and operational controls are explicit requirements.

---

# Sources and implementation references

## Microsoft documentation

- [Agents in Microsoft Foundry](https://learn.microsoft.com/azure/foundry/agents/overview)
- [What are hosted agents?](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Foundry Agent Runtime components](https://learn.microsoft.com/azure/foundry/agents/concepts/runtime-components)

## Repository

- `README.md`
- `agents/shared/instructions/intake_agent.md`
- `agents/prompt/config.yaml`
- `agents/prompt/sync.py`
- `agents/hosted/agent.py`
- `agents/hosted/hosted_agent.py`
- `agents/hosted/history.py`
- `agents/hosted/rag.py`
- `azure.yaml`
- `docs/intake-agent-functional-requirements.md`

*No relative quality or latency claim is included because the scored evaluation
artifacts referenced by `.foundry/agent-metadata.yaml` are not present in this
worktree.*
