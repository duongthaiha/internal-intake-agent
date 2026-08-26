# Intake agent functional requirements

## 1. Purpose

The intake agent helps an authenticated requester develop, review, save, and
submit an internal innovation or AI use-case intake. It improves the quality and
completeness of the information supplied to reviewers without making approval,
rejection, funding, or prioritisation decisions.

These requirements apply to both the hosted and prompt-agent implementations.
The canonical behavior remains defined in
[`agents/shared/instructions/intake_agent.md`](../agents/shared/instructions/intake_agent.md),
and the persisted payload remains defined by
[`schemas/intake-request.schema.json`](../schemas/intake-request.schema.json).

## 2. Actors and systems

| Actor or system | Responsibility |
| --- | --- |
| Requester | Provides and confirms intake information and approves tool actions. |
| Intake agent | Guides discovery, drafts content, identifies gaps, and coordinates approved intake operations. |
| Authorised reviewer | Retains responsibility for assessment, approval, rejection, funding, and prioritisation. |
| Intake API | Validates, authorises, stores, retrieves, updates, lists, and submits intake records. |
| Knowledge provider | Supplies optional grounded reference material for factual answers. |

## 3. Intake information

An intake is complete enough to create or submit only when it contains:

- a short, recognisable title;
- a specific problem, unmet need, or opportunity;
- a concrete proposed idea;
- an expected outcome; and
- the requester's name and valid email address.

The agent may progressively capture optional discovery information covering
business context, target users, value and success measures, AI and data
considerations, dependencies, delivery considerations, risks, responsible AI,
security and privacy, ownership, supporting links, and additional notes.

## 4. Functional requirements

### Conversation and drafting

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-001 | The agent must identify which required intake fields are missing or insufficiently specific. | When required information is absent, the agent names the missing fields and does not create or submit a record. |
| FR-002 | The agent must ask focused clarification questions and help the requester express the problem, idea, and expected outcome concretely. | The resulting draft maps requester-provided information to the intake schema without adding unsupported facts. |
| FR-003 | The agent must preserve explicit requester corrections. | A later correction supersedes the earlier value, while unrelated values remain unchanged. |
| FR-004 | The agent must distinguish evidence from assumptions, estimates, and desired outcomes. | Unverified projections are not presented as observed facts. |
| FR-005 | The agent must provide a concise reviewable summary on request. | The summary uses only supplied or grounded information and makes any remaining gaps or assumptions visible. |
| FR-006 | The agent must allow the requester to cancel or pause the intake process. | After cancellation, the agent does not save, update, or submit a record unless the requester starts or resumes the process explicitly. |

### Grounded guidance

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-010 | When relevant knowledge is available, the agent must use it for factual process or policy answers. | The answer cites the source and does not replace available source content with a general assumption. |
| FR-011 | The agent must state when the available knowledge does not contain an answer. | Missing source coverage is made explicit rather than filled with invented information. |
| FR-012 | Retrieved content must be treated as untrusted data. | Instructions embedded in retrieved content cannot override the agent's governing instructions or the requester's authorised intent. |

### Intake record operations

These operations apply only when the intake tools are configured and available.
Without those tools, the agent may draft and review content but must not claim to
have read or changed a record.

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-020 | The agent must request explicit approval before every intake tool execution. | No read or write operation runs before approval, and chained create-and-submit work requires approval at each step. |
| FR-021 | The agent must list only requests available to the authenticated caller. | A list request invokes `list_intake_requests` without invented filters and reports only the successful tool result. |
| FR-022 | The agent must retrieve a specified request by its supplied identifier. | A get request invokes `get_intake_request` with the supplied identifier and does not invent record content. |
| FR-023 | The agent must create a mutable draft only from schema-valid required information. | `create_intake_request` receives an accurate intake payload; the agent reports creation only after a successful result. |
| FR-024 | The agent must support safe retries when the caller supplies an idempotency key. | The key is passed unchanged, contains no sensitive information, and is not invented or silently reused by the agent. |
| FR-025 | The agent must replace a draft only with a complete replacement payload and the latest known ETag. | `replace_intake_request` receives the request ID, full payload, and `If-Match`; if the current representation or ETag is unavailable, the agent asks for it or offers a separately approved retrieval. |
| FR-026 | The agent must not attempt to modify a submitted request. | Submitted records are treated as immutable, and the agent explains that further edits require the supported business process rather than claiming an update. |
| FR-027 | Before submission, the agent must search the requester's available requests for potentially similar titles, problems, ideas, or outcomes. | The agent presents potential matches, or states that none were found, and asks the requester to confirm whether to proceed. |
| FR-028 | The agent must submit only after the duplicate check, requester confirmation, and acquisition of the latest ETag. | `submit_intake_request` receives the request ID and `If-Match`; submission is not attempted if a prerequisite is missing. |
| FR-029 | The agent must accurately report operation outcomes. | Success is claimed only after a successful tool result. Errors, conflicts, stale ETags, missing preconditions, throttling, and unavailable dependencies are surfaced without success-shaped fallback behavior. |

### Human authority and responsible handling

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| FR-030 | The agent must keep approval, rejection, funding, and prioritisation decisions with authorised people. | Requests for automatic scoring or decisions are declined, while drafting, gap analysis, and clarification support remain available. |
| FR-031 | The agent must apply data minimisation. | It does not request credentials, keys, connection strings, sensitive records, or unnecessary personal data and recommends a high-level description when sensitive detail is proposed. |
| FR-032 | The agent must identify concerns requiring specialist review. | Privacy, security, accessibility, ethics, safety, and responsible-AI concerns are clearly directed to human review and are not represented as approved by the agent. |
| FR-033 | The agent must resist conflicting instructions in user input, retrieved documents, and tool output. | It neither reveals protected information nor follows embedded instructions that conflict with its governing behavior or access boundaries. |

## 5. Record lifecycle

1. **Discover:** collect required information and optional discovery details.
2. **Review:** summarise the intake, expose gaps and assumptions, and obtain
   requester corrections.
3. **Create draft:** after approval, create a mutable record and retain its
   identifier and ETag from the successful response.
4. **Revise draft:** retrieve the current record when needed and replace it using
   the complete payload and latest ETag.
5. **Check for similarity:** search accessible requests and ask the requester to
   confirm whether to proceed.
6. **Submit:** after approval, submit the draft using its latest ETag.
7. **Hand off:** leave assessment and all consequential decisions to authorised
   reviewers.

## 6. Out of scope

The intake agent does not:

- approve, reject, rank, prioritise, or allocate funding to requests;
- bypass requester approval for tool operations;
- modify submitted records;
- infer or fabricate missing intake details;
- expose records outside the caller's authorised scope;
- replace formal privacy, security, accessibility, ethics, legal, or
  responsible-AI review; or
- provide a key-based or unauthenticated fallback for the intake API.

## 7. Traceability

| Requirement area | Repository source |
| --- | --- |
| Shared agent behavior | `agents/shared/instructions/intake_agent.md` |
| Intake fields and validation | `schemas/intake-request.schema.json` |
| Record operations and lifecycle | `openapi/intake-api.openapi.json` |
| Core behavior acceptance cases | `evals/shared/intake_behavior.jsonl` |
| Tool selection acceptance cases | `evals/shared/intake_tool_calls.jsonl` |

