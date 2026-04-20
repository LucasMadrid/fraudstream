# Feature Specification: Feature Serving Contract

**Feature Branch**: `007-feature-serving-contract`
**Created**: 2026-04-19
**Status**: Draft
**Input**: User description: "This specification defines the feature serving layer for FraudStream — the read-path counterpart to the Feast materialization write path delivered in Feature 006 — covering the contract by which the scoring engine retrieves velocity, geolocation, and device fingerprint features from the Redis online store within the 2ms p99 SLO required by Principle XI, including the 3ms client timeout, zero-value fallback behaviour, cold-start miss handling, and the staleness alerting mechanism that closes the loop between write and read."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Scoring Engine Reads Features Within Budget (Priority: P1)

A fraud decision is triggered the moment a transaction arrives. The scoring engine must retrieve all precomputed features for the account — velocity windows, geolocation context, device history — within 2ms so that the end-to-end 100ms latency budget is not breached. The engine submits the account identifier, receives a complete feature vector, and proceeds to score. No additional computation is required for features that were already enriched upstream.

**Why this priority**: Without a compliant read path, every fraud decision either violates the latency SLO or operates on missing features — both outcomes degrade fraud detection accuracy and breach the system's constitutional guarantee.

**Independent Test**: A scoring engine request cycle can be exercised against a seeded online store; the test passes if p99 retrieval latency for a populated feature vector is below 2ms and the complete feature set is returned.

**Acceptance Scenarios**:

1. **Given** the online store contains a feature vector for account A, **When** the scoring engine requests features for account A, **Then** the full vector is returned and the lookup completes in under 2ms at p99.
2. **Given** a peak load of 2× expected transaction volume, **When** the scoring engine issues concurrent feature retrievals, **Then** p99 latency remains below 2ms and no requests time out under normal store availability.

---

### User Story 2 — Graceful Degradation on Store Unavailability or Timeout (Priority: P1)

If the feature store is unavailable, too slow, or returns no data within 3ms, the scoring engine must not stall, retry indefinitely, or fail the transaction. Instead, it immediately falls back to a zero-valued feature vector, logs the event, and continues scoring. The transaction receives a score based on the ML model's neutral baseline and whatever deterministic rules can be evaluated against the raw transaction fields. The fallback must be silent to the transaction — no error response is issued to the caller.

**Why this priority**: A feature store outage must never become a fraud scoring outage. The fallback path is the constitutional guarantee that the hot path remains available even when the online store is not.

**Independent Test**: With the online store made unavailable, submitting a transaction to the scoring engine results in a completed fraud decision with zero-valued features; the `feature_store_fallback_total` counter increments; no error is propagated to the API caller.

**Acceptance Scenarios**:

1. **Given** the online store is completely unreachable, **When** the scoring engine requests features, **Then** the engine falls back to zero-valued features within 3ms, increments the fallback counter, and issues a valid fraud decision.
2. **Given** the online store responds in exactly 3ms (at the timeout boundary), **When** the scoring engine makes a retrieval request, **Then** the request times out, zero-valued features are used, and the fallback counter increments.
3. **Given** the online store recovers after a period of unavailability, **When** the scoring engine makes subsequent requests, **Then** the engine resumes reading live features automatically without restart.

---

### User Story 3 — Cold-Start and Cache Miss Handling (Priority: P2)

When the pipeline first starts or recovers from a job restart, the online store may be empty or partially stale. Accounts that have not yet had their features materialized have no entry in the store. The scoring engine must treat a missing entry as zero-valued features — not as an error — and log a miss event for each occurrence. The rule engine continues to evaluate deterministic rules against the raw transaction fields, providing partial fraud coverage even before feature materialization catches up.

**Why this priority**: Pipeline restarts are routine operational events. If cold-start triggers errors or scoring failures, operators face unnecessary alerts and the fraud detection gap widens during recovery.

**Independent Test**: With the online store empty, submitting a transaction through the scoring engine produces a valid decision; `feature_store_miss` events appear in the logs; no error or scoring failure is raised.

**Acceptance Scenarios**:

