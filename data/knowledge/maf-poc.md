# MAF POC knowledge

The MAF POC Intake Agent uses Microsoft Agent Framework with the llm model
`gpt-5.6-sol` deployment in a dedicated private Microsoft Foundry project.

For local development, conversation history uses the in-memory history provider
and retrieval uses documents from the `data/knowledge` directory.

For Azure deployments, conversation history uses private Azure Cosmos DB and
retrieval uses a private Azure AI Search index. The hosted agent reaches both
services through private endpoints in the Foundry BYO VNet and authenticates
with Microsoft Entra ID rather than application secrets. The Foundry public
endpoint is deny-by-default and permits only the configured client IPv4 `/32`.

DevUI is available at `http://127.0.0.1:8080` during local development. DevUI is
a development and debugging interface and is not intended for production use.
