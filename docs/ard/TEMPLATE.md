# ARD: <system name>

**Status:** draft | in review | approved with conditions | approved
**Authors:**
**Reviewers:** (the architecture review board, security, platform)
**Date:**
**Related:** PRD, RFCs, ADRs

An Architecture Review Document presents a whole system for review
before it goes live, or before a major change. Some organisations call
it an Architecture Requirements Document. It is not a design doc (how to
build one feature) and not an ADR (one decision). It is the system as a
reviewer must understand it to approve it. Link to the evidence rather
than restating it.

## 1. Context and scope

What the system does, for whom, and what it deliberately does not do. A
context diagram: users, and the external systems it talks to.

## 2. Quality attributes

The non-functional requirements, each with a target, how it is
measured, and the evidence today.

| Attribute | Target | Measured by | Evidence today |
|---|---|---|---|

## 3. Architecture

The containers or components, and the main request paths. The
technology choices, each with its ADR.

## 4. Data

What is stored, how it is classified, where it flows (third parties
included), how long it is kept, and how it is backed up and restored:
the recovery point (RPO) and recovery time (RTO).

## 5. Security

Identity and authorization; secrets; network exposure; the threat
model's summary (attack → control → test).

## 6. AI components (if any)

The model and the provider; what data reaches it; how quality and
safety are measured; guardrails; what users see when it fails.

## 7. Operations

Deploy and rollback; monitoring and alerting; capacity; failure modes
and single points of failure; who is on call.

## 8. Compliance

Licences of dependencies and images; data protection; audit needs.

## 9. Risks

| Risk | Likelihood | Impact | Mitigation | Owner |
|---|---|---|---|---|

## 10. Decisions and deviations

The ADRs, and any deviation from organisation standards, with why.

## 11. Open issues

## 12. Review outcome

Approved / approved with conditions / rejected. The conditions, each
with an owner and a date.