1. **Given** the online store contains no entry for account A, **When** the scoring engine requests features for account A, **Then** zero-valued features are returned, a `feature_store_miss` event is logged, and the scoring engine produces a valid decision.
2. **Given** the pipeline has just restarted with an empty store, **When** the first 100 transactions are scored, **Then** all produce valid decisions, all generate `feature_store_miss` logs, and no latency spikes or errors occur.
3. **Given** features are subsequently materialized for account A, **When** the next transaction for account A arrives, **Then** the scoring engine retrieves the live features without any miss event.

---

### User Story 4 — Staleness Alerting Closes the Write-Read Loop (Priority: P2)

The materialization pipeline (Feature 006) is responsible for keeping the online store current. If the pipeline stalls or falls behind, feature vectors grow stale — velocity windows no longer reflect recent activity, and fraud signals weaken. An alerting mechanism must detect when the online store has not been updated within an acceptable window and surface the staleness as an operational alert, allowing engineers to intervene before fraud detection accuracy degrades.

**Why this priority**: Stale features are more dangerous than missing ones because the scoring engine cannot distinguish them from fresh data. The alert is the only mechanism that closes the observability loop between the write path (Feature 006) and the read path (this feature).

**Independent Test**: Stopping the materialization pipeline while transactions continue flowing; after the configured staleness threshold elapses, an alert fires; the alert clears when materialization resumes and the store is updated.

**Acceptance Scenarios**:

1. **Given** no feature materialization has occurred for longer than the defined staleness threshold, **When** the staleness monitor runs, **Then** a staleness alert fires and is visible in the operational monitoring system.
2. **Given** a staleness alert is active, **When** materialization resumes and the online store is updated, **Then** the alert clears automatically without manual intervention.
3. **Given** the pipeline is running normally with sub-threshold materialization lag, **When** the staleness monitor runs, **Then** no alert fires.

---

### Edge Cases

- What happens when the feature store returns a partial vector (some fields present, others missing)?
- What is the behaviour when the online store is temporarily overloaded but still reachable (high latency, not a full timeout)?
- How does the fallback counter behave across scoring engine restarts — does it reset or persist?
- What is the staleness threshold default, and how is it changed without a deployment?
- How does the system differentiate between a known-new account (legitimately no features) and an account whose features were evicted from the store (data loss)? — **Accepted limitation**: the system does not differentiate; both produce the same `feature_store_miss` event and zero-value vector.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The scoring engine MUST retrieve the complete feature vector for a given account from the online store on every transaction, using the account identifier as the sole lookup key.
- **FR-002**: Feature retrieval MUST complete within 2ms at p99 under expected peak load; any retrieval exceeding this threshold constitutes a budget breach and MUST be observable via metrics.
- **FR-003**: The scoring engine MUST enforce a 3ms client-side timeout on every online store read; a read that does not complete within 3ms MUST be cancelled immediately and treated as a fallback trigger.
- **FR-004**: On timeout or store unavailability, the scoring engine MUST substitute zero-valued features for all feature fields; it MUST NOT use stale values from any local cache or prior retrieval.
- **FR-004a**: On a partial feature response (one or more feature fields are `None` while others are populated), the scoring engine MUST treat the full vector as zero-valued; partial merging of present and absent fields is not permitted.
- **FR-005**: The scoring engine MUST increment a `feature_store_fallback_total` counter on every zero-value fallback event; this counter MUST be exposed as a metric visible to the operational monitoring system.
- **FR-006**: The log record for a fallback event MUST distinguish between timeout fallback and unavailability fallback.
- **FR-007**: On a cache miss (account has no entry in the online store), the scoring engine MUST treat the result as zero-valued features and log a `feature_store_miss` event containing the account identifier, transaction identifier, and transaction timestamp.
- **FR-008**: A `feature_store_miss` event MUST NOT cause a scoring error or halt; the scoring engine MUST produce a valid fraud decision using zero-valued features.
- **FR-009**: The online store backend MUST support point lookups by account identifier; lookups MUST return the full feature vector in a single round-trip without requiring the caller to issue multiple requests.
- **FR-010**: A staleness monitoring mechanism MUST track the elapsed time since the last successful materialization write to the online store.
- **FR-011**: When the online store has not been updated within the configured staleness threshold, the monitoring mechanism MUST emit an alert to the operational monitoring system.
- **FR-012**: The staleness alert MUST resolve automatically when materialization resumes and updates the online store within the threshold window, without requiring manual intervention.
- **FR-013**: The scoring engine MUST remain fully available for fraud scoring for the entire duration of any online store outage; feature store unavailability MUST NOT propagate as a scoring failure to upstream callers.
- **FR-014**: Feature retrieval latency (p50, p95, p99) MUST be exposed as a metric, recorded per lookup, and visible in the operational monitoring system.

