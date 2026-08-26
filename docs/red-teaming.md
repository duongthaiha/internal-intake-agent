# AI red teaming

This workload has two red-teaming paths with different result surfaces.

## Local scanner

`python -m scripts.red_team_local` runs the Azure AI Evaluation SDK's
`RedTeam` orchestration in the local Python process. Attack generation and
safety scoring still use a Microsoft Foundry project, which must be in a
[supported AI red-teaming region][regions]. The target callback invokes an
exact immutable hosted or prompt agent version in the selected azd environment.

The smoke profile uses one objective for each of Violence, Hate and Unfairness,
Sexual, and Self-Harm. It sends direct baseline attacks and Base64 attacks.
Every attack uses a fresh Foundry conversation. This keeps the initial run
small while covering all four core content risks.

The runner writes JSON scorecards under:

```text
.foundry/results/<azd-environment>/red-team-local/<timestamped-scan>/evaluation_results.json
```

The scorecard contains overall, per-risk, per-complexity, and per-technique
Attack Success Rate (ASR), plus row-level attack results. These files are
ignored by Git because they can contain generated prompts and agent responses.
The timestamped directory also contains the SDK's intermediate PyRIT artifacts
and logs. Do not publish any of them without reviewing their content.

The local SDK is a preview capability. It can incur attack-generation,
target-agent, and safety-evaluation costs. It supports single-turn text
scenarios, so it does not validate approval continuation or side-effecting MCP
operations. The CLI exposes only single-turn attack strategies; multi-turn,
crescendo, and indirect-context attacks require a stateful target adapter and
are intentionally excluded.

## Foundry cloud scanner

The cloud API creates a native red-team evaluation group and run through
`AIProjectClient.get_openai_client().evals`. Agentic prohibited-action scans can
also create a reviewed taxonomy, use multi-turn attacks, run asynchronously,
and expose output items in Foundry.

Cloud agent targets must exist in the same supported-region Foundry project as
the red-team run. The repository's current project and both deployed agent
variants are in UK South, which supports batch evaluation but is not currently
listed for AI red teaming. A native cloud run against these agents therefore
requires separately approved supported-region deployments. This repository
does not provision those replicas.

The local JSON scorecard cannot be imported as a native red-team run in the new
Foundry portal. Microsoft explicitly documents the local RedTeam workflow as
incompatible with the new portal and SDK. Use the JSON for local reporting, or
use the cloud API after deploying the targets in a supported region.

The runner passes `skip_upload=True`, so its detailed local scorecard is not
published to the classic-compatible tracking surface either.

## Authentication and networking

- Use `DefaultAzureCredential`; no API-key fallback is provided.
- The operator needs **Foundry User** on the supported-region scanner project.
- The selected azd environment must be reachable so `azd ai agent invoke` can
  call the exact hosted and prompt agent versions.
- Private or IP-restricted projects require the runner to have the appropriate
  network path. Do not enable unrestricted public access as a workaround.
- Keep tool approval enabled. The smoke scan must not approve or execute
  side-effecting intake operations.

## References

- [Run AI Red Teaming Agent locally][local]
- [Run AI Red Teaming Agent in the cloud][cloud]
- [Evaluation regions, limits, and virtual-network support][regions]

[local]: https://learn.microsoft.com/azure/foundry/how-to/develop/run-scans-ai-red-teaming-agent
[cloud]: https://learn.microsoft.com/azure/foundry/how-to/develop/run-ai-red-teaming-cloud
[regions]: https://learn.microsoft.com/azure/foundry-classic/concepts/evaluation-regions-limits-virtual-network#supported-regions-for-ai-red-teaming
