# Implementation Requirements Checklist: Remove Live Feast Call from Co-located Scoring Path (SD-023)

**Purpose**: Validate that deletion requirements, preservation constraints, and success criteria are complete, unambiguous, and independently reviewable — for use during PR review
**Created**: 2026-05-16
**Feature**: [spec.md](../spec.md)
**Audience**: PR reviewer

---

## Requirement Completeness

- [x] CHK001 Does the spec name every file that must be modified or deleted, so a reviewer can assess scope without running a grep scan? [Completeness, Spec §FR-002/FR-003 — FR-004 is general; `tests/integration/test_feature_serving.py` was only discovered via research, not from reading the spec]
- [x] CHK002 Are all symbols slated for deletion (class names, dict names, import names) explicitly enumerated in the functional requirements? [Completeness, Spec §FR-002]
- [x] CHK003 Is the call-site removal of `feature_store_fallback_total` in `job_extension.py` explicitly required alongside the symbol deletions, so a reviewer knows the import removal is in scope? [Completeness, Spec §FR-002]
- [x] CHK004 Does the spec explicitly state that the `enriched_stream` variable at the downstream eval call (line 185) requires no rewiring after deletion — i.e., is the "no new logic needed" guarantee documented as a requirement or only as an assumption? [Completeness, Spec §Assumptions — covered by "purely subtractive" assumption; line 185 detail belongs in plan.md]
- [x] CHK005 Are the two operations required on `tests/unit/scoring/test_job_extension_safety.py` — deleting `TestFeatureEnrichmentFallback` AND adding `TestLiveFeaturesReachEvaluator` — both explicitly stated as requirements in the spec? [Completeness, Spec §FR-003 — fixed: FR-003 now requires both the deletion and the addition]

---

## Requirement Clarity

- [x] CHK006 Is FR-004 ("No other test, import, or module-level symbol MAY reference the deleted names") specific enough to drive implementation without a research scan? Or does it implicitly require the implementer to discover `tests/integration/test_feature_serving.py` independently? [Clarity, Gap — fixed: FR-004 now names all three files explicitly]
- [x] CHK007 Is "no intermediate Feast lookup or zero-value substitution" in FR-001 precise enough for a reviewer to verify — or does it require knowing the internal behavior of `_FlinkFeatureEnrichmentFunction`? [Clarity, Spec §FR-001 — reviewer can verify by grepping for the symbol; internal behavior not required]
- [x] CHK008 Is the term "co-located scoring path" defined or referenced (e.g., to ADR-005) in the spec so a reviewer who hasn't read the ADR understands the scope boundary? [Clarity, Spec §Context — Context section grounds the term in ADR-005 explicitly]
- [x] CHK009 Is the "purely subtractive" constraint (no new logic to replace deleted code) stated as an explicit requirement or only implied by the Assumptions section? [Clarity, Spec §Assumptions — stated in Assumptions; FRs being purely deletions make it self-evident]

---

## Requirement Consistency

- [x] CHK010 Does FR-005 ("metrics.py MUST NOT be modified") explicitly distinguish between the counter *definition* in `metrics.py` (preserved) and the call *site* in `job_extension.py` (removed) — or is the distinction only derivable from SC-001's note and the Assumptions? [Consistency, Spec §FR-005 vs SC-001 — FR-002 names the import/call-site removal; FR-005 names the preservation; distinction is now explicit]
- [x] CHK011 Are the acceptance scenarios in US1 and US2 consistent with the functional requirements FR-001 through FR-005 — does every FR map to at least one acceptance scenario? [Consistency, Spec §User Scenarios vs §Requirements — all FRs map to US1/US2 scenarios or success criteria]
- [x] CHK012 Does the Context section's description of the correctness bug ("Feast timeout overwrites live feature values") align precisely with SC-002's testable outcome ("non-zero enriched features reach the rule evaluator with those same feature values")? [Consistency, Spec §Context vs SC-002 — aligned]

---

## Acceptance Criteria Quality (Success Criteria)

- [x] CHK013 Is SC-002 ("non-zero enriched features reach the rule evaluator with those same feature values") measurable from the spec alone — or does a reviewer need to read `quickstart.md` to understand what "same feature values" means, which values are used as test inputs, and how "no zero-value substitution" is asserted? [Measurability, Gap — US1 Independent Test in the spec names vel_count_5m=10 and device_known_fraud=True; measurable from spec alone]
- [x] CHK014 Does SC-001 unambiguously define its grep target — is it clear that `feature_store_fallback_total` is intentionally excluded from the grep (not an oversight), and is this visible to a reviewer reading only the spec? [Clarity, Spec §SC-001 — parenthetical in SC-001 makes the exclusion explicit]
- [x] CHK015 Is SC-003 ("all CI tests pass after deletion") sufficient as a success criterion, or does the spec need to specify the test runner invocation so that "all tests" can be independently verified without assuming CI configuration? [Measurability, Spec §SC-003 — spec-level abstraction is appropriate; tasks.md T012 specifies the command]
- [x] CHK016 Does SC-004 ("no import error is introduced") specify how this is verified — e.g., a full import scan, running the test suite, or a static analysis pass? [Measurability, Spec §SC-004 — spec-level abstraction appropriate; pytest passing is sufficient verification]

---

## Scenario Coverage

- [x] CHK017 Does the spec define requirements for the scenario where deletion is partial — e.g., only `_FeatureEnrichmentFunction` is removed but `_FlinkFeatureEnrichmentFunction` is left in place? Is partial deletion treated as an invalid state? [Coverage, Edge Case — partial deletion = FRs not satisfied; MUST language in FR-002 is sufficient]
- [x] CHK018 Does US2's acceptance scenario 2 ("all remaining tests pass and coverage does not drop below pre-change baseline") define what "coverage baseline" means and how it is measured? [Measurability, Spec §US2 Acceptance Scenario 2 — fixed: baseline now defined as pytest --cov result on main before branch begins]
- [x] CHK019 Are requirements defined for the scenario where a future engineer attempts to re-add the Feast call — is there a spec-level requirement (e.g., a comment, ADR cross-reference, or CI rule) that prevents regression, beyond the deletion itself? [Coverage, Spec §US2 — ADR-005 + Constitution Principle XI are the architectural constraints; out of spec scope]

---

## Dependencies & Assumptions

- [x] CHK020 Is the assumption that "EnrichedTransaction always carries populated feature values by the time it reaches `wire_rule_evaluator`" validated by a cross-reference to the upstream enrichment operator's contract — or is it only asserted? [Assumption, Spec §Assumptions — ADR-005 cited; sufficient for this spec]
- [x] CHK021 Does the spec document a dependency on ADR-005 being in force — i.e., if ADR-005 were reversed, this deletion would be a bug? Is that constraint visible to a reviewer? [Dependency, Spec §Context — Context section explicitly ties the change to ADR-005]
- [x] CHK022 Is the assumption "no branch or PR in-flight re-introduces these classes before this spec ships" verifiable at review time — and if not, should the spec require a branch scan as part of acceptance? [Assumption, Spec §Assumptions — noted as assumption; branch scan is a process concern, not a spec requirement]

---

## Notes

- Items marked `[Gap]` indicate a missing requirement that may need to be added to the spec before the PR is merged
- Items marked `[Clarity]` indicate an existing requirement that may need rewording for an independent reviewer
- CHK006 and CHK013 are the highest-priority items: FR-004's generality caused `test_feature_serving.py` to be missed until the research phase, and SC-002's test inputs are only documented outside the spec
- Check items off as completed: `[x]`
