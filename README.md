# Microsoft Agent Framework POC

A Python agent built with Microsoft Agent Framework and a private Azure
deployment of `gpt-5.6-sol`.

The agent uses the Agent Framework harness, which supplies the runtime
scaffolding for function invocation, per-model-call history persistence,
planning and todo tracking, plan/execute modes, tool approvals, web search when
supported by the model client, and OpenTelemetry integration.

Retrieval-augmented generation is configurable:

- `RAG_PROVIDER=memory` loads `.md` and `.txt` files from `data/knowledge`.
- `RAG_PROVIDER=azure_search` injects results from Azure AI Search.
- `RAG_PROVIDER=none` disables retrieval.

## Prerequisites

- Python 3.11 or later
- Azure CLI
- Azure Developer CLI (`azd`)
- Permission to create resources and role assignments in the target subscription
- Available `gpt-5.6-sol` `GlobalStandard` quota in `uksouth`

The agent uses `DefaultAzureCredential` for Foundry and Cosmos DB, so no API
keys are stored locally. The identity needs Cosmos DB data-plane permissions,
which the included Bicep assigns through the **Cosmos DB Built-in Data
Contributor** role. Cosmos local key authentication is disabled.

## Setup

```powershell
az login --tenant c214aaa8-7a43-441a-b501-f942c96f54a8
azd auth login --tenant-id c214aaa8-7a43-441a-b501-f942c96f54a8
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## BYO-VNet Azure deployment

The supported deployment uses the official Microsoft Foundry Standard Agent
network-secured template, adapted for this workload. It creates
`rg-maf-poc-byo-uk` with:

- A new Foundry account and project injected into a dedicated BYO VNet
- A delegated `/24` agent subnet and private-endpoint subnet
- Private Storage, Cosmos DB, Azure AI Search, ACR, and Azure Monitor ingestion
- Private endpoints and linked Private DNS zones
- A `gpt-5.6-sol` model deployment
- Optional Azure Bastion and private Windows Server administration VM

The agent subnet permits public outbound traffic. Foundry inbound access uses
two paths: a private endpoint inside the VNet and a public endpoint restricted
to one narrow deployer CIDR rule. The current corporate egress pool uses
`85.210.10.184/29`, which includes the supplied `85.210.10.186` address.
Cosmos DB, Search, Storage, and ACR remain private.

The current `maf-poc-byo` environment sets `AZURE_DEPLOY_ADMIN_ACCESS=false`
because this subscription has no deployable VM SKU in UK South. This does not
affect Foundry BYO network injection or hosted-agent operation.

Run preflight without provisioning or deleting resources:

```powershell
.\scripts\deploy_byo.ps1 -PreflightOnly -AllowedClientIp "<your-public-ipv4>"
```

The script can query an approved Microsoft or enterprise IP-echo endpoint passed
with `-IpDetectionEndpoint` or `PUBLIC_IP_ECHO_ENDPOINT`. If detection is not
configured, unavailable, or ambiguous, it stops and requires
`-AllowedClientIp`; it never creates an allow-all rule.

UK South currently requires capacity to be released from the former deployment.
The destructive switch is guarded so it can delete only
`rg-maf-poc-private-uk`:

```powershell
.\scripts\deploy_byo.ps1 `
  -AllowedClientIp "<your-public-ipv4>" `
  -DeleteOldResourceGroup
```

The workflow builds and previews the Bicep, removes the old stack, waits for
model quota, provisions the BYO-VNet stack, publishes the hosted agent, assigns
its Cosmos DB and Search roles, republishes with dependency checks enabled, and
runs a grounded invocation. The new Cosmos history container starts empty; the
hosted agent rebuilds the Search index from `data/knowledge`.

The one-time administration VM password is generated when omitted and stored in
the local azd environment without being printed. Supply a secure value instead:

```powershell
$password = Read-Host "Admin VM recovery password" -AsSecureString
.\scripts\deploy_byo.ps1 `
  -AllowedClientIp "<your-public-ipv4>" `
  -AdminVmPassword $password `
  -DeleteOldResourceGroup
```