### Key Entities

- **Feature Vector**: The complete set of precomputed attributes for an account — velocity windows (1-minute, 5-minute, 1-hour, 24-hour counts and amounts), geolocation context (country, city, network class, confidence score), and device fingerprint attributes (device first-seen timestamp, lifetime transaction count, known-fraud flag). The schema is defined by the enrichment pipeline (Feature 002/006) and is not extended by this feature.
- **Account**: The entity whose features are retrieved; identified by a stable account identifier that serves as the lookup key for all online store reads.
- **Fallback Event**: An observable event (log record + metric increment) recording that the scoring engine substituted zero-valued features for a given account, along with the reason (timeout vs. store unavailability) and the associated transaction context.
- **Miss Event**: An observable event recording that the online store contained no entry for the requested account at lookup time; distinct from a fallback event in that the store was reachable but held no data.
- **Staleness Alert**: An operational signal emitted when the elapsed time since the last materialization write exceeds the configured threshold; resolves automatically within 60 seconds after the condition clears.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Online feature retrieval completes in under 2ms at p99 when measured against a store seeded with feature vectors at expected peak volume.
- **SC-002**: Under a simulated complete store outage, the scoring engine produces a valid fraud decision for 100% of transactions with zero propagated errors to upstream callers.
- **SC-003**: The `feature_store_fallback_total` counter increments on every timeout or unavailability event and is visible in the operational monitoring dashboard within 30 seconds of the event.
- **SC-004**: Under a cold-start simulation (empty store, 100 consecutive transactions for distinct accounts), 100% of transactions produce valid decisions and 100% generate `feature_store_miss` log events with no scoring errors.
- **SC-005**: When materialization is halted for longer than the staleness threshold, a staleness alert fires within 60 seconds; the alert clears within 60 seconds after materialization resumes and the gauge drops below the threshold.
- **SC-006**: End-to-end transaction scoring latency remains within the 100ms p99 budget even when the feature retrieval path exhausts the full 3ms client timeout.
- **SC-007**: After a simulated store outage ends, the scoring engine resumes reading live features on the next transaction without a process restart and without manual operator intervention.

## Assumptions

- The online store is seeded by the Feast materialization pipeline delivered in Feature 006; this feature specifies only the read path contract and does not redefine the write path.
- The feature vector schema is the one produced by the enrichment pipeline (velocity windows at 1m/5m/1h/24h, geolocation fields, device fingerprint fields); no new features are added.
- The staleness threshold is fixed at 30 seconds as mandated by Constitution Principle XI (`feature_materialization_lag_ms > 30,000ms`); runtime configurability is out of scope for this feature.
- Zero-valued features produce a neutral (low-fraud-score) ML model output; operating on zero features is safer than blocking or erroring — the rule engine still evaluates deterministic rules on raw transaction fields.
- The fallback counter resets to zero on scoring engine process restart; cross-restart persistence is out of scope.
- Partial vector responses (some feature fields present, others absent) are treated as full zero-value fallbacks for all missing fields; partial merging is out of scope.
- The staleness monitoring interval defaults to 60 seconds; the configurable surface is the `for:` field in `infra/prometheus/alerts/feature_serving.yml`, which can be edited without a code or job deployment.
- The scoring engine connects to the online store via a persistent connection or connection pool; connection configuration is an implementation concern not specified here.
- In local development, the online feature store and the hot store share a single Redis instance with separate key namespaces; in production they are separate instances — this feature's read path contract is identical in both environments.
