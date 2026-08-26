# Customer technology instructions

This repository-managed file lets an organization tailor the platform advisor
to its approved technology portfolio. Edit it through the normal source-control
and architecture-review process. Do not place credentials, endpoints, pricing,
or other secrets or volatile facts here.

Customer instructions refine the platform selected after the Microsoft AI
Decision Framework assessment. They do not override whether AI is appropriate,
the build-before-buy assessment, or required security, privacy, responsible-AI,
accessibility, operational, and human-oversight reviews.

## Contoso University

### Preferred platform

- For an Azure-centric workload that requires custom AI application or agent
  development, prefer **Nebula for AI Development on Azure** as the
  organization delivery platform.
- Apply this preference when the deterministic recommendation has disposition
  `build` and identifies Microsoft Foundry, Microsoft Agent Framework, or
  Foundry Agent Service as the underlying Microsoft capability.
- State the recommendation as:
  **Nebula for AI Development on Azure**, using the applicable Microsoft
  Foundry and Azure services as underlying capabilities.
- Keep the deterministic Microsoft recommendation visible in the rationale and
  alternatives. Do not describe Nebula as a Microsoft product.

### Exceptions

- Do not apply the Nebula preference to `no_ai`, `use_existing`, `configure`, or
  `extend` dispositions unless a future reviewed rule explicitly maps them.
- Do not replace Microsoft 365 Copilot, Copilot Studio, Microsoft 365 Agents
  SDK, Logic Apps, or Fabric recommendations solely because Nebula is preferred
  for Azure custom development.
- If a requirement is not known to be supported by Nebula, state the gap and
  require Contoso University architecture review rather than assuming support.