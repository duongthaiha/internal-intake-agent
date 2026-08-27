# Hosted and prompt agent architecture

Both agent variants implement the same intake behavior and use the same model
deployment and approval-gated intake operations. The prompt agent delegates
runtime concerns to Microsoft Foundry. The hosted agent owns its Agent
Framework runtime, retrieval, conversation persistence, middleware, and local
platform-advisor capability.

```mermaid
flowchart LR
    User["Requester<br/>Foundry or Teams"]

    subgraph Foundry["Microsoft Foundry project"]
        Model@{ img: "images/azure/foundry-models.svg", label: "Model deployment", pos: "b", w: 48, h: 48, constraint: "on" }
        Prompt@{ img: "images/azure/foundry-agent-service.svg", label: "Prompt agent", pos: "b", w: 48, h: 48, constraint: "on" }
        Hosted@{ img: "images/azure/foundry-agent-service.svg", label: "Hosted agent", pos: "b", w: 48, h: 48, constraint: "on" }
        Toolbox@{ img: "images/azure/foundry-project.svg", label: "Foundry Toolbox", pos: "b", w: 48, h: 48, constraint: "on" }
        OAuth@{ img: "images/azure/foundry-project.svg", label: "OAuth project connection", pos: "b", w: 48, h: 48, constraint: "on" }
        IQ@{ img: "images/azure/foundry-iq.svg", label: "Foundry IQ knowledge base", pos: "b", w: 48, h: 48, constraint: "on" }
    end

    subgraph Data["Private data and observability services"]
        History@{ img: "images/azure/cosmos-db.svg", label: "Cosmos DB conversation history", pos: "b", w: 48, h: 48, constraint: "on" }
        Search@{ img: "images/azure/ai-search.svg", label: "Azure AI Search retrieval", pos: "b", w: 48, h: 48, constraint: "on" }
        Blob@{ img: "images/azure/storage-account.svg", label: "Knowledge storage", pos: "b", w: 48, h: 48, constraint: "on" }
        Monitor@{ img: "images/azure/application-insights.svg", label: "Application Insights", pos: "b", w: 48, h: 48, constraint: "on" }
    end

    subgraph Intake["Intake workload"]
        APIM@{ img: "images/azure/api-management.svg", label: "API Management MCP endpoint", pos: "b", w: 48, h: 48, constraint: "on" }
        API@{ img: "images/azure/container-apps.svg", label: "Intake API Container App", pos: "b", w: 48, h: 48, constraint: "on" }
        IntakeDb@{ img: "images/azure/cosmos-db.svg", label: "Intake Cosmos DB", pos: "b", w: 48, h: 48, constraint: "on" }
        IntakeIndex@{ img: "images/azure/ai-search.svg", label: "Intake search index", pos: "b", w: 48, h: 48, constraint: "on" }
    end

    User --> Prompt
    User --> Hosted
    Prompt --> Model
    Hosted --> Model

    Prompt --> OAuth
    OAuth --> APIM
    Hosted --> Toolbox
    Toolbox --> APIM

    Hosted --> History
    Prompt --> IQ
    Hosted --> IQ
    IQ --> Search
    Search --> Blob
    Prompt --> Monitor
    Hosted --> Monitor

    APIM --> API
    API --> IntakeDb
    IntakeDb -. "scheduled indexing" .-> IntakeIndex
```

## Component boundaries

| Area | Prompt agent | Hosted agent |
| --- | --- | --- |
| Instructions | Loaded into the versioned prompt-agent definition | Loaded by `build_agent()` |
| Runtime and state | Managed by Foundry | Agent Framework runtime with Cosmos or in-memory history |
| Intake tools | Direct MCP configuration through the Foundry OAuth connection | Foundry Toolbox with an allowlist and required approval |
| Retrieval | Foundry IQ knowledge base | Foundry IQ, Azure AI Search, in-memory knowledge, or disabled |
| Custom capability | Shared intake behavior only | Repository-bundled platform-advisor skill and deterministic local tool |
| Observability | Foundry-managed tracing with Application Insights | Foundry tracing plus custom middleware and Application Insights |

The APIM MCP endpoint projects the intake REST API's five operations. APIM and
the Container App both validate the requester token; the API then enforces
tenant and record ownership before using its dedicated Cosmos DB. Conversation
history and intake records are intentionally stored in separate Cosmos DB
accounts and containers.

Azure product icons are from the
[official Azure architecture icon collection](https://learn.microsoft.com/azure/architecture/icons/)
and are used without modification.