Run control-plane and public-path validation separately:

```powershell
.\scripts\validate_byo_deployment.ps1 -EnvironmentName maf-poc-byo
```

From the administration VM reached through Azure Bastion, clone this repository,
install Azure CLI and `azd`, authenticate, then verify private DNS and endpoint
connectivity:

```powershell
.\scripts\validate_from_vnet.ps1 -EnvironmentName maf-poc-byo
```

Inspect the environment variables captured by the currently published hosted
agent version:

```powershell
$agent = azd ai agent show maf-poc-agent --environment maf-poc-byo --output json | ConvertFrom-Json
$agent.version
$agent.definition.environment_variables | Format-List
```

Hosted agent versions are immutable snapshots, so changing an environment
variable requires publishing a new version. Treat this output as sensitive
because it can contain values such as the Application Insights connection
string. Foundry platform variables injected only at runtime might not appear in
this configured-variable list.

The local `.env` can use:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<foundry-account>.services.ai.azure.com/api/projects/maf-poc-project
FOUNDRY_MODEL=gpt-5.6-sol
LOG_LEVEL=INFO
HISTORY_PROVIDER=memory
COSMOS_STARTUP_CHECK=false
RAG_PROVIDER=memory
RAG_AUTO_INDEX=false
RAG_DOCUMENTS_PATH=data/knowledge
RAG_TOP_K=3
AZURE_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com:443/
AZURE_COSMOS_DATABASE_NAME=agent-framework
AZURE_COSMOS_CONTAINER_NAME=chat-history
AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
AZURE_SEARCH_INDEX_NAME=maf-poc-knowledge
```

The Cosmos container uses `/session_id` as its partition key.

## Conversation history

For local development, `.env` defaults to transient in-memory history:

```dotenv
HISTORY_PROVIDER=memory
```

This preserves context during the current process only. It does not restore a
conversation after the CLI exits.

To use persistent Cosmos DB history locally, run the agent from a host with
private network connectivity to Cosmos DB and set:

```dotenv
HISTORY_PROVIDER=cosmos
```

The selected history provider is passed to `create_harness_agent`, so history
is persisted after every model call, including intermediate tool calls.

## Harness configuration

`create_harness_agent` composes the standard Agent Framework harness instead of
manually assembling its providers and middleware. The application adds:

- Harness instructions for methodical planning, todo tracking, and verification
- Agent instructions for concise, accurate responses
- The configured in-memory or Cosmos DB history provider
- Experimental file memory disabled because this agent does not use file tools
- Foundry response storage disabled because history is managed by the provider

Shell execution, shared file access, background agents, and looping are not
enabled because they require explicit application-specific security and
execution policies.

## RAG

### Local in-memory retrieval

The default local configuration loads `.md` and `.txt` files recursively:

```dotenv
RAG_PROVIDER=memory
RAG_DOCUMENTS_PATH=data/knowledge
RAG_TOP_K=3
```

Documents are chunked and ranked in memory for every process. This mode requires
no search service and is intended for local development and automated tests.
Retrieved content is treated as untrusted input and source names are included
for citations.

### Azure AI Search retrieval

For local testing against Azure AI Search, use a host with private connectivity,
copy the generated endpoint from `azd env get-values` into `.env`, and run:

```powershell
python -m scripts.index_documents
```

Then select Azure AI Search:

```dotenv
RAG_PROVIDER=azure_search
AZURE_SEARCH_ENDPOINT=https://<search-service>.search.windows.net
AZURE_SEARCH_INDEX_NAME=maf-poc-knowledge
RAG_TOP_K=3
```

The hosted agent creates or updates the index and upserts `data/knowledge`
during startup. Startup fails instead of silently running without RAG if Search
is unavailable. The same startup gate writes, reads, and deletes a Cosmos DB
sentinel item to verify network and RBAC access before serving requests.
Azure AI Search citations use the indexed document identifier, such as
`maf-poc-md-1`, which retains the source name in a Search-safe form.

The Azure identity running the agent needs **Search Index Data Reader**. An
identity that initializes the index also needs **Search Service Contributor**
and **Search Index Data Contributor**. The deployment script assigns all three
roles and Cosmos DB Built-in Data Contributor to the hosted agent identity.

## DevUI

[Agent Framework DevUI](https://learn.microsoft.com/agent-framework/integrations/by-component/ui/devui/)
provides a local web interface and OpenAI-compatible API for development and
debugging. It is a sample development tool and is not intended for production.

Start DevUI:

```powershell
python devui.py
```

The browser opens at `http://localhost:8080`. DevUI authentication is enabled;
the development token is printed in the terminal at startup.
Set `DEVUI_AUTH_TOKEN` in the shell when a stable local API token is needed.
Do not commit that token to `.env` or source control.

