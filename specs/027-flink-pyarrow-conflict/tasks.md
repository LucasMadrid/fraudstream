---

description: "Task list for SD-027 — document & contain apache-flink ↔ analytics pyarrow conflict (#31)"
---

# Tasks: Document & Contain `apache-flink` ↔ `analytics` pyarrow Conflict (#31)

**Input**: Design documents from `/specs/027-flink-pyarrow-conflict/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: NO new tests are authored. The spec did not request them. Verification is documentation-readability + preservation of existing CI green status (the existing pytest + ruff are run as polish gates to prove no regression).

**Organization**: One phase per user story. The three user stories from the spec map to three documentation artifacts (ADR-015 + README for US1; SECURITY.md for US2; uv.lock header for US3). Phases are tightly scoped and independently testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

This is an existing Python project (documentation-only feature). All paths are relative to repo root `/Users/lucasmadridbbva/Desktop/repos/streaming/fraudstream/`.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the documentation surfaces and styles to mirror. No project initialization needed.

- [X] T001 Verify clean working tree on branch `027-flink-pyarrow-conflict` (run `git status` — only `specs/027-flink-pyarrow-conflict/` artifacts should be in the staged/unstaged set; pre-existing untracked drift such as `storage/feature_store/online_store.db` and other spec drafts is acceptable)
- [X] T002 [P] Inventory ADR style as reference: read `docs/adr/ADR-009-COMPOSITE-ALERT-SINK.md` and `docs/adr/ADR-014-SCORING-CONFIG-NARROWING.md` to confirm the section pattern (problem narrative → decision → consequences) that ADR-015 will follow

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Confirm the assumed next ADR number is still correct (no other branch raced and merged an ADR-015 in the meantime).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T003 Confirm next ADR number is **015** by listing `docs/adr/` and verifying no `ADR-015-*.md` exists. If a different branch took 015, increment to the next free number and propagate the new ID through all subsequent tasks (T004, T005, T006, T007) before continuing.

**Checkpoint**: ADR number reserved. Story work may begin.

---

## Phase 3: User Story 1 — Contributor onboarding (Priority: P1) 🎯 MVP

**Goal**: A new contributor can read the project's onboarding documentation and, within 5 minutes, understand why combined extras don't install in one venv and choose the correct per-extras install for their task.

**Independent Test**: SC-001 from spec — engineer with no prior context selects the right `pip install` invocation in under 5 minutes using only repo content.

### Implementation for User Story 1

- [X] T004 [US1] Create `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`. Required sections (mirror the existing ADR style): a short **Problem** narrative explaining the apache-flink ↔ pyarrow constraint conflict surfaced in SD-026; a **Decision** stating per-service container topology with `[processing,scoring]` and `[analytics]` as mutually exclusive in a single venv; a **Consequences** list covering local-dev install pattern, `uv.lock` advisory status, and the scoring-image `pyarrow` audit-signal exception; a **Revisit** signal tied to "when apache-flink lifts the pyarrow<21 upper bound"; **References** linking issue #31, PR #30 (SD-026), and `SECURITY.md`
- [X] T005 [US1] Update `README.md` Quick Start section (around line 67-71) to replace the single `pip install -e ".[processing,scoring]"` line with two explicit recipes — one for processing/scoring work, one for analytics work — and add a single-sentence note that these extras families are mutually exclusive in a single venv, linking to `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md`. Use the patch shape suggested in `specs/027-flink-pyarrow-conflict/quickstart.md` Step 2.

**Checkpoint**: A contributor opening `README.md` cold can pick the right install command without asking, and the link from README → ADR-015 resolves.

---

## Phase 4: User Story 2 — Security audit signal (Priority: P1)

**Goal**: A security reviewer (human or automated future scanner) finds a co-located, evidence-based justification for `PYSEC-2026-113` on the scoring/processing image and reaches an accept/escalate decision in under 5 minutes.

**Independent Test**: SC-002 from spec — reviewer reads in-repo justification and reaches a justified decision without needing to ask an engineer.

### Implementation for User Story 2

- [X] T006 [US2] Create `SECURITY.md` at the repository root. Required sections: a short "Reporting a vulnerability" header (single sentence pointing to GitHub Security Advisories — boilerplate); a "Known scanner findings & justifications" section containing a `PYSEC-2026-113 / CVE-2026-25087` block with the structured subfields **Affected image**, **Resolved version on image**, **Why not fixed**, **Exposure assessment**, **Revisit signal**, **Last reviewed** (set to 2026-05-27), and **See also** cross-linking ADR-015 + issue #31. Use the content scaffold from `specs/027-flink-pyarrow-conflict/quickstart.md` Step 3.

**Checkpoint**: `SECURITY.md` exists at root, is rendered on the GitHub Security tab, and answers "is `PYSEC-2026-113` exploitable in our scoring runtime?" in plain language.

---

## Phase 5: User Story 3 — `uv.lock` authority status (Priority: P2)

**Goal**: An operator can answer "is `uv.lock` authoritative?" from the file itself or repo docs, without asking another team member.

**Independent Test**: SC-005 from spec — outside reader answers "should I run `uv sync`?" with a single yes/no/explanation from in-repo content.

### Implementation for User Story 3

- [X] T007 [US3] Prepend a 5-line TOML comment block to `uv.lock` marking it **ADVISORY** as of 2026-05-27, citing the apache-flink/pyarrow conflict as the regeneration blocker, noting that CI installs via `pip install -e ".[...]"` (not `uv sync`), and linking to `docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md` for the why. Do NOT modify any other lines of `uv.lock`. Use the scaffold from `specs/027-flink-pyarrow-conflict/quickstart.md` Step 4.

**Checkpoint**: `head -5 uv.lock` shows the advisory comment block; no other lines changed.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify each artifact meets its spec criterion, confirm no CI regression, and ship.

- [X] T008 [P] Verify ADR-015 contains the four required headings (Problem / Decision / Consequences / Revisit): `grep -E '^## (Problem|Decision|Consequences|Revisit)' docs/adr/ADR-015-*.md | wc -l` returns at least 4
- [X] T009 [P] Verify README links to ADR-015: `grep -c 'ADR-015' README.md` returns ≥1 AND `grep -c 'mutually exclusive' README.md` returns ≥1
- [X] T010 [P] Verify SECURITY.md has all PYSEC-2026-113 subsections: `grep -cE 'PYSEC-2026-113|Exposure assessment|Revisit signal|Last reviewed' SECURITY.md` returns ≥4
- [X] T011 [P] Verify `uv.lock` carries the advisory header: `head -5 uv.lock | grep -c 'ADVISORY'` returns ≥1, AND `git diff uv.lock | grep -cE '^[+-]' | head -1` shows only header lines added (no other changes)
- [X] T012 Preservation gate — run `.venv/bin/ruff check .` (clean) and `.venv/bin/python -m pytest tests/unit/ --cov=pipelines --cov-fail-under=20` (passes); documentation-only changes must not regress CI
- [ ] T013 Bundle changes into a single commit. Commit message MUST include the trailer `Closes #31` so the originating GitHub issue auto-closes on PR merge (FR-008, SC-003)
- [ ] T014 Push branch `027-flink-pyarrow-conflict` and open a PR titled `docs: contain apache-flink ↔ pyarrow conflict (#31)`. Wait for the full CI run — all stages must remain green (same set as `main` baseline)
- [ ] T015 After merge — verify issue #31 is in `closed` state (`gh issue view 31 --json state -q '.state'` returns `CLOSED`) and that the four checkboxes in issue #31's Acceptance Criteria are all checked

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — number assignment must be confirmed before naming the file
- **US1 (Phase 3)**: Depends on Phase 2. T005 (README) depends on T004 (ADR file must exist for the link to resolve)
- **US2 (Phase 4)**: Depends on Phase 2 only (independent of US1 — touches a different file)
- **US3 (Phase 5)**: Depends on Phase 2 only (independent of US1/US2 — touches a different file)
- **Polish (Phase 6)**: T008-T011 depend on the corresponding US task completing. T012 (preservation gate) depends on all artifacts being in place. T013 (commit) depends on T008-T012. T014 (push + PR) depends on T013. T015 (issue close) depends on PR merge.

