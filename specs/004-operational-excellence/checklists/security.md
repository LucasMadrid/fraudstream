# Requirements Quality Checklist: Operational Excellence — Security & PII

**Purpose**: Validate that security, PII protection, secret management, and access control requirements in this spec are complete, unambiguous, and enforceable before implementation begins. Audience: PR reviewer.
**Created**: 2026-04-13
**Feature**: [spec.md](../spec.md) | [plan.md](../plan.md) | [contracts/management-api.md](../contracts/management-api.md)

---

## 🔴 Mandatory Gate Items

Items in this section MUST pass before the PR can be approved.

- [X] CHK001 — Is the PII scanning pattern for "full 16-digit PAN" in FR-005 specified precisely enough to implement without ambiguity? The spec says "full 16-digit PAN patterns" — does this mean a strict Luhn-valid 16-digit sequence, any 16-digit numeric sequence, or a pattern matching common card number formatting (spaces/dashes: `4111 1111 1111 1111`)? Without a regex or Luhn requirement, different implementers will produce scanners with wildly different false-positive and false-negative rates. [Clarity, Spec §FR-005, Ambiguity]

- [X] CHK002 — Are the PII scanning patterns for IP addresses in FR-005 precise enough to distinguish a true PII leak from a legitimate network log field? The spec says "full IPv4/IPv6 addresses outside /24 subnet notation" — is `/24` a literal network mask check, or does it mean "any address more specific than a /24 should be redacted"? A `10.0.0.1` address in a `client_ip` field is different from the same address in a `raw_http_headers` blob — does scope (field context) matter? [Clarity, Spec §FR-005, Ambiguity]

- [X] CHK003 — Does FR-005 define what "halt writes to the affected Iceberg table" means operationally? A write halt is a destructive operational action — does the spec define: which component issues the halt, how it propagates to the producer (Kafka producer backpressure? schema-level reject? Flink sink abort?), and what the recovery procedure is before writes resume? Without this, the halt mechanism is underspecified and could be interpreted as anything from a Prometheus alert to a force-stop of the Flink job. [Completeness, Spec §FR-005, Gap]

- [X] CHK004 — Does the spec define the security model for port 8090 (management API)? `contracts/management-api.md` states "No authentication in v1 (internal network only); mTLS deferred to cloud deployment" — but is this a documented requirement (an explicit, intentional v1 security decision) or an undocumented assumption? If it is intentional, is there a stated threat model that justifies no-auth on an endpoint that can demote/promote fraud detection rules? [Completeness, Spec §contracts/management-api.md, Security Risk]

---

## Secret Management & Config Exposure

- [X] CHK005 — Are requirements defined for how `ml_serving_url` (added to `ScoringConfig` in FR-025) is injected at runtime? The spec defines the field but not its source — is it an environment variable, a Kubernetes secret, an AWS Secrets Manager reference, or a plaintext config file? A URL containing embedded credentials (`http://user:pass@ml-serving/`) would be a credential leak if stored in YAML or logged. [Completeness, Spec §FR-025, Gap]

- [X] CHK006 — Is `redis_url` (FR-026, `ScoringConfig`) specified with a security requirement? Redis in production may require AUTH tokens or TLS (`rediss://`). The spec sets the default to `redis://localhost:6379/0` (no auth, no TLS) — is there a requirement that the cloud deployment value MUST use a different scheme, or is an insecure default acceptable in v1 with an explicit documented exception? [Completeness, Spec §FR-026, Gap]

- [X] CHK007 — Does FR-025 define requirements for how `ScoringConfig` fields are protected in the execution environment? If `ml_serving_url`, `redis_url`, and circuit breaker config are loaded from a plaintext YAML file committed to the repository, sensitive values would be visible in git history. Is there a requirement that secrets MUST come from environment variables or a secrets manager — not from the YAML config file? [Completeness, Spec §FR-025, §FR-026, Gap]