Optional arguments:

```powershell
python devui.py --port 8081 --no-open --reload
```

DevUI uses the same harness agent and `HISTORY_PROVIDER` setting as the CLI.
Keep `HISTORY_PROVIDER=memory` for local development. Cosmos mode requires the
DevUI host to have private network connectivity to the provisioned VNet.

Tracing is enabled by default. In a response's trace, expand
`rag.retrieve in_memory` or `rag.retrieve azure_ai_search` to see the retrieval
provider, result count, whether context was injected, and the local source names
or Azure AI Search index. RAG runs as a context provider before the model call,
so it appears as a trace span rather than a tool call. Use `--no-tracing` only
when trace collection is not wanted.

The private hosted deployment exports application spans to the
project-connected Application Insights resource. Hosted traces include
`rag.retrieve azure_ai_search` and `cosmos.history.save`; Cosmos load spans are
also emitted when `COSMOS_LOAD_MESSAGES=true`. Message content capture is
disabled through `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false`.
New spans can take several minutes to appear in the Foundry trace viewer.

With `--reload`, DevUI restarts when project files change. Open the displayed
URL manually; automatic browser opening is disabled in reload mode to avoid
opening a new tab after every restart.

The browser creates a conversation automatically. OpenAI-compatible API clients
must create a `/v1/conversations` resource and pass its ID with each
`/v1/responses` request because the harness approval middleware requires an
Agent Framework session.

## Logging

Set `LOG_LEVEL` to control Python logs from the application, Agent Framework,
Azure SDKs, and DevUI:

```dotenv
LOG_LEVEL=DEBUG
```

Supported levels are `CRITICAL`, `ERROR`, `WARNING`, `INFO`, and `DEBUG`.
The default is `INFO`. A command-line value overrides `.env`:

```powershell
python agent.py --log-level DEBUG
python devui.py --reload --no-open --log-level DEBUG
```

`DEBUG` also logs each RAG provider's retrieval result count. OpenTelemetry
response traces remain independently controlled by `--tracing` and
`--no-tracing`. Use `DEBUG` only for local troubleshooting because dependency
logs can include prompts, retrieved context, model responses, and request
metadata.

## Run

Start an interactive conversation:

```powershell
python agent.py
```

The CLI prints the generated session ID. With `HISTORY_PROVIDER=cosmos`, reuse
it later to restore the conversation:

```powershell
python agent.py --session-id "<session-id>"
```

Or send a one-shot prompt:

```powershell
python agent.py "Explain Microsoft Agent Framework in one sentence." `
  --session-id "<session-id>"
