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

## Agent implementations

The repository keeps the two Foundry implementations separate while sharing
one intake behavior contract:

```text
agents/
  shared/   # canonical intake instructions
  hosted/   # Agent Framework code, DevUI, history, and RAG
  prompt/   # prompt-agent config and create/update workflow
```

The hosted agent retains Cosmos DB history and Azure AI Search retrieval. The
prompt agent initially uses Foundry-managed state with no tools or RAG. Both
load `agents/shared/instructions/intake_agent.md`; do not duplicate that prompt
inside either implementation.

## Intake request contract

[`schemas/intake-request.schema.json`](schemas/intake-request.schema.json)
defines the version 1.0.0 intake contract using JSON Schema Draft 2020-12. A
complete fictional submission is available at
[`examples/intake-request.example.json`](examples/intake-request.example.json).

The schema supports progressive intake. Every record must include:

- `title`
- `problemOpportunity`
- `proposedIdea`
- `expectedOutcome`
- `requester.name` and `requester.email`

Business context, users, value measures, AI and data considerations,
dependencies, delivery, risks, responsible AI, security and privacy, ownership,
and supporting links can be added as discovery progresses. A request type is
intentionally not part of the contract. Unknown properties are rejected to
surface misspelled or unsupported fields.

The standalone intake API validates this contract and stores its records
separately from agent conversation history. The agent does not yet call the API
or submit records automatically.

## Intake persistence API

`intake_api` is a FastAPI service with a versioned, self-contained OpenAPI
contract at
[`openapi/intake-api.openapi.json`](openapi/intake-api.openapi.json). Regenerate
the checked-in contract after changing routes or schemas:

```powershell
python -m scripts.export_intake_openapi
```

The service exposes these stable operations:

| Method and path | OpenAPI operation | Behavior |
| --- | --- | --- |
| `POST /v1/intake-requests` | `create_intake_request` | Create a mutable draft |
| `GET /v1/intake-requests/{request_id}` | `get_intake_request` | Read an authorized request |
| `GET /v1/intake-requests` | `list_intake_requests` | List the caller's requests, or the tenant for privileged callers |
| `PUT /v1/intake-requests/{request_id}` | `replace_intake_request` | Replace a draft using `If-Match` |
| `POST /v1/intake-requests/{request_id}/submit` | `submit_intake_request` | Submit a draft using `If-Match` |

Create and update bodies use the intake JSON Schema directly. Responses add the
request ID, status, schema version, and audit timestamps. Create, read, update,
and submit responses include an `ETag`; mutating an existing record requires
that value in `If-Match`. Missing and stale preconditions return `428` and
`412`, respectively. A read can send `If-None-Match` and receives `304` when its
cached ETag is still current. Submitted requests are immutable, and retrying a
submission after an unknown outcome returns the submitted representation.

Create accepts an optional `Idempotency-Key` header. The key is scoped to the
tenant and authenticated caller and remains reserved for the lifetime of the
intake record. Retrying the same validated payload with the same key returns the
original resource, `Location`, and `ETag`; using the key for a different payload
returns `409`. Keys must contain 1-255 visible ASCII characters, must not contain
sensitive information, and must not be reused. Clients that omit the header keep
the existing behavior where each POST creates a new draft.

This API uses `Idempotency-Key` for generic HTTP and APIM MCP client ergonomics.
The Microsoft Azure REST API Guidelines prescribe the paired
`Repeatability-Request-ID` and `Repeatability-First-Sent` protocol for formal
Azure data-plane APIs; supporting both protocols in v1 would create ambiguous
retry semantics, so that alternative is deferred to a future API version.

Errors use RFC 9457-style `application/problem+json` bodies and include a stable
`x-ms-error-code` response header. Authentication failures include
`WWW-Authenticate`, persistence throttling includes `Retry-After`, and the
OpenAPI contract documents these headers.

Every route requires a Microsoft Entra ID access token. Configure one app
registration for the API with:

