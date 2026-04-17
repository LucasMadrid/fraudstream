# Post-Mortem: [Title]

**Date**: YYYY-MM-DD  
**Severity**: SEV-[1-4]  
**Duration**: [X hours Y minutes]  
**Author**: [Incident Commander]  
**Status**: Draft / In Review / Closed  

---

## Summary

[One-paragraph summary: what failed, what the impact was, how it was resolved.]

## Impact

| Metric | Value |
|--------|-------|
| Detection outage duration | [X minutes] |
| Transactions affected | [N transactions] |
| False negative rate during outage | [X%] |
| Customer-facing impact | [Yes/No — describe if yes] |

## Timeline

| Time (UTC) | Event |
|------------|-------|
| HH:MM | [Incident begins — describe trigger] |
| HH:MM | [First alert fires] |
| HH:MM | [On-call engaged] |
| HH:MM | [Root cause identified] |
| HH:MM | [Mitigation applied] |
| HH:MM | [Incident resolved] |

## Root Cause

[Describe the technical root cause. Be specific: which component, which code path, what invariant was violated.]

## Contributing Factors

- [Factor 1: e.g., missing alerting for X]
- [Factor 2: e.g., runbook didn't cover this scenario]
- [Factor 3: e.g., dependency on Y without circuit breaker]

## Detection

[How was the incident detected? Alert, customer report, on-call observation? How long after the incident started was it detected?]

## Resolution

[Step-by-step account of what was done to resolve the incident.]

## What Went Well

- [Thing 1 that helped]
- [Thing 2 that helped]

## What Went Poorly

- [Thing 1 that hurt]
- [Thing 2 that hurt]

## Action Items

| Action | Owner | GitHub Issue | Due Date | Status |
|--------|-------|--------------|----------|--------|
| [Specific action to prevent recurrence] | @username | #000 | YYYY-MM-DD | Open |

## Lessons Learned

[2-3 sentences on the most important lessons from this incident.]