### Within Each User Story

- **US1**: T004 → T005 (README links target file; create file before link)
- **US2**: Single task (T006)
- **US3**: Single task (T007)

### Parallel Opportunities

- **T002 setup** is `[P]` — reading-only, no dependencies
- **T004, T006, T007** can be done in parallel — different files, no inter-dependency (US1 README/T005 depends only on T004 within US1, not on US2/US3)
- **T008, T009, T010, T011** verification checks can all run in parallel — read-only greps against different files

### Same-File Constraint

No two tasks target the same file at the same time. The closest case is T005 (README) and T004 (ADR-015) — different files, but T005 references T004's filename in a hyperlink, so T005 is sequenced after T004.

---

## Parallel Example: Authoring the four documentation artifacts

```bash
# After Phase 2 completes, the three artifact-creation tasks can run in parallel:
Task: "Write docs/adr/ADR-015-PER-EXTRAS-INSTALL-TOPOLOGY.md per quickstart.md Step 1"
Task: "Write SECURITY.md per quickstart.md Step 3"
Task: "Prepend advisory header to uv.lock per quickstart.md Step 4"

# Then sequentially:
Task: "Update README.md per quickstart.md Step 2 (depends on ADR-015 file existing)"
```

After authoring, the four verification tasks run in parallel:

```bash
Task: "Verify ADR-015 headings (T008)"
Task: "Verify README links (T009)"
Task: "Verify SECURITY.md sections (T010)"
Task: "Verify uv.lock header (T011)"
```

---

## Implementation Strategy

### MVP (single PR — recommended)

All three stories ship as one PR. The total change footprint is ~5 files and ~150 lines of Markdown:

1. Phase 1 (Setup) → T001–T002
2. Phase 2 (Foundational) → T003
3. Phase 3 (US1) → T004–T005
4. Phase 4 (US2) → T006
5. Phase 5 (US3) → T007
6. Phase 6 (Polish) → T008–T015
7. Open PR titled `docs: contain apache-flink ↔ pyarrow conflict (#31)`

### Why ship together

- The three stories address one coherent finding (issue #31); bundling matches the user's mental model
- Reviewer overhead is small (Markdown-only diff)
- Cross-references between files (README → ADR-015 → SECURITY.md → uv.lock comment → ADR-015) are easier to land atomically than across multiple PRs
- One PR = one `Closes #31` trailer = clean issue lifecycle

### Why not split

- Splitting into three PRs would mean three reviews for ~50 lines each. The audit cost exceeds the partition value.
- US1 and US3 cross-reference ADR-015 directly; landing them in different PRs creates transient broken links.

---

## Notes

- [P] tasks = different files, no dependencies (or read-only verification)
- This feature touches **NO source code** (zero `pipelines/`, `tests/`, or CI-config edits). The only file-tree change outside `docs/` and `specs/` is the new root `SECURITY.md` and the `uv.lock` header
- Each artifact has a corresponding verification task in Phase 6 — the spec's "5-minute comprehension" gates are operationalised as `grep`-able structural checks
- `Closes #31` in the commit message (T013) is the load-bearing mechanism for SC-003; do not omit
- If T003 finds a race on ADR number, the only downstream impact is renaming T004's file and updating four references (README, SECURITY.md, uv.lock header, issue link); no ordering changes