- [X] CHK008 — Does FR-002 (schema validation CI) define requirements for how Schema Registry credentials are handled in the CI environment? The CI job must authenticate to Confluent Schema Registry — are there requirements that these credentials are stored as GitHub Actions secrets (not in workflow YAML), rotated on a defined schedule, and scoped to read-only for schema validation? [Completeness, Spec §FR-002, Gap]

---

## PII Minimization & Audit Completeness

- [X] CHK009 — Does FR-005 specify a retention policy for `audit.pii_scans` records? The `PIIAuditRecord` entity definition says "append-only" but does not define how long audit records are retained. If PII is detected and the record captures `matched_pattern`, is the matched PII value itself written to the audit table (a PII-in-audit-log risk), or only the pattern name and position? [Completeness, Spec §FR-005, §Key Entities, Ambiguity]

- [X] CHK010 — Is there a requirement that `PIIAuditRecord.matched_pattern` stores only the pattern descriptor (e.g., `"PAN_16_DIGIT"`) and not the actual matched value? Storing the matched PAN in an audit record would itself be a PII leak. The spec defines the field as "matched pattern (if any)" — is "pattern" the regex/rule name, or the matched string? [Clarity, Spec §Key Entities — PIIAuditRecord, Ambiguity]

- [X] CHK011 — Does FR-006 (PR template) define what "PII audit passed" means as a checklist gate? The PR template must include "PII audit passed" — but since FR-005 is BLOCKED (TD-007), what does an implementer check against when FR-005 doesn't exist yet? Is "PII audit passed" gated on a manual review, a lint rule, or deferred until FR-005 ships? A PR template with an unresolvable checklist item will cause friction on every PR until the Iceberg sink spec is delivered. [Completeness, Spec §FR-006, §FR-005, Gap]

- [X] CHK012 — Does the spec define which pipeline stages are in scope for PII enforcement? Constitution Principle VII requires "PII masked at producer edge" — but FR-005 audits Iceberg tables (post-enrichment). Is there a requirement that PII masking happens at ingestion (before Kafka write), at enrichment (before Iceberg write), or both? If masking happens at ingestion, the Iceberg audit is a safety net; if only at ingestion and a bug bypasses masking, is the audit the first detection point? [Completeness, Spec §FR-005, Constitution Principle VII, Gap]

---

## Management API Security Scope

- [X] CHK013 — Does the spec define the trust boundary for the management API's demotion path? `contracts/management-api.md` specifies Alertmanager fires a webhook to `/rules/{rule_id}/demote` using the `rule_id` from `GroupLabels`. Is there a requirement that `rule_id` is validated against a known list of rules before the demotion is executed? An Alertmanager misconfiguration could pass an arbitrary `rule_id` — does the API spec define input validation requirements beyond "404 if not found"? [Completeness, Spec §contracts/management-api.md, §FR-022, Gap]

- [X] CHK014 — Is the absence of rate limiting on the management API demote/promote endpoints documented as an intentional v1 decision? If Alertmanager misfires (e.g., a flapping alert fires 100 times per minute), the management API would receive 100 demote calls for the same rule. The 409 idempotency response handles repeated calls gracefully — but is there a stated requirement for rate limiting or Alertmanager deduplication to prevent cascading state mutation? [Completeness, Spec §contracts/management-api.md, Gap]

- [X] CHK015 — Does the spec define audit logging requirements for rule mode changes made via the management API? A rule demotion from active to shadow changes which transactions get blocked — this is a security-relevant state change. Is there a requirement that every `/demote` and `/promote` call is logged with: caller identity (Alertmanager or analyst), timestamp, previous mode, new mode, and the triggering FP rate? `contracts/management-api.md` defines the response body but not the logging requirement. [Completeness, Spec §FR-022, §contracts/management-api.md, Constitution Principle VIII, Gap]

