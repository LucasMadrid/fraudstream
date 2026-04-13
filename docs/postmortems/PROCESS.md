# Post-Mortem Process

**Owner**: Platform Team | **Applies to**: All production incidents | **Review cycle**: Quarterly

## Principles

- **Blameless**: Incidents are systemic failures, not individual failures.
- **Fast**: Draft within 24 hours of incident resolution.
- **Actionable**: Every post-mortem must produce at least one tracked action item.
- **Public**: All post-mortems are visible to the entire engineering organization.

## Timeline

| Phase | Deadline | Owner |
|-------|----------|-------|
| Incident detected | T+0 | On-call |
| Incident resolved | T+varies | Incident Commander |
| Post-mortem draft | T+24h | Incident Commander |
| Post-mortem review | T+72h | Team lead + affected parties |
| Action items tracked | T+1 week | Owning team |
| Action items closed | T+30 days | Owning team |

## Severity Levels

| Severity | Definition | Post-Mortem Required? |
|----------|------------|-----------------------|
| SEV-1 | Full pipeline outage, zero fraud detection | Required within 24h |
| SEV-2 | Degraded detection (>10% decisions missed) | Required within 48h |
| SEV-3 | Single component failure, mitigated | Optional (recommended) |
| SEV-4 | Performance degradation, no detection impact | Not required |

## Steps

1. **Declare incident**: On-call declares in Slack `#incidents` with severity and impact
2. **Assign Incident Commander**: Senior engineer takes IC role
3. **Resolve incident**: IC coordinates resolution, tracks timeline in the post-mortem doc in real time
4. **Draft post-mortem**: IC completes `TEMPLATE.md` within 24h of resolution
5. **Review meeting**: 30-minute meeting with team; IC presents, all comment
6. **Track action items**: Each action item gets a GitHub issue with `post-mortem` label
7. **30-day review**: Confirm all action items closed; update post-mortem with status

## Template

Use `docs/postmortems/TEMPLATE.md` for all post-mortems. Store completed post-mortems in `docs/postmortems/YYYY-MM-DD-{title}.md`.