- Delegated scope `Intake.ReadWrite` for requesters
- Application role `Intake.Read.All` for tenant-wide read access
- Application role `Intake.ReadWrite.All` for tenant-wide read/write access

The API validates the token signature, fixed tenant issuer, audience, lifetime,
scope, and roles. It derives tenant and creator IDs from token claims; clients
cannot select them in request bodies. Requesters can access only their own
records. Privileged roles remain tenant-scoped.

The deployed service uses its managed identity to access a dedicated Cosmos DB
account. The account disables local authentication and public networking. Its
`intake/intake-requests` container uses the hierarchical partition key
`/tenantId`, `/id`; API updates use Cosmos `_etag` optimistic concurrency. The
account is separate from the Foundry capability-host and chat-history Cosmos
account. Composite indexes cover owner/status lists ordered by update time. TTL
is intentionally disabled because the correct retention period is an
organisation policy decision; the `retentionRequirements` intake field records
context but does not delete data automatically.

To run the API locally, use a host with private connectivity to the deployed
intake Cosmos private endpoint, sign in with an identity that has the Cosmos DB
data role, populate the `INTAKE_*` settings shown in `.env.example`, and run:

```powershell
uvicorn intake_api.app:app --host 127.0.0.1 --port 8000
```

Liveness is available at `/health/live`; `/health/ready` also checks Cosmos
access. Neither endpoint returns configuration or intake data.

### API Management MCP projection

Azure API Management can expose selected operations of a managed REST API as
remote MCP tools, so this service does not implement a second MCP protocol
server. Import `openapi/intake-api.openapi.json`, select the five intake
operations, and use APIM's Streamable HTTP MCP endpoint. Configure APIM products,
authorization, quotas, and policies for callers. Configure APIM's managed
identity to request a token for `INTAKE_ENTRA_AUDIENCE` and assign only the
required intake application role.

