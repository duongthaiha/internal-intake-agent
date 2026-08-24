# Contoso University SOP: IT Incident Response

> **Fictional example:** Contoso University, its systems, contacts, service
> levels, teams, and policy references in this document are fictional and are
> provided only as sample university process content.

| Document control | Value |
| --- | --- |
| Owner | Digital Services |
| Approver | Chief Information Officer |
| Version | 1.0 |
| Effective date | 1 September 2026 |
| Review cycle | Every six months |

## Purpose and scope

This SOP defines how Contoso University reports, classifies, contains, resolves,
communicates, and reviews unplanned IT service interruptions and information
security incidents. It applies to university-managed services, devices,
networks, applications, cloud platforms, integrations, and third-party
suppliers. Routine service requests and planned maintenance use separate
processes.

## Roles and responsibilities

- The **Service Desk** records reports, performs initial triage, and maintains
  the incident record.
- The **Incident Manager** coordinates response, decisions, communications, and
  recovery for major incidents.
- Technical **Resolver Groups** investigate and restore their services.
- The **Cyber Security Team** leads suspected compromise, malicious activity,
  data exfiltration, or credential exposure.
- The **Service Owner** accepts restoration and owns corrective actions.
- The **Communications Lead**, **Data Protection Office**, and **Business
  Continuity Team** join when their escalation criteria are met.

## Severity classification

- **Severity 1 - Critical:** widespread loss of a critical service, credible
  threat to life or safety, active major cyberattack, or severe institutional
  impact.
- **Severity 2 - High:** major degradation affecting a faculty or essential
  business process with no practical workaround.
- **Severity 3 - Medium:** limited impact with a workaround or non-critical
  service disruption.
- **Severity 4 - Low:** minor, localized impact with little urgency.

Severity is based on impact and urgency, not the seniority of the reporter.

## Procedure

1. **Report and record.** Users contact the fictional Contoso Service Portal or
   Service Desk. Staff must not include passwords, authentication tokens, or
   unnecessary sensitive data in the ticket. The Service Desk records symptoms,
   affected users and services, start time, business impact, and contact details.
2. **Triage and classify.** The Service Desk checks monitoring alerts, known
   errors, recent changes, and duplicate incidents, then assigns severity and a
   resolver group. Suspected security events are referred immediately to the
   Cyber Security Team.
3. **Mobilize the response.** For Severity 1 or 2, an Incident Manager opens a
   response channel, names technical leads, sets an update frequency, and
   records a timeline and decisions in the incident record.
4. **Contain impact.** Resolver Groups take proportionate steps such as
   isolating affected components, disabling a vulnerable integration, revoking
   exposed sessions, or switching to a tested continuity service. Evidence must
   be preserved for security or supplier investigation.
5. **Communicate.** The Communications Lead issues factual, accessible updates
   that state impact, available workarounds, next update time, and where to get
   help. Updates must not speculate, disclose exploitable detail, or identify
   affected individuals.
6. **Diagnose and restore.** Resolver Groups identify the failure, assess change
   risk, implement an authorized fix or rollback, and monitor service health.
   Emergency changes follow the fictional Emergency Change Procedure and are
   documented retrospectively.
7. **Validate recovery.** The Service Owner confirms critical functions, data
   integrity, integrations, monitoring, and user access. The Incident Manager
   closes active communications only after stable recovery is demonstrated.
8. **Review and improve.** Severity 1 and 2 incidents receive a blameless
   post-incident review covering root causes, contributing conditions,
   detection, response, communications, recovery, and assigned corrective
   actions.

## Escalation and exceptions

A suspected personal data breach is escalated immediately to the Data
Protection Office; responders must not decide independently whether external
notification is required. A safety impact is escalated to campus emergency
management. A critical teaching, examination, payroll, or research service
failure triggers the relevant business continuity plan. If evidence suggests
criminal activity, the Cyber Security Lead coordinates any law-enforcement
contact. Service restoration must not destroy logs or forensic evidence unless
an Incident Manager records the safety or operational necessity.

## Records and controls

The fictional Contoso Service Portal is the authoritative record for severity,
timeline, affected services, decisions, communications, changes, evidence
references, recovery checks, and corrective actions. Access to security
evidence is restricted. Incident records follow the fictional Digital Services
Retention Schedule. Digital Services reviews severity accuracy, response time,
repeat incidents, overdue actions, and lessons learned each month.