- [X] CHK016 — Is the Alertmanager → management API webhook URL constructed safely? `contracts/management-api.md` shows the Alertmanager config as `url: "http://scoring-engine:8090/rules/{{ .GroupLabels.rule_id }}/demote"`. Is there a requirement that `rule_id` in Alertmanager `GroupLabels` is validated to prevent path traversal or injection (e.g., `rule_id = "../circuit-breaker/reset"`)? The spec should define whether the rule ID format is constrained (e.g., alphanumeric only, max 64 chars) to make the URL construction safe. [Clarity, Spec §contracts/management-api.md, §FR-022, Security Risk]

---

## Network Isolation & Port Exposure

- [X] CHK017 — Does the spec define docker-compose port exposure requirements for the management API? `contracts/management-api.md` states port 8090 is NOT exposed to the host — but is this a stated requirement in the spec (enforceable) or an implementation note in the contract doc? If it's only in the contract, a developer could inadvertently expose port 8090 in docker-compose and create an externally reachable rule-demotion endpoint without violating any stated requirement. [Completeness, Spec §FR-014, §contracts/management-api.md, Gap]

- [X] CHK018 — Are network isolation requirements defined for the Redis service added in FR-026? The spec defines Redis for docker-compose (port 6379, named volume) but does not specify whether the Redis port should be exposed to the host. In a local dev environment with Redis accessible on `localhost:6379` without auth, any process on the developer's machine can read or write feature cache data. Is this acceptable, and if so, is it an explicitly documented exception? [Completeness, Spec §FR-026, Gap]

- [X] CHK019 — Does the spec define requirements for the Prometheus scrape endpoint security? If `ml_circuit_breaker_state`, `rule_shadow_triggers_total`, and other metrics are exposed on a Prometheus `/metrics` endpoint, is there a requirement that this endpoint is not publicly accessible in cloud deployment? The spec defines the metrics but does not specify endpoint access control. [Completeness, Spec §FR-014, §FR-013, Gap]

---

## Terraform & Infrastructure Security

- [X] CHK020 — Does FR-003 define security requirements for the Terraform state file? Terraform state can contain sensitive values (Redis AUTH token, Kafka API keys). Is there a requirement that state is stored in an encrypted backend (S3 with SSE, Terraform Cloud) with access control, not in a local `terraform.tfstate` file that could be committed? [Completeness, Spec §FR-003, Gap]

- [X] CHK021 — Are IAM/RBAC requirements defined for the three Terraform deployment targets (local, AWS, GCP) in FR-003? The spec defines what resources Terraform must manage (Kafka topics, Schema Registry, Redis, Iceberg) but not the minimum-privilege IAM role required for each target. Without a stated minimum-privilege requirement, implementations may use overly broad credentials (e.g., `AdministratorAccess`). [Completeness, Spec §FR-003, Gap]

- [X] CHK022 — Does FR-016 define whether Redis data at rest must be encrypted? The spec defines Redis Sentinel/cluster topology and TTL policy but does not state whether encryption-at-rest is required for the feature cache. Since feature vectors may contain derived signals from transaction data (velocity, geolocation), is there a classification decision documenting whether this data is PII-adjacent and subject to encryption requirements? [Completeness, Spec §FR-016, Gap]

---

## Dependency & Supply Chain Security

- [X] CHK023 — Does FR-001 (CI/CD pipeline) include a dependency security scanning stage? The 7-stage CI pipeline (lint → unit tests → schema validation → integration tests → latency bench → manual approval → Terraform plan) does not include `pip audit`, `safety`, or SBOM generation. Is this intentionally excluded from v1 scope, or a gap? [Completeness, Spec §FR-001, Gap]

- [X] CHK024 — Is there a stated requirement for `pybreaker` version pinning given that FR-014 depends on its circuit breaker semantics? The plan.md specifies `pybreaker 1.x` but the spec does not state a pinning requirement. If `pybreaker` releases a breaking change in 1.x behavior (e.g., changes to how failure counting works), CI would silently pick up the new behavior. Is there a requirement to pin the exact version or use a lockfile? [Completeness, Spec §FR-014, plan.md, Gap]