REST-to-MCP projection currently exposes tools, not MCP resources or prompts,
and is not supported in APIM workspaces. Avoid APIM policies or global
diagnostics that buffer or log MCP response bodies. See [Expose REST API in API
Management as an MCP server](https://learn.microsoft.com/azure/api-management/export-rest-mcp-server)
and [MCP server support in API Management](https://learn.microsoft.com/azure/api-management/mcp-server-overview).

## Prerequisites

- Python 3.11 or later
- Azure CLI
- Azure Developer CLI (`azd`)
- Docker Desktop or another Docker engine for the intake API image
- Permission to create resources and role assignments in the target subscription
- Available `gpt-5.6-sol` `GlobalStandard` quota in `uksouth`
- A Microsoft Entra app registration for the intake API audience, scope, and roles

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
- Private endpoints for Storage, Cosmos DB, Azure AI Search, ACR, and Azure Monitor ingestion
- A Container Apps intake API and separate private Cosmos DB account
- Private endpoints and linked Private DNS zones
- A `gpt-5.6-sol` model deployment
- Optional Azure Bastion and private Windows Server administration VM

The agent subnet permits public outbound traffic. Foundry, both Cosmos DB
accounts, Azure AI Search, Storage, and ACR retain their private endpoints and
also enable public access through selected-network rules restricted to
`85.210.10.0/24`. ACL defaults remain deny, local/key authentication remains
disabled where already configured, and each template-created service carries
the `SecurityControl=Ignore` tag. Externally supplied existing resources are
referenced but not retagged or reconfigured.

The current `maf-poc-byo` environment sets `AZURE_DEPLOY_ADMIN_ACCESS=false`
because this subscription has no deployable VM SKU in UK South. This does not
affect Foundry BYO network injection or hosted-agent operation.

Run preflight without provisioning or deleting resources:

```powershell
.\scripts\deploy_byo.ps1 `
  -PreflightOnly `
  -IntakeEntraAudience "api://<intake-api-application-id>"
```

The script defaults `-AllowedClientIp` to `85.210.10.0/24`. An explicitly
approved public IPv4 address or `/24`-through-`/32` CIDR can override the
default. If the parameter is explicitly empty, the script can query an approved
Microsoft or enterprise IP-echo endpoint passed with `-IpDetectionEndpoint` or
`PUBLIC_IP_ECHO_ENDPOINT`; it never creates an allow-all rule.

UK South currently requires capacity to be released from the former deployment.
The destructive switch is guarded so it can delete only
`rg-maf-poc-private-uk`:

```powershell
.\scripts\deploy_byo.ps1 `
  -IntakeEntraAudience "api://<intake-api-application-id>" `
  -DeleteOldResourceGroup
```

The workflow builds and previews the Bicep, removes the old stack, waits for
model quota, provisions the BYO-VNet stack, publishes the hosted agent, assigns
its Cosmos DB and Search roles, republishes with dependency checks enabled, and
runs a grounded invocation. It also builds and deploys the intake API container.
The new Cosmos history and intake containers start empty; the hosted agent
rebuilds the Search index from `data/knowledge`.

The intake image is built locally because the ACR data plane is network
restricted. During deployment the workflow applies the same client CIDR to ACR, pushes the
image, and leaves the registry private endpoint enabled. Run the workflow from
that allowlisted range or from a host with private VNet connectivity; it never
enables unrestricted registry access.

The separate serverless Cosmos account, Container Apps environment, and running
replicas add Azure cost independently of the hosted agent. The default API scale
keeps one replica warm and permits five; lower `intakeApiMinReplicas` only after
accepting cold starts.

The one-time administration VM password is generated when omitted and stored in
the local azd environment without being printed. Supply a secure value instead:

```powershell
$password = Read-Host "Admin VM recovery password" -AsSecureString
.\scripts\deploy_byo.ps1 `
  -IntakeEntraAudience "api://<intake-api-application-id>" `
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
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-5.6-sol
AZURE_TENANT_ID=<tenant-guid>
AGENT_MAF_POC_AGENT_NAME=maf-poc-agent
AGENT_MAF_POC_AGENT_VERSION=<deployed-version>
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
INTAKE_COSMOS_ENDPOINT=https://<intake-cosmos-account>.documents.azure.com:443/
INTAKE_COSMOS_DATABASE_NAME=intake
INTAKE_COSMOS_CONTAINER_NAME=intake-requests
INTAKE_ENTRA_TENANT_ID=<tenant-guid>
INTAKE_ENTRA_AUDIENCE=api://<intake-api-application-id>
INTAKE_ENTRA_ISSUER=https://login.microsoftonline.com/<tenant-guid>/v2.0
INTAKE_DELEGATED_WRITE_SCOPE=Intake.ReadWrite
INTAKE_PRIVILEGED_READ_ROLE=Intake.Read.All
INTAKE_PRIVILEGED_WRITE_ROLE=Intake.ReadWrite.All
INTAKE_JWKS_CACHE_SECONDS=3600
INTAKE_API_MIN_REPLICAS=1
INTAKE_API_MAX_REPLICAS=5
AZURE_INTAKE_CONTAINER_APPS_SUBNET_ID=
AZURE_INTAKE_CONTAINER_APPS_SUBNET_PREFIX=192.168.4.0/23
```

`AZURE_AI_MODEL_DEPLOYMENT_NAME`, `AZURE_TENANT_ID`,
`AGENT_MAF_POC_AGENT_NAME`, and `AGENT_MAF_POC_AGENT_VERSION` are required only
for Foundry cloud evaluation. The agent name and version must identify an
existing hosted-agent version; there is no default version because evaluating a
stale deployment would give misleading results. These values are deployment
identifiers, not secrets. The Application Insights connection string is
sensitive and must remain in the azd environment or local process environment.

The intake settings are required only for the intake API. The endpoint,
database, container, tenant, audience, issuer, scope, and role names are
non-secret deployment configuration. `INTAKE_ENTRA_ISSUER` defaults to the
tenant-specific v2 issuer, role/scope names default to the values above, and
`INTAKE_JWKS_CACHE_SECONDS` defaults to `3600`. Do not use a client secret or
Cosmos key; local and deployed access use Azure identity.

`INTAKE_API_MIN_REPLICAS` and `INTAKE_API_MAX_REPLICAS` are non-sensitive
deployment-only scale limits and default to `1` and `5`.
`AZURE_INTAKE_CONTAINER_APPS_SUBNET_PREFIX` is the non-sensitive CIDR used only
when creating the dedicated subnet and defaults to `192.168.4.0/23`.
`AZURE_INTAKE_CONTAINER_APPS_SUBNET_ID` is an optional, non-sensitive existing
subnet resource ID; leave it empty for a new VNet and set it when existing VNet
subnets must not be changed.

`scripts/deploy_byo.ps1` requires `-IntakeEntraAudience` or the
`INTAKE_ENTRA_AUDIENCE` process environment variable and records the audience
and deployment tenant in the selected azd environment. When reusing a VNet that
must not be modified, set `AZURE_INTAKE_CONTAINER_APPS_SUBNET_ID` to an existing
dedicated subnet delegated to `Microsoft.App/environments`; otherwise the
template creates `192.168.4.0/23` by default.

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
Retrieved content is treated as trusted input and source names are included
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

DevUI loads the repository's `.env` file. Create it from the example:

```powershell
Copy-Item .env.example .env
```

At minimum, configure these values:

```dotenv
FOUNDRY_PROJECT_ENDPOINT=https://<foundry-account>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=<model-deployment-name>
HISTORY_PROVIDER=memory
RAG_PROVIDER=memory
RAG_DOCUMENTS_PATH=data/knowledge
```

`FOUNDRY_PROJECT_ENDPOINT` and `AZURE_AI_MODEL_DEPLOYMENT_NAME` are required,
non-secret deployment identifiers. `FOUNDRY_MODEL` can be used instead of
`AZURE_AI_MODEL_DEPLOYMENT_NAME`. The local provider values shown above require
no Cosmos DB or Azure AI Search configuration. Authenticate the
`DefaultAzureCredential` identity before starting DevUI:

```powershell
az login
```

Alternatively, load the required values from an existing azd environment for
the current PowerShell session:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = azd env get-value FOUNDRY_PROJECT_ENDPOINT --environment {azd-env-name}
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = azd env get-value AZURE_AI_MODEL_DEPLOYMENT_NAME --environment {azd-env-name}
$env:HISTORY_PROVIDER = "memory"
$env:RAG_PROVIDER = "memory"
```

Start DevUI from the hosted-agent package:

```powershell
$env:DEVUI_AUTH_TOKEN = "{personal token}"
python -m agents.hosted.devui --reload --log-level DEBUG 
```

The browser opens at `http://localhost:8080`. DevUI authentication is enabled;
the command generates and sets a token only for the current PowerShell session.
DevUI does not print the token. Use `$env:DEVUI_AUTH_TOKEN` as the bearer token
for the OpenAI-compatible API. Do not commit the token to `.env` or source
control.

Optional arguments:

```powershell
python -m agents.hosted.devui --port 8081 --no-open --reload
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
enabled through `ENABLE_SENSITIVE_DATA=true` and
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. This records prompts,
responses, tool arguments, and tool results in Application Insights; restrict
trace access and retention accordingly. New spans can take several minutes to
appear in the Foundry trace viewer.

Foundry calculates the **Estimated cost** value; the agent does not emit a
dollar-cost metric. The calculation requires model-call spans with
`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and
`gen_ai.request.model` or `gen_ai.response.model`. The VNet validation invokes
the hosted agent, waits for telemetry ingestion, and verifies those attributes:

```powershell
.\scripts\validate_from_vnet.ps1 -EnvironmentName maf-poc-byo
```

Run this from the admin VM or another environment with the required private
network connectivity and Application Insights read access. The Azure CLI
`application-insights` extension is also required. Re-run `azd provision` after
upgrading an existing environment so azd records the new
`APPLICATIONINSIGHTS_RESOURCE_ID` output, or pass
`-ApplicationInsightsResourceId` explicitly. Use
`-SkipTelemetryValidation` only when validating connectivity without querying
telemetry. If the attributes pass validation but Foundry still displays `$0`,
the selected model might not yet have reference pricing in the preview
dashboard, or the estimate might be below the tile's currency precision. Use
Azure Cost Management for billed cost and financial reconciliation.

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
python -m agents.hosted.agent --log-level DEBUG
python -m agents.hosted.devui --reload --no-open --log-level DEBUG
```

`DEBUG` also logs each RAG provider's retrieval result count and every
retrieved source and chunk. OpenTelemetry response traces remain independently
controlled by `--tracing` and `--no-tracing`. Use `DEBUG` only for local
troubleshooting because dependency logs can include prompts, retrieved context,
model responses, and request metadata.

## Run

Start an interactive conversation:

```powershell
python -m agents.hosted.agent
```

The CLI prints the generated session ID. With `HISTORY_PROVIDER=cosmos`, reuse
it later to restore the conversation:

```powershell
python -m agents.hosted.agent --session-id "<session-id>"
```

Or send a one-shot prompt:

```powershell
python -m agents.hosted.agent "Explain Microsoft Agent Framework in one sentence." `
  --session-id "<session-id>"
```

If Azure returns `403 Forbidden`, grant the signed-in identity a Foundry role
that permits project inference and the required Cosmos DB data-plane role,
then sign in again. Cosmos mode also requires private network connectivity to
the provisioned virtual network.

## Prompt agent

The prompt variant is defined by `agents/prompt/config.yaml` and uses the same
model deployment and project endpoint environment variables as the hosted
agent. `PROMPT_AGENT_NAME` defaults to `maf-poc-prompt-agent`.

Validate its local configuration without calling Foundry:

```powershell
python -m agents.prompt.sync --dry-run
```

Create the agent or create a new immutable version when its model,
instructions, description, or tools change:

```powershell
python -m agents.prompt.sync
```

The command uses `DefaultAzureCredential` and requires a project role that can
manage Foundry agents. It does not delete agents and does not silently fall back
to another project, model, or authentication method.

Shared behavior cases are stored in
`evals/shared/intake_behavior.jsonl`. Pass the prompt agent's immutable name and
version to the Foundry evaluation workflow:

```powershell
python -m scripts.evaluate_foundry `
  --dataset evals/shared/intake_behavior.jsonl `
  --dataset-name maf-poc-shared-intake `
  --dataset-version 1 `
  --agent-name $env:PROMPT_AGENT_NAME `
  --agent-version "<prompt-agent-version>" `
  --inline-data
```

The existing local and hosted smoke datasets assert retrieval and source
attribution and therefore remain hosted-agent checks.

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

The deployed hosted agent uses the reviewed regression cases in
`evals/foundry_smoke.jsonl`. The evaluation invokes the exact deployed agent
version and scores responses with these built-in cloud evaluators:

- `relevance`
- `task_adherence`
- `intent_resolution`
- `indirect_attack`

It also registers `maf_poc_expected_behavior`, a prompt-based evaluator that
scores each response against the row's reviewed `expected_behavior`. The custom
evaluator treats the query, response, and rubric as untrusted data.

The dataset version is immutable. The registration stores the local SHA-256 in
Foundry dataset tags. If local content changes, choose a new
`--dataset-version`; the script refuses to reuse a version whose content cannot
be verified.

Authenticate to the deployment tenant from the allowlisted `85.210.10.0/24`
or an approved private-network host, select the deployed azd environment, and
load its values:

```powershell
$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
az login --tenant "<tenant-guid>"
azd env select maf-poc-byo
azd env get-values --environment maf-poc-byo | ForEach-Object {
  $name, $value = $_ -split "=", 2
  if ($name -and $null -ne $value) {
    [Environment]::SetEnvironmentVariable($name, $value.Trim('"'), "Process")
  }
}
```

The signed-in operator needs **Foundry User** on the project and **Cognitive
Services OpenAI User** on the parent Foundry account. Native schedules execute
as the Foundry project managed identity; the Bicep deployment grants that
identity **Foundry User** at project scope and **Cognitive Services OpenAI
User** at account scope. Role propagation can take several minutes.

Run a one-off evaluation:

```powershell
python -m scripts.evaluate_foundry --inline-data
```

The default `smoke` suite authenticates with the Azure CLI identity, invokes the
exact hosted-agent version, waits for completion, and saves per-item results
under `.foundry/results/`. `--inline-data` embeds the reviewed rows in the
Foundry request, avoiding direct access to the private Storage data plane. A
VNet-connected runner can omit that flag to register an immutable dataset.

#### Comprehensive multi-turn evaluation

`evals/foundry_comprehensive_multi_turn.jsonl` contains reviewed, fictional
internal-intake conversations based on Foundry's OpenAI-style `messages` schema.
Each row also includes `agent_query`, a standalone prompt carrying all requester
turns, plus the flattened fields needed by turn-level evaluators:

```json
{"case_id":"missing-required-details","category":"clarification","messages":[{"role":"user","content":[{"type":"text","text":"Can you submit this incomplete idea?"}]},{"role":"assistant","content":[{"type":"text","text":"Not yet. The required details are missing."}]}],"query":"Submit an incomplete request.","response":"Not yet. The required details are missing.","ground_truth":"Decline to submit and identify missing required fields.","context":"Relevant reviewed source text.","expected_behavior":"Ask for missing fields and do not invent them.","retrieval_ground_truth":[{"document_id":"intake-schema","query_relevance_label":4}],"retrieved_documents":[{"document_id":"intake-schema","relevance_score":1.0}],"agent_query":"Treat the requester messages below as one standalone internal-intake conversation..."}
```

Run both comprehensive evaluations:

```powershell
python -m scripts.evaluate_foundry --suite comprehensive --inline-data
```

The command registers `maf-poc-comprehensive:2` and creates two direct dataset
evaluations. The turn-level run scores each reviewed final response; the
conversation-level run scores each complete `messages` interaction. They must
be separate because Foundry rejects a run that mixes evaluators with
incompatible evaluation levels.

The `comprehensive` suite evaluates reviewed transcripts and does **not** invoke
the deployed hosted agent. Use `comprehensive-replay` to test the current agent
with the same cases. When the source dataset changes, increment its immutable
version:

```powershell
python -m scripts.evaluate_foundry `
  --suite comprehensive `
  --dataset-version 2
```

`evals/foundry_comprehensive_single_turn.jsonl` provides the same reviewed
coverage as self-contained single-turn examples. Each `query` is one natural
end-user message with no embedded turn transcript, while `ground_truth` and
`response` contain the reviewed reference answer. This follows Foundry synthetic
Simple Q&A datasets, which use `query` and `ground_truth`, while retaining the
repository's behavioral and retrieval fields for custom and built-in evaluators.

| Run | Included evaluators |
| --- | --- |
| Turn | Coherence, Fluency, Similarity, F1, BLEU, GLEU, ROUGE, METEOR, Retrieval, Document Retrieval, Groundedness, Groundedness Pro, Relevance, Response Completeness, Hate and Unfairness, Sexual, Violence, Self-Harm, Protected Materials, Indirect Attack, Ungrounded Attributes, Task Adherence, Task Completion, Intent Resolution, Quality Grader, and `maf_poc_expected_behavior` |
| Conversation | Customer Satisfaction, Task Completion, Coherence, and Groundedness |

Tool Call Accuracy, Tool Selection, Tool Input Accuracy, Tool Output
Utilization, Tool Call Success, and Task Navigation Efficiency are excluded.
This agent has no user-defined function tools, and Microsoft documents limited
evaluator support for Azure AI Search, which this workload uses as a context
provider rather than a function tool. Prohibited Actions and Sensitive Data
Leakage are also excluded because their agent-target contracts require tool-call
artifacts. Code Vulnerability is not applicable to this non-code-generation
workload. Azure OpenAI graders and generated rubric/custom evaluators are
configurable extensions rather than fixed built-in signals; the existing
expected-behavior custom evaluator remains the domain-specific judge.

Several comprehensive evaluators and conversation-level evaluation are preview
features. UK South supports batch evaluation but does not support the risk and
safety evaluator service, Groundedness Pro, or Protected Materials. The runner
therefore reports and excludes `hate_unfairness`, `sexual`, `violence`,
`self_harm`, `indirect_attack`, `ungrounded_attributes`, `groundedness_pro`, and
`protected_material` from executable UK South runs. The full definitions remain
enabled in supported regions. Other unavailable evaluators fail the run rather
than being silently treated as passing. Each LLM judge and hosted safety
evaluation can incur model or evaluation-service cost. Token-overlap metrics
are included for complete built-in coverage but should be treated as secondary
signals for open-ended answers.

#### Relink an existing deployed environment

An azd environment is local state and is not committed. From a clean clone,
relink the existing resource group without provisioning or redeploying:

```powershell
$environment = "maf-poc-byo"
$subscription = "<subscription-guid>"
$tenant = "<tenant-guid>"
$resourceGroup = "<existing-resource-group>"
$location = "uksouth"
$agentName = "maf-poc-agent"

$env:AZURE_DEV_USER_AGENT = "microsoft_foundry_skill"
az login --tenant $tenant
az account set --subscription $subscription
azd env new $environment --subscription $subscription --location $location --no-prompt
azd env set "AZURE_TENANT_ID=$tenant" "AZURE_RESOURCE_GROUP=$resourceGroup" `
  --environment $environment --no-prompt

$deploymentName = az deployment group list --resource-group $resourceGroup `
  --query "[?properties.provisioningState=='Succeeded'] | sort_by(@, &properties.timestamp)[-1].name" `
  --output tsv
$outputs = az deployment group show --resource-group $resourceGroup `
  --name $deploymentName --query properties.outputs --output json | ConvertFrom-Json
$projectEndpoint = $outputs.foundrY_PROJECT_ENDPOINT.value
$modelDeployment = $outputs.azurE_AI_MODEL_DEPLOYMENT_NAME.value

$agent = az rest --method get --resource "https://ai.azure.com" `
  --url "$projectEndpoint/agents/$agentName?api-version=v1" | ConvertFrom-Json
$agentVersion = [string]$agent.versions.latest.version
$agentEndpoint = "$projectEndpoint/agents/$agentName/versions/$agentVersion"

azd env set "FOUNDRY_PROJECT_ENDPOINT=$projectEndpoint" `
  "AZURE_AI_PROJECT_ENDPOINT=$projectEndpoint" `
  "AZURE_AI_MODEL_DEPLOYMENT_NAME=$modelDeployment" `
  "AGENT_MAF_POC_AGENT_NAME=$agentName" `
  "AGENT_MAF_POC_AGENT_VERSION=$agentVersion" `
  "AGENT_MAF_POC_AGENT_ENDPOINT=$agentEndpoint" `
  --environment $environment --no-prompt

azd ai agent show --environment $environment --output json --no-prompt |
  ConvertFrom-Json | Select-Object id, status
```

`azd env refresh` is not used here because this deployment has secure input
parameters that are intentionally unavailable in a clean clone. Do not invent,
rotate, or copy those values merely to refresh non-secret outputs.

#### Exact live multi-turn replay

Replay every requester turn in each case against one fresh hosted-agent
conversation, then evaluate the generated transcripts at both turn and
conversation levels:

```powershell
azd env get-values --environment maf-poc-byo | ForEach-Object {
  $name, $value = $_ -split "=", 2
  if ($name -and $null -ne $value) {
    [Environment]::SetEnvironmentVariable($name, $value.Trim('"'), "Process")
  }
}

python -m scripts.evaluate_foundry `
  --suite comprehensive-replay `
  --inline-data `
  --environment maf-poc-byo
```

The first turn in each case uses a new hosted session and Foundry conversation;
follow-up turns reuse that conversation. Golden assistant messages are never
sent to the agent or used as fallbacks. Generated transcripts are cached under
`.foundry/datasets/`, registered with a version derived from the source SHA and
agent version, and scored by the same turn/conversation evaluations. Detailed
results and failure clusters are written under
`.foundry/results/<environment>/<evaluation-id>/`.

### Native daily Foundry evaluation

Foundry's **Recurring Configs** supports scheduled evaluations over a registered
golden dataset. This repository configures the native schedule directly through
`AIProjectClient.beta.schedules`; it does not depend on GitHub Actions, a VM
task, or a Foundry agent routine.

After deploying the RBAC change, create or update the daily schedule:

```powershell
python -m scripts.evaluate_foundry --action schedule
```

The stable `maf-poc-daily-regression` schedule runs every day at **09:00 UTC**.
Schedule upsert does not invoke the agent immediately, but each scheduled run
uses target-model and judge-model tokens and therefore incurs cost. The
configuration is a preview Foundry capability without an SLA.

Check provisioning status and the ten most recent runs:

```powershell
python -m scripts.evaluate_foundry --action status
```

Foundry schedule run history is also available under **Evaluation** >
**Recurring Configs** in the Foundry portal. Local schedule references are
cached under `.foundry/schedules/`; response payloads and evaluation outputs are
ignored by Git.

Hosted-agent versions are immutable, and the schedule intentionally captures an
exact version. After every agent deployment, reload the new azd values and run
the schedule upsert command again. A failed or missing
`AGENT_MAF_POC_AGENT_VERSION` stops setup rather than silently targeting an old
version.

Create a separate daily live-agent regression from the comprehensive dataset:

```powershell
python -m scripts.evaluate_foundry `
  --suite comprehensive-agent `
  --inline-data `
  --action schedule
```

This creates or updates `maf-poc-daily-comprehensive` at **09:00 UTC**. Each
`agent_query` is independently invocable, so native Recurring Configs can run
the cases without custom replay orchestration. The schedule evaluates generated
agent output with all applicable turn-level evaluators except Retrieval and
Document Retrieval, because the agent-target result does not expose the Azure AI
Search context provider's actual retrieved-document artifacts.

Check only the comprehensive schedule:

```powershell
python -m scripts.evaluate_foundry `
  --suite comprehensive-agent `
  --inline-data `
  --action status
```

Exact multi-turn behavior remains an on-demand replay because Foundry Recurring
Configs cannot execute custom turn-by-turn orchestration. Both workflows pin the
immutable hosted-agent version and must be refreshed after deployment.

To use a revised dataset, increment the immutable version:

```powershell
python -m scripts.evaluate_foundry `
  --suite comprehensive-agent `
  --action schedule `
  --inline-data `
  --dataset-version 3
```

Disable or delete the schedule through Foundry **Recurring Configs** after
reviewing the selected schedule ID. The repository intentionally provides no
automatic deletion path.

## Foundry Agent Service configuration

The hosted entry point is `agents/hosted/hosted_agent.py`. The `azure.yaml`
service deploys `maf-poc-agent` to the private project with:

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
Search output, Cosmos write/read/delete access, `SecurityControl=Ignore` tags,
private endpoint approvals, DNS links, network injection, and the exact
selected-network CIDR rules.

To retire a failed BYO deployment safely, delete the project capability host
before the account capability host or account, purge the deleted Foundry
account, and wait for the agent subnet service association to clear before
reusing that subnet. Prefer a new subnet or VNet for retries after capability
host provisioning has started.