```

If Azure returns `403 Forbidden`, grant the signed-in identity a Foundry role
that permits project inference and the required Cosmos DB data-plane role,
then sign in again. Cosmos mode also requires private network connectivity to
the provisioned virtual network.

## Local evaluation

Agent Framework's `LocalEvaluator` runs deterministic checks in the local
process. The evaluated agent still calls the configured Foundry model for each
query; only the scoring checks avoid evaluator API calls.

The starter dataset in `evals/local_cases.jsonl` uses every local evaluator
that applies to this agent:

- Custom checks for non-empty and concise responses
- Per-case expected terms, including acceptable aliases
- Per-case source citations
- Agent Framework tool-call presence and argument matching when a case declares
  `expected_tool_calls`

The generic `keyword_check` and `tool_called_check` helpers are not added
separately because the per-case expected-term and tool-call checks cover the
same behavior while supporting different expectations for each case.

```powershell
python -m scripts.evaluate_local
```

The command prints each response and its per-check results, then exits with a
non-zero status if any case fails. Run each query multiple times to check
non-deterministic behavior:

```powershell
python -m scripts.evaluate_local --repetitions 3
```

Each JSONL row contains a query and local expectations:

```json
{"query":"Which model deployment does this POC use?","expected_output":{"terms":[["gpt-5.6-sol"]],"source":"maf-poc.md"}}
```

Each inner `terms` list contains acceptable aliases; every term group must
match. The `source` value must also appear in the response. Cases for agents
with function tools can additionally declare expected calls:

```json
{"query":"Check the weather in London.","expected_output":{"terms":[["London"]],"source":"weather"},"expected_tool_calls":[{"name":"get_weather","arguments":{"location":"London"}}]}
```

Tool-call evaluators pass as not applicable when a case has no
`expected_tool_calls`. RAG in this POC is a context provider rather than a
function tool, so the starter RAG cases do not declare tool calls.

### Foundry cloud evaluation

The deployed hosted agent has a Foundry smoke evaluation using the reviewed
cases in `evals/foundry_smoke.jsonl`. It invokes the deployed agent and scores
responses with these built-in cloud evaluators:

- `relevance`
- `task_adherence`
- `intent_resolution`
- `indirect_attack`

Load the deployed azd environment values and run:

```powershell
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
azd env get-values | ForEach-Object {
  if ($_ -match '^([^=]+)="(.*)"$') {
    [Environment]::SetEnvironmentVariable($matches[1], $matches[2], "Process")
  }
}
python -m scripts.evaluate_foundry
```

The script authenticates with the Azure CLI identity, uploads a versioned
Foundry dataset, creates an agent-target evaluation, waits for completion, and
saves per-item results under `.foundry/results/`.

## Foundry Agent Service configuration

The hosted entry point is `hosted_agent.py`. The `azure.yaml` service deploys
`maf-poc-agent` to the private project with:

- Cosmos DB conversation history
- Azure AI Search retrieval
- Microsoft Entra authentication through the hosted agent identity
- Foundry Responses protocol version `2.0.0`
- Idempotent Search indexing and Cosmos connectivity validation at startup

Foundry Responses owns history loading and session lifecycle. In hosted mode,
the Cosmos provider uses `COSMOS_LOAD_MESSAGES=false`, so it records inputs and
outputs without loading a second copy of conversation history into each model
request.

The deployment script first deploys with dependency startup checks disabled so
the hosted identity can be created. It assigns RBAC, enables
`DEPENDENCY_STARTUP_CHECKS`, and deploys again. A successful final startup
therefore proves the managed agent can reach and use both private dependencies.

Underlying commands remain available for troubleshooting and CI after the azd
environment has been initialized by `deploy_byo.ps1`:

```powershell
azd provision --environment maf-poc-byo
azd deploy --environment maf-poc-byo
.\scripts\assign_hosted_agent_roles.ps1 -EnvironmentName maf-poc-byo
.\scripts\validate_byo_deployment.ps1 -EnvironmentName maf-poc-byo
```

Hosted agent deployment is complete only after the final version starts with
dependency checks enabled and `validate_byo_deployment.ps1` confirms grounded
Search output, Cosmos write/read/delete access, private endpoint approvals, DNS
links, network injection, and the exact Foundry client IP rule.

To retire a failed BYO deployment safely, delete the project capability host
before the account capability host or account, purge the deleted Foundry
account, and wait for the agent subnet service association to clear before
reusing that subnet. Prefer a new subnet or VNet for retries after capability
host provisioning has started.
